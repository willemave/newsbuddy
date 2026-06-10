//
//  ArticleCardView.swift
//  newsly
//
//  Individual card component for the article/podcast card stack.
//

import SwiftUI
import os.log

private let cardLogger = Logger(subsystem: "com.newsly", category: "ArticleCardView")

struct ArticleCardView: View {
    let content: ContentSummary
    let keyPoints: [String]?
    let hook: String?
    let topics: [String]?
    let isLoadingKeyPoints: Bool
    let onSaveToKnowledge: () -> Void
    let onMarkRead: () -> Void
    let onTap: () -> Void
    let onDownloadMore: (Int) -> Void

    var scale: CGFloat = 1.0
    var yOffset: CGFloat = 0
    var cardOpacity: Double = 1.0

    @State private var showDownloadSheet = false

    private let cardCornerRadius: CGFloat = 20
    private let heroImageHeight: CGFloat = 200

    var body: some View {
        Button(action: onTap) {
            cardContent
        }
        .buttonStyle(.plain)
        .scaleEffect(scale)
        .offset(y: yOffset)
        .opacity(cardOpacity)
    }

    private var cardContent: some View {
        VStack(alignment: .leading, spacing: 0) {
            heroImageSection

            VStack(alignment: .leading, spacing: 12) {
                titleSection
                metadataRow
                hookSection
                keyPointsSection
                Spacer(minLength: 8)
                actionButtonsRow
            }
            .padding(20)
        }
        .background(Color.surfaceSecondary)
        .cornerRadius(cardCornerRadius)
        .overlay(
            RoundedRectangle(cornerRadius: cardCornerRadius)
                .stroke(Color.onSurface.opacity(0.12), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.10), radius: 8, x: 0, y: 3)
        .onAppear {
            logPreviewState(context: "appear")
        }
    }

    @ViewBuilder
    private var heroImageSection: some View {
        ZStack(alignment: .topTrailing) {
            if let imageUrlString = content.imageUrl,
               let imageUrl = buildImageURL(from: imageUrlString) {
                // Use progressive loading: thumbnail first, then full image
                let thumbnailUrl = content.thumbnailUrl.flatMap { buildImageURL(from: $0) }
                CachedAsyncImage(
                    url: imageUrl,
                    thumbnailUrl: thumbnailUrl,
                    targetSize: CGSize(width: UIScreen.main.bounds.width, height: heroImageHeight)
                ) { image in
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .frame(height: heroImageHeight)
                        .clipped()
                } placeholder: {
                    placeholderImage
                }
            } else {
                placeholderImage
            }

            contentTypeBadge
                .padding(12)
        }
        .frame(height: heroImageHeight)
        .clipShape(
            UnevenRoundedRectangle(
                topLeadingRadius: cardCornerRadius,
                bottomLeadingRadius: 0,
                bottomTrailingRadius: 0,
                topTrailingRadius: cardCornerRadius
            )
        )
    }

