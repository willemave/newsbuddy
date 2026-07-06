//
//  LearningDeckListSheet.swift
//  newsly
//

import SwiftUI

private struct LearningDeckBrowserDestination: Identifiable {
    let url: URL

    var id: String { url.absoluteString }
}

private struct LearningDeckNotice: Identifiable {
    let id = UUID()
    let title: String
    let message: String
}

private enum LearningDeckListSheetDestination: Identifiable {
    case createDeck
    case share(ShareContent)

    var id: String {
        switch self {
        case .createDeck:
            "createDeck"
        case .share(let content):
            "share.\(content.id.uuidString)"
        }
    }
}

struct LearningDeckListSheet: View {
    @Environment(\.dismiss) private var dismiss

    let viewModel: LearningDecksViewModel

    @State private var activeSheet: LearningDeckListSheetDestination?
    @State private var readerDestination: LearningDeckReaderDestination?
    @State private var browserDestination: LearningDeckBrowserDestination?
    @State private var notice: LearningDeckNotice?
    @State private var deckPendingDeletion: LearningDeck?

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 2) {
                    if let errorMessage = viewModel.errorMessage {
                        errorBanner(errorMessage)
                    }

                    if viewModel.isLoading && viewModel.decks.isEmpty {
                        LearningDeckLoadingRow()
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                            .padding(.vertical, 14)
                    } else if viewModel.decks.isEmpty {
                        LearningDeckEmptyRow(onCreate: presentCreateSheet)
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
                                retry: { Task { await retry(deck) } },
                                delete: { deckPendingDeletion = deck }
                            )
                        }
                    }
                }
                .padding(.top, 8)
                .padding(.bottom, 28)
            }
            .background {
                LinearGradient(
                    colors: [
                        Color.surfacePrimary,
                        Color.surfaceContainer.opacity(0.42),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .ignoresSafeArea()
            }
            .navigationTitle("Learning Decks")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        dismiss()
                    } label: {
                        Text("Done")
                            .frame(minHeight: 44)
                    }
                    .accessibilityIdentifier("learning_deck.list.done")
                }

                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: presentCreateSheet) {
                        Image(systemName: "plus")
                            .frame(width: 44, height: 44)
                    }
                    .accessibilityLabel("Create Learning Deck")
                    .accessibilityIdentifier("learning_deck.list.create")
                }
            }
            .refreshable {
                await viewModel.load()
            }
            .sheet(item: $activeSheet) { destination in
                switch destination {
                case .createDeck:
                    LearningDeckCreateSheet(
                        sourceTitle: nil,
                        requiresURL: true,
                        isSubmitting: viewModel.isCreating,
                        onCreate: createDeck
                    )
                case .share(let content):
                    ShareSheet(content: content)
                }
            }
            .fullScreenCover(item: $browserDestination) { destination in
                SafariView(url: destination.url)
                    .ignoresSafeArea()
            }
            .fullScreenCover(item: $readerDestination) { destination in
                LearningDeckReaderView(
                    deck: destination.deck,
                    viewerURL: destination.url,
                    onClose: {
                        readerDestination = nil
                    }
                )
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
            .withToast()
        }
    }

    private func errorBanner(_ message: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(message)
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.statusDestructive)
                .frame(maxWidth: .infinity, alignment: .leading)

            Button {
                Task { await viewModel.load() }
            } label: {
                Text("Try again")
                    .font(.terracottaBodySmall.weight(.semibold))
                    .foregroundStyle(Color.brandPrimary)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("learning_deck.list.retry")
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 14)
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

    private func presentCreateSheet() {
        activeSheet = .createDeck
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

        await openDeck(deck)
        return true
    }

    @MainActor
    private func openDeck(_ deck: LearningDeck) async {
        if deck.viewerAvailable {
            let url = await viewModel.viewerURL(for: deck)
            readerDestination = LearningDeckReaderDestination(deck: deck, url: url)
        } else {
            // Open the reader anyway; it shows live generation progress and swaps
            // in the deck when ready instead of stranding the user on an alert.
            readerDestination = LearningDeckReaderDestination(deck: deck, url: nil)
        }
    }

    @MainActor
    private func retry(_ deck: LearningDeck) async {
        guard let replacement = await viewModel.regenerate(deck) else { return }
        ToastService.shared.show("Regenerating your deck", type: .info)
        await openDeck(replacement)
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
            activeSheet = .share(
                ShareContent(
                    messageContent: shareURL,
                    articleTitle: deck.displayTitle,
                    articleUrl: nil
                )
            )
        } else if deck.shareEnabled {
            ToastService.shared.show("Deck is private again", type: .success)
        }
    }
}
