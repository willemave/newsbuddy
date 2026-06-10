//
//  LearningDeckListSheet.swift
//  newsly
//

import SwiftUI
import UIKit

private struct LearningDeckBrowserDestination: Identifiable {
    let url: URL

    var id: String { url.absoluteString }
}

private struct LearningDeckNotice: Identifiable {
    let id = UUID()
    let title: String
    let message: String
}

struct LearningDeckListSheet: View {
    @ObservedObject var viewModel: LearningDecksViewModel
    @Binding var isPresented: Bool

    @State private var showCreateSheet = false
    @State private var browserDestination: LearningDeckBrowserDestination?
    @State private var notice: LearningDeckNotice?
    @State private var deckPendingDeletion: LearningDeck?

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 0) {
                    if let errorMessage = viewModel.errorMessage {
                        Text(errorMessage)
                            .font(.terracottaBodySmall)
                            .foregroundStyle(Color.statusDestructive)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                            .padding(.vertical, 14)
                    }

                    if viewModel.isLoading && viewModel.decks.isEmpty {
                        LearningDeckLoadingRow()
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                            .padding(.vertical, 14)
                    } else if viewModel.decks.isEmpty {
                        LearningDeckEmptyRow()
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                            .padding(.vertical, 18)
                    } else {
                        ForEach(viewModel.decks) { deck in
                            LearningDeckRow(
                                deck: deck,
                                isBusy: viewModel.busyDeckIDs.contains(deck.id),
                                open: { Task { await openDeck(deck) } },
                                openNotes: { Task { await openSourceNotes(deck) } },
                                toggleShare: { Task { await toggleShare(deck) } },
                                delete: { deckPendingDeletion = deck }
                            )
                        }
                    }
                }
                .padding(.top, 6)
                .padding(.bottom, 28)
            }
            .background(Color.surfacePrimary)
            .navigationTitle("Learning Decks")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        isPresented = false
                    } label: {
                        Text("Done")
                            .frame(minHeight: 44)
                    }
                }

                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showCreateSheet = true
                    } label: {
                        Image(systemName: "plus")
                            .frame(width: 44, height: 44)
                    }
                    .accessibilityLabel("Create Learning Deck")
                }
            }
            .refreshable {
                await viewModel.load()
            }
            .sheet(isPresented: $showCreateSheet) {
                LearningDeckCreateSheet(
                    sourceTitle: nil,
                    requiresURL: true,
                    isSubmitting: viewModel.isCreating,
                    onCreate: createDeck
                )
            }
            .fullScreenCover(item: $browserDestination) { destination in
                SafariView(url: destination.url)
                    .ignoresSafeArea()
            }
            .alert(item: $notice) { notice in
                Alert(
                    title: Text(notice.title),
                    message: Text(notice.message),
                    dismissButton: .cancel(Text("OK"))
                )
            }
            .alert(
                "Delete Learning Deck?",
                isPresented: deleteConfirmationBinding
            ) {
                Button("Cancel", role: .cancel) {
                    deckPendingDeletion = nil
                }
                Button("Delete", role: .destructive) {
                    guard let deck = deckPendingDeletion else { return }
                    deckPendingDeletion = nil
                    Task { await viewModel.delete(deck) }
                }
            } message: {
                Text("This removes the deck from your library.")
            }
        }
    }

    private var deleteConfirmationBinding: Binding<Bool> {
        Binding(
            get: { deckPendingDeletion != nil },
            set: { isPresented in
                if !isPresented {
                    deckPendingDeletion = nil
                }
            }
        )
    }

    @MainActor
    private func createDeck(url: String?, interestsPrompt: String?) async -> Bool {
        guard let url else {
            notice = LearningDeckNotice(title: "Learning Deck", message: "Add a URL first.")
            return false
        }

        guard let deck = await viewModel.createDeck(
            url: url,
            interestsPrompt: interestsPrompt
        ) else {
            return false
        }

        if deck.viewerAvailable {
            await openDeck(deck)
        } else {
            notice = LearningDeckNotice(
                title: "Learning Deck queued",
                message: deck.latestNote ?? "The deck is being generated."
            )
        }
        return true
    }

    @MainActor
    private func openDeck(_ deck: LearningDeck) async {
        if deck.viewerAvailable, let url = await viewModel.viewerURL(for: deck) {
            browserDestination = LearningDeckBrowserDestination(url: url)
            return
        }

        await viewModel.refresh(deck)
        guard let latest = viewModel.decks.first(where: { $0.id == deck.id }) else {
            return
        }
        if latest.viewerAvailable, let url = await viewModel.viewerURL(for: latest) {
            browserDestination = LearningDeckBrowserDestination(url: url)
        } else {
            notice = LearningDeckNotice(
                title: "Learning Deck",
                message: latest.latestNote ?? "The deck is still being generated."
            )
        }
    }

    @MainActor
    private func openSourceNotes(_ deck: LearningDeck) async {
        guard let url = await viewModel.sourceNotesURL(for: deck) else { return }
        browserDestination = LearningDeckBrowserDestination(url: url)
    }

    @MainActor
    private func toggleShare(_ deck: LearningDeck) async {
        let shareURL = await viewModel.toggleShare(for: deck)
        if let shareURL {
            UIPasteboard.general.string = shareURL
            notice = LearningDeckNotice(title: "Share link copied", message: shareURL)
        } else if deck.shareEnabled {
            notice = LearningDeckNotice(
                title: "Share link disabled",
                message: "This deck is private again."
            )
        }
    }
}
