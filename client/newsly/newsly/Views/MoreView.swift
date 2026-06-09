//
//  MoreView.swift
//  newsly
//

import SwiftUI

struct MoreView: View {
    @ObservedObject var submissionsViewModel: SubmissionStatusViewModel
    @StateObject private var processingCountService = ProcessingCountService.shared

    var body: some View {
        VStack(spacing: 0) {
            EditorialMastheadHeader(title: "Settings")

            List {
                Section {
                    menuRow(
                        destination: SearchView(),
                        icon: "magnifyingglass",
                        title: "Search"
                    )

                    menuRow(
                        destination: RecentlyReadView(),
                        icon: "clock",
                        title: "Recently Read"
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
                            if processingCountService.processingCount > 0 {
                                CountBadge(count: processingCountService.processingCount, color: .brandPrimary)
                            }
                        }
                        .frame(minHeight: RowMetrics.compactHeight)
                    }
                }

                Section {
                    menuRow(
                        destination: SettingsView(),
                        icon: "gearshape",
                        title: "Settings"
                    )
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .contentMargins(.top, 0, for: .scrollContent)
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("more.screen")
        .task {
            await submissionsViewModel.load()
            await processingCountService.refreshCount()
        }
    }

    private func menuRow<D: View>(destination: D, icon: String, title: String) -> some View {
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
    }

    private func minimalIcon(_ name: String) -> some View {
        Image(systemName: name)
            .font(.appSymbol(size: Spacing.smallIcon, weight: .regular))
            .foregroundStyle(Color.onSurfaceSecondary)
            .frame(width: 24, height: 24)
    }
}

#Preview {
    MoreView(submissionsViewModel: SubmissionStatusViewModel())
}
