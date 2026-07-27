//
//  MoreView.swift
//  newsly
//

import SwiftUI

struct MoreView: View {
    @Environment(\.dismiss) private var dismiss

    let submissionsViewModel: SubmissionStatusViewModel
    let readStateCache: ReadStateCache
    let showsDismissButton: Bool
    @State private var badgeStatsStore = BadgeStatsStore.shared

    init(
        submissionsViewModel: SubmissionStatusViewModel,
        readStateCache: ReadStateCache? = nil,
        showsDismissButton: Bool = false
    ) {
        let readStateCache = readStateCache ?? ReadStateCache()
        self.submissionsViewModel = submissionsViewModel
        self.readStateCache = readStateCache
        self.showsDismissButton = showsDismissButton
    }

    var body: some View {
        VStack(spacing: 0) {
            EditorialMastheadHeader(
                title: "More",
                showsDate: false,
                trailingAccessory: showsDismissButton ? AnyView(dismissButton) : nil
            )

            List {
                Section {
                    menuRow(
                        destination: SearchView(
                            readStateCache: readStateCache,
                            viewModel: RootDependencyFactory.makeSearchViewModel()
                        ),
                        icon: "magnifyingglass",
                        title: "Search",
                        accessibilityIdentifier: "more.search"
                    )

                    menuRow(
                        destination: RecentlyReadView(readStateCache: readStateCache),
                        icon: "clock",
                        title: "Recently Read",
                        accessibilityIdentifier: "more.recently_read"
                    )

                    NavigationLink {
                        SubmissionsView(viewModel: submissionsViewModel)
                    } label: {
                        HStack(spacing: 16) {
                            minimalIcon("tray.and.arrow.up")
                            Text("Submissions")
                                .foregroundStyle(Color.onSurface)
                            Spacer()
                            if submissionsViewModel.unseenCount > 0 {
                                CountBadge(count: submissionsViewModel.unseenCount, color: .brandPrimary)
                            }
                        }
                        .frame(minHeight: RowMetrics.compactHeight)
                    }

                    NavigationLink {
                        ProcessingStatsView()
                    } label: {
                        HStack(spacing: 16) {
                            minimalIcon("clock.arrow.circlepath")
                            Text("Processing")
                                .foregroundStyle(Color.onSurface)
                            Spacer()
                            if badgeStatsStore.processingCount > 0 {
                                CountBadge(count: badgeStatsStore.processingCount, color: .brandPrimary)
                            }
                        }
                        .frame(minHeight: RowMetrics.compactHeight)
                    }
                }

                Section {
                    menuRow(
                        destination: SettingsView(),
                        icon: "gearshape",
                        title: "Settings",
                        accessibilityIdentifier: "more.settings"
                    )
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .contentMargins(.top, 0, for: .scrollContent)
            .contentMargins(.horizontal, Spacing.appHorizontalMargin, for: .scrollContent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("more.screen")
        .task {
            await submissionsViewModel.load()
            await badgeStatsStore.refreshStats()
        }
    }

    private var dismissButton: some View {
        Button {
            dismiss()
        } label: {
            Image(systemName: "xmark")
                .font(.appSymbol(size: 16, weight: .semibold))
                .foregroundStyle(Color.onSurfaceSecondary)
                .frame(width: 44, height: 44)
                .background(Color.surfaceTertiary)
                .clipShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Close More")
        .accessibilityIdentifier("more.close")
    }

    private func menuRow<D: View>(
        destination: D,
        icon: String,
        title: String,
        accessibilityIdentifier: String
    ) -> some View {
        NavigationLink {
            destination
        } label: {
            HStack(spacing: 16) {
                minimalIcon(icon)
                Text(title)
                    .foregroundStyle(Color.onSurface)
            }
            .frame(minHeight: RowMetrics.compactHeight)
        }
        .accessibilityIdentifier(accessibilityIdentifier)
    }

    private func minimalIcon(_ name: String) -> some View {
        Image(systemName: name)
            .font(.appSymbol(size: Spacing.smallIcon, weight: .regular))
            .foregroundStyle(Color.onSurfaceSecondary)
            .frame(width: 24, height: 24)
    }
}

#Preview {
    MoreView(submissionsViewModel: RootDependencyFactory.makeSubmissionStatusViewModel())
}