    private var placeholderImage: some View {
        Rectangle()
            .fill(
                LinearGradient(
                    colors: [Color.surfaceTertiary, Color.surfaceContainer],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .frame(height: heroImageHeight)
            .overlay(
                Image(systemName: contentTypeIcon)
                    .font(.appSymbol(size: 36, weight: .light))
                    .foregroundColor(Color.onSurfaceSecondary.opacity(0.4))
            )
    }

    private var contentTypeBadge: some View {
        HStack(spacing: 4) {
            Image(systemName: contentTypeIcon)
                .font(.appCaption)
            Text(content.contentType.capitalized)
                .font(.appCaption)
                .fontWeight(.medium)
        }
        .foregroundColor(.white)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(.ultraThinMaterial)
        .cornerRadius(12)
    }

    private var titleSection: some View {
        Text(content.displayTitle)
            .font(.terracottaHeadlineCompact)
            .fontWeight(.medium)
            .foregroundColor(Color.onSurface)
            .lineLimit(3)
            .multilineTextAlignment(.leading)
    }

    private var metadataRow: some View {
        HStack(spacing: 8) {
            if let source = content.source {
                Text(source)
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .lineLimit(1)
            }

            if content.source != nil {
                Circle()
                    .fill(Color.outlineVariant.opacity(0.5))
                    .frame(width: 4, height: 4)
            }

            Text(content.formattedDate)
                .font(.appSubheadline)
                .foregroundColor(Color.onSurfaceSecondary)

            Spacer()

            if content.isRead {
                Image(systemName: "checkmark.circle.fill")
                    .font(.appCaption)
                    .foregroundColor(.onSurfaceSecondary)
            }
        }
    }

    @ViewBuilder
    private var hookSection: some View {
        if let hookText = hook, !hookText.isEmpty {
            Text(hookText)
                .font(.appSubheadline)
                .italic()
                .foregroundColor(Color.readerBodyText)
                .padding(.vertical, 4)
        }
    }

    @ViewBuilder
    private var topicsSection: some View {
        if let topicsList = topics, !topicsList.isEmpty {
            FlowLayout(spacing: 6) {
                ForEach(topicsList.prefix(5), id: \.self) { topic in
                    Text(topic)
                        .font(.appCaption)
                        .fontWeight(.medium)
                        .foregroundColor(.onSurfaceSecondary)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Color.surfaceTertiary)
                        .cornerRadius(12)
                }
            }
        }
    }

    @ViewBuilder
    private var keyPointsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Key Points")
                .font(.appSubheadline)
                .fontWeight(.semibold)
                .foregroundColor(Color.onSurfaceSecondary)

            if isLoadingKeyPoints {
                skeletonKeyPoints
            } else if let points = keyPoints, !points.isEmpty {
                ForEach(points.prefix(4), id: \.self) { point in
                    keyPointRow(point)
                }
            } else if let summary = content.summaryDisplayText {
                Text(summary)
                    .font(.appSubheadline)
                    .foregroundColor(Color.readerBodyText)
                    .lineLimit(4)
            } else {
                Text("No summary available")
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .italic()
            }
        }
    }

    private func keyPointRow(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(Color.brandPrimary)
                .frame(width: 6, height: 6)
                .padding(.top, 6)

            Text(text)
                .font(.appSubheadline)
                .foregroundColor(Color.readerBodyText)
                .multilineTextAlignment(.leading)
        }
    }

    private var skeletonKeyPoints: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(0..<3, id: \.self) { index in
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.surfaceTertiary)
                    .frame(height: 14)
                    .frame(maxWidth: skeletonWidth(for: index))
            }
        }
    }

    private func skeletonWidth(for index: Int) -> CGFloat {
        switch index {
        case 0: return .infinity
        case 1: return 280
        default: return 200
        }
    }

    private var actionButtonsRow: some View {
        HStack(spacing: 0) {
            // Mark as read
            Button(action: onMarkRead) {
                Image(systemName: content.isRead ? "checkmark.circle.fill" : "checkmark.circle")
                    .font(.appSymbol(size: 20, weight: .regular))
                    .foregroundColor(Color.onSurfaceSecondary)
            }
            .frame(width: 44, height: 44)
            .accessibilityLabel(content.isRead ? "Marked as read" : "Mark as read")

            Spacer()

            // Download more
            Button { showDownloadSheet = true } label: {
                Image(systemName: "tray.and.arrow.down")
                    .font(.appSymbol(size: 20, weight: .regular))
                    .foregroundColor(Color.onSurfaceSecondary)
            }
            .frame(width: 44, height: 44)
            .accessibilityLabel("Download more from this series")

            Spacer()

            // Save to Knowledge
            Button(action: onSaveToKnowledge) {
                KnowledgeSaveIcon(isSaved: content.isSavedToKnowledge)
            }
            .frame(width: 44, height: 44)
            .accessibilityLabel(content.isSavedToKnowledge ? "Remove from Knowledge" : "Save to Knowledge")
        }
        .sheet(isPresented: $showDownloadSheet) {
            downloadSheet
        }
    }

    private var downloadSheet: some View {
        VStack(spacing: 0) {
            // Drag indicator
            Capsule()
                .fill(Color.outlineVariant.opacity(0.4))
                .frame(width: 36, height: 5)
                .padding(.top, 8)

            HStack {
                Text("Download More")
                    .font(.appHeadline)
                Spacer()
                Button { showDownloadSheet = false } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.appTitle3)
                        .foregroundColor(Color.onSurfaceSecondary.opacity(0.6))
                        .frame(width: 44, height: 44)
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.top, 14)
            .padding(.bottom, 10)

            VStack(spacing: 2) {
                downloadSheetRow(count: 3, subtitle: "Quick catch-up")
                downloadSheetRow(count: 5, subtitle: "A good batch")
                downloadSheetRow(count: 10, subtitle: "Deep dive session")
                downloadSheetRow(count: 20, subtitle: "Full backlog")
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
        }
        .presentationDetents([.height(320)])
        .presentationCornerRadius(20)
        .presentationDragIndicator(.hidden)
        .accessibilityIdentifier("article.download.sheet")
    }

    private func downloadSheetRow(count: Int, subtitle: String) -> some View {
        Button {
            showDownloadSheet = false
            onDownloadMore(count)
        } label: {
            HStack(spacing: 14) {
                Image(systemName: "arrow.down.circle.fill")
                    .font(.appTitle3)
                    .foregroundColor(.brandPrimary)
                    .frame(width: 36, height: 36)

                VStack(alignment: .leading, spacing: 2) {
                    Text("\(count) episodes")
                        .font(.appBody)
                        .fontWeight(.medium)
                        .foregroundColor(Color.onSurface)
                    Text(subtitle)
                        .font(.appSubheadline)
                        .foregroundColor(Color.onSurfaceSecondary)
                }

                Spacer()
            }
            .padding(.vertical, 14)
            .padding(.horizontal, 4)
            .frame(minHeight: RowMetrics.compactHeight)
        }
        .buttonStyle(.plain)
    }

    private var contentTypeIcon: String {
        switch content.contentType {
        case "article":
            return "doc.text"
        case "podcast":
            return "headphones"
        default:
            return "newspaper"
        }
    }

    private func buildImageURL(from urlString: String) -> URL? {
        // If it's already a full URL, use it
        if urlString.hasPrefix("http://") || urlString.hasPrefix("https://") {
            return URL(string: urlString)
        }
        // Otherwise, it's a relative path - prepend base URL
        // Use string concatenation instead of appendingPathComponent to preserve path structure
        let baseURL = AppSettings.shared.baseURL
        let fullURL = urlString.hasPrefix("/") ? baseURL + urlString : baseURL + "/" + urlString
        return URL(string: fullURL)
    }

    private func logPreviewState(context: String) {
        let keyPointCount = keyPoints?.count ?? 0
        let hookLength = hook?.count ?? 0
        let topicsCount = topics?.count ?? 0
        let shortSummaryLength = content.shortSummary?.count ?? 0
        cardLogger.info(
            "[ArticleCardView] preview (\(context)) id=\(content.id) type=\(content.contentType, privacy: .public) loading=\(isLoadingKeyPoints) keyPoints=\(keyPointCount) hookLen=\(hookLength) topics=\(topicsCount) shortSummaryLen=\(shortSummaryLength)"
        )
    }
}
