//
//  KnowledgeLibraryView.swift
//  newsly
//

import SwiftUI

struct KnowledgeLibraryView: View {
    let showNavigationTitle: Bool

    @StateObject private var viewModel = ContentListViewModel(defaultReadFilter: "all")

    init(showNavigationTitle: Bool = true) {
        self.showNavigationTitle = showNavigationTitle
    }

    var body: some View {
        Group {
            if viewModel.isLoading && viewModel.contents.isEmpty {
                LoadingView()
            } else if let error = viewModel.errorMessage, viewModel.contents.isEmpty {
                ErrorView(message: error) {
                    Task { await viewModel.loadKnowledgeLibrary() }
                }
            } else if viewModel.contents.isEmpty {
                emptyState
            } else {
                contentList
            }
        }
        .background(Color.surfacePrimary)
        .navigationTitle(showNavigationTitle ? "Knowledge Library" : "")
        .task { await viewModel.loadKnowledgeLibrary() }
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: 20) {
            Image(systemName: "books.vertical")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(Color.accentColor.opacity(0.7))

            VStack(spacing: 6) {
                Text("No saved knowledge yet")
                    .font(.listTitle.weight(.semibold))
                    .foregroundStyle(Color.onSurface)

                Text("Save articles or podcasts to Knowledge and they’ll show up here.")
                    .font(.listSubtitle)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 280)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }

    // MARK: - Content List

    private var contentList: some View {
        List {
            ForEach(viewModel.contents) { content in
                NavigationLink(destination: ContentDetailView(
                    contentId: content.id,
                    allContentIds: viewModel.contents.map(\.id)
                )) {
                    KnowledgeLibraryRow(content: content)
                }
                .appListRow()
                .swipeActions(edge: .leading, allowsFullSwipe: true) {
                    if !content.isRead {
                        Button {
                            Task { await viewModel.markAsRead(content.id) }
                        } label: {
                            Label("Mark as Read", systemImage: "checkmark.circle.fill")
                        }
                        .tint(.green)
                    }
                }
                .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                    Button {
                        Task {
                            await viewModel.toggleKnowledgeSave(content.id)
                            withAnimation(.easeOut(duration: 0.3)) {
                                viewModel.contents.removeAll { $0.id == content.id }
                            }
                        }
                    } label: {
                        Label("Remove", systemImage: "books.vertical.fill")
                    }
                    .tint(.red)
                }
                .onAppear {
                    if content.id == viewModel.contents.last?.id {
                        Task { await viewModel.loadMoreContent() }
                    }
                }
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
        .refreshable { await viewModel.loadKnowledgeLibrary() }
    }
}

// MARK: - Knowledge Library Row

private struct KnowledgeLibraryRow: View {
    let content: ContentSummary

    private var textOpacity: Double {
        content.isRead ? 0.6 : 1.0
    }

    var body: some View {
        HStack(spacing: 12) {
            thumbnailView

            VStack(alignment: .leading, spacing: 4) {
                Text(content.displayTitle)
                    .font(.listTitle)
                    .foregroundStyle(Color.onSurface.opacity(textOpacity))
                    .lineLimit(2)

                HStack(spacing: 6) {
                    if let source = content.source {
                        Text(source)
                            .font(.listCaption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineLimit(1)
                    }

                    if let date = content.processedDateDisplay {
                        Text("·")
                            .font(.listCaption)
                            .foregroundStyle(Color.onSurfaceSecondary)

                        Text(date)
                            .font(.listCaption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                }
            }

            Spacer(minLength: 8)
        }
        .appRow(.regular)
    }

    // MARK: - Thumbnail

    @ViewBuilder
    private var thumbnailView: some View {
        let displayUrl = content.thumbnailUrl ?? content.imageUrl
        if let imageUrlString = displayUrl,
           let imageUrl = buildImageURL(from: imageUrlString) {
            CachedAsyncImage(url: imageUrl) { image in
                image
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: RowMetrics.thumbnailSize, height: RowMetrics.thumbnailSize)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            } placeholder: {
                thumbnailPlaceholder
            }
        } else {
            thumbnailPlaceholder
        }
    }

    private var thumbnailPlaceholder: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(Color.surfaceSecondary)
            .frame(width: RowMetrics.thumbnailSize, height: RowMetrics.thumbnailSize)
            .overlay(
                Image(systemName: contentTypeIcon)
                    .font(.system(size: 20))
                    .foregroundStyle(Color.onSurfaceSecondary)
            )
    }

    private var contentTypeIcon: String {
        switch content.contentTypeEnum {
        case .article: return "doc.text"
        case .podcast: return "headphones"
        case .news: return "newspaper"
        default: return "doc"
        }
    }

    private func buildImageURL(from urlString: String) -> URL? {
        if urlString.hasPrefix("http://") || urlString.hasPrefix("https://") {
            return URL(string: urlString)
        }
        let baseURL = AppSettings.shared.baseURL
        let fullURL = urlString.hasPrefix("/") ? baseURL + urlString : baseURL + "/" + urlString
        return URL(string: fullURL)
    }
}
