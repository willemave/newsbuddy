//
//  SubmissionsView.swift
//  newsly
//
//  Created by Assistant on 1/15/26.
//

import SwiftUI

struct SubmissionsView: View {
    let viewModel: SubmissionStatusViewModel

    var body: some View {
        Group {
            if viewModel.isLoading && viewModel.submissions.isEmpty {
                LoadingView()
            } else if let error = viewModel.errorMessage, viewModel.submissions.isEmpty {
                ErrorView(message: error) {
                    Task { await viewModel.load() }
                }
            } else if viewModel.submissions.isEmpty {
                emptyStateView
            } else {
                listView
            }
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .toolbarBackground(Color.surfacePrimary, for: .navigationBar)
        .appNavigationTitle("Submissions")
        .onAppear {
            viewModel.markCurrentSubmissionsViewed()
        }
        .onChange(of: viewModel.submissions.first?.createdAt) { _, _ in
            viewModel.markCurrentSubmissionsViewed()
        }
        .task {
            await viewModel.load()
            viewModel.markCurrentSubmissionsViewed()
        }
    }

    private var listView: some View {
        List {
            ForEach(viewModel.submissions) { submission in
                NavigationLink {
                    SubmissionDetailView(submission: submission)
                } label: {
                    SubmissionStatusRow(submission: submission)
                }
                .buttonStyle(.plain)
                .listRowInsets(EdgeInsets(top: 0, leading: 8, bottom: 0, trailing: 12))
                .listRowSeparator(.hidden)
                .listRowBackground(Color.clear)
            }

            if viewModel.isLoadingMore {
                HStack {
                    Spacer()
                    ProgressView()
                        .padding()
                    Spacer()
                }
                .appListRow()
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .contentMargins(.horizontal, 0, for: .scrollContent)
        .onPaginationThresholdReached {
            await viewModel.loadMore()
        }
        .refreshable {
            await viewModel.load()
        }
    }

    private var emptyStateView: some View {
        EmptyStateView(
            icon: "tray",
            title: "No Submissions",
            subtitle: "Submitted URLs will appear here while they're being processed."
        )
    }
}

#Preview {
    NavigationStack {
        SubmissionsView(viewModel: RootDependencyFactory.makeSubmissionStatusViewModel())
    }
}
