//
//  ContentDetailViewModel.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation
import SwiftUI
import UniformTypeIdentifiers
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ContentDetail")

enum ShareContentOption {
    case light
    case medium
    case full
}

enum LinkReadLaterState: Equatable {
    case idle
    case adding
    case added
    case failed
}

typealias DiscussionLinkAddState = LinkReadLaterState

@MainActor
class ContentDetailViewModel: ObservableObject {
    @Published var content: ContentDetail?
    @Published var contentBody: ContentBody?
    @Published var readerBody: ContentBody?
    @Published var isLoadingReaderBody = false
    @Published var readerErrorMessage: String?
    @Published var isLoading = false
    @Published var errorMessage: String?
    // Indicates if the item was already marked as read when it was fetched
    @Published var wasAlreadyReadWhenLoaded: Bool = false

    // Feed subscription state
    @Published var isSubscribingToFeed = false
    @Published var feedSubscriptionSuccess = false
    @Published var feedSubscriptionError: String?
    @Published private var readLaterLinkStates: [String: LinkReadLaterState] = [:]

    private let contentService = ContentService.shared
    private let unreadCountService = UnreadCountService.shared
    private let scraperConfigService = ScraperConfigService.shared
    private let submitLinkToLongFormHandler: (URL, String?) async throws -> SubmitContentResponse
    private var contentId: Int = 0
    private var contentType: APIContentType?
    
    init(
        contentId: Int = 0,
        contentType: APIContentType? = nil,
        submitLinkToLongFormHandler: @escaping (URL, String?) async throws -> SubmitContentResponse = {
            url,
            title in
            try await ContentService.shared.submitContent(url: url, title: title)
        }
    ) {
        self.contentId = contentId
        self.contentType = contentType
        self.submitLinkToLongFormHandler = submitLinkToLongFormHandler
    }
    
    func updateContentId(_ newId: Int, contentType newContentType: APIContentType? = nil) {
        self.contentId = newId
        if let newContentType {
            self.contentType = newContentType
        }
        // Clear previous content to show loading state
        self.content = nil
        self.contentBody = nil
        self.readerBody = nil
        self.isLoadingReaderBody = false
        self.readerErrorMessage = nil
        self.errorMessage = nil
        self.isLoading = true
        readLaterLinkStates = [:]
    }
    
    func loadContent() async {
        let requestedContentId = contentId
        let requestedContentType = contentType
        logger.info("[ContentDetail] loadContent started | contentId=\(requestedContentId)")
        isLoading = true
        errorMessage = nil
        contentBody = nil

        do {
            logger.debug("[ContentDetail] Fetching content detail | contentId=\(requestedContentId) contentType=\(requestedContentType?.rawValue ?? "nil", privacy: .public)")
            let fetched: ContentDetail
            if requestedContentType == .news {
                fetched = try await contentService.fetchNewsItemDetail(id: requestedContentId)
            } else {
                fetched = try await contentService.fetchContentDetail(id: requestedContentId)
            }

            guard contentId == requestedContentId,
                  contentType == requestedContentType else {
                logger.debug(
                    "[ContentDetail] Ignoring stale content detail | requestedId=\(requestedContentId) currentId=\(self.contentId)"
                )
                return
            }

            content = fetched
            logger.info("[ContentDetail] Content fetched | contentId=\(requestedContentId) type=\(fetched.contentType.rawValue, privacy: .public) isRead=\(fetched.isRead) title=\(fetched.displayTitle, privacy: .public)")

            // Capture read state as returned by the server BEFORE any auto-marking
            wasAlreadyReadWhenLoaded = fetched.isRead
            logger.debug("[ContentDetail] wasAlreadyReadWhenLoaded=\(fetched.isRead) | contentId=\(requestedContentId)")

            // Render immediately once the main detail payload arrives.
            isLoading = false

            Task {
                await self.trackOpenedInteraction(for: fetched)
            }

            if fetched.bodyAvailable && fetched.contentType != .news {
                Task {
                    await self.loadContentBody(for: fetched)
                }
            }

            Task {
                await self.markFetchedContentAsReadIfNeeded(fetched)
            }
        } catch where isNetworkCancellation(error) {
            guard contentId == requestedContentId,
                  contentType == requestedContentType else { return }
            isLoading = false
        } catch {
            guard contentId == requestedContentId,
                  contentType == requestedContentType else { return }
            logger.error("[ContentDetail] Error loading content | contentId=\(requestedContentId) error=\(error.localizedDescription)")
            errorMessage = error.localizedDescription
            isLoading = false
        }
        logger.debug("[ContentDetail] loadContent completed | contentId=\(requestedContentId)")
    }

    func canShowReader(for content: ContentDetail) -> Bool {
        guard content.bodyAvailable else { return false }
        return content.contentType == .article || content.contentType == .news
    }

    func loadReaderBody(for content: ContentDetail, force: Bool = false) async {
        guard canShowReader(for: content) else { return }
        guard force || readerBody == nil else { return }
        guard !isLoadingReaderBody else { return }

        isLoadingReaderBody = true
        readerErrorMessage = nil
        defer { isLoadingReaderBody = false }

        do {
            let body = try await fetchReaderBody(for: content)
            guard self.contentId == content.id,
                  self.content?.id == content.id,
                  self.content?.contentType == content.contentType else {
                logger.debug("[ContentDetail] Ignoring stale reader body | requestedId=\(content.id) currentId=\(self.contentId)")
                return
            }
            readerBody = body
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            guard self.contentId == content.id else { return }
            logger.error("[ContentDetail] Failed to fetch reader body | contentId=\(content.id) error=\(error.localizedDescription)")
            readerErrorMessage = error.localizedDescription
        }
    }

    private func loadContentBody(for fetched: ContentDetail) async {
        do {
            let body = try await contentService.fetchContentBody(
                id: fetched.id,
                contentType: fetched.contentType
            )
            guard self.contentId == fetched.id else {
                logger.debug("[ContentDetail] Ignoring stale content body | requestedId=\(fetched.id) currentId=\(self.contentId)")
                return
            }
            contentBody = body
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            logger.error("[ContentDetail] Failed to fetch content body | contentId=\(fetched.id) error=\(error.localizedDescription)")
        }
    }

    private func fetchReaderBody(for content: ContentDetail) async throws -> ContentBody {
        do {
            let renderedBody = try await contentService.fetchContentBody(
                id: content.id,
                variant: "rendered",
                contentType: content.contentType
            )
            if !renderedBody.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return renderedBody
            }
        } catch where isNetworkCancellation(error) {
            throw error
        } catch {
            logger.debug("[ContentDetail] Rendered reader body unavailable, falling back to source | contentId=\(content.id) error=\(error.localizedDescription)")
        }

        return try await contentService.fetchContentBody(
            id: content.id,
            variant: "source",
            contentType: content.contentType
        )
    }

    private func markFetchedContentAsReadIfNeeded(_ fetched: ContentDetail) async {
        guard !fetched.isRead else {
            logger.info("[ContentDetail] Content already read, skipping mark-as-read | contentId=\(fetched.id)")
            return
        }

        // Only issue the server-side mark-as-read for the item that is still
        // current. During fast cascade navigation this task can run after the
        // user has already moved on, and the network call fires before the
        // post-await guard below — without this check, transited (unread) items
        // get marked read server-side and their counts decremented.
        guard self.contentId == fetched.id else {
            logger.debug("[ContentDetail] Skipping mark-as-read for transited item | requestedId=\(fetched.id) currentId=\(self.contentId)")
            return
        }

        do {
            logger.info("[ContentDetail] Content not read, marking as read | contentId=\(fetched.id) type=\(fetched.contentType.rawValue, privacy: .public)")
            try await contentService.markContentAsRead(id: fetched.id, contentType: fetched.contentType)
            logger.info("[ContentDetail] Successfully marked as read | contentId=\(fetched.id)")

            guard self.contentId == fetched.id else {
                logger.debug("[ContentDetail] Ignoring stale mark-as-read completion | requestedId=\(fetched.id) currentId=\(self.contentId)")
                return
            }

            content?.isRead = true

            logger.debug("[ContentDetail] Posting contentMarkedAsRead notification | contentId=\(fetched.id) type=\(fetched.contentType.rawValue, privacy: .public)")
            NotificationCenter.default.post(
                name: .contentMarkedAsRead,
                object: nil,
                userInfo: ["contentId": fetched.id, "contentType": fetched.contentType]
            )

            if fetched.contentType == .article {
                logger.debug("[ContentDetail] Decrementing article count | contentId=\(fetched.id)")
                unreadCountService.decrementArticleCount()
            } else if fetched.contentType == .podcast {
                logger.debug("[ContentDetail] Decrementing podcast count | contentId=\(fetched.id)")
                unreadCountService.decrementPodcastCount()
            } else if fetched.contentType == .news {
                logger.debug("[ContentDetail] Decrementing news count | contentId=\(fetched.id)")
                unreadCountService.decrementNewsCount()
            }
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            logger.error("[ContentDetail] Failed to mark content as read | contentId=\(fetched.id) error=\(error.localizedDescription)")
        }
    }

    private func trackOpenedInteraction(for fetched: ContentDetail) async {
        let contextData: [String: Any] = [
            "content_type": fetched.contentType.rawValue,
            "was_read_when_loaded": fetched.isRead,
        ]

        do {
            let response = try await contentService.trackContentOpened(
                contentId: fetched.id,
                contextData: contextData
            )
            logger.debug(
                "[ContentDetail] Open interaction tracked | contentId=\(fetched.id) recorded=\(response.recorded)"
            )
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            logger.error(
                "[ContentDetail] Failed to track open interaction | contentId=\(fetched.id) error=\(error.localizedDescription)"
            )
        }
    }
    
    func shareContent(option: ShareContentOption) {
        let items = buildShareItems(option: option)
        guard !items.isEmpty else { return }

        let activityVC = UIActivityViewController(activityItems: items, applicationActivities: nil)
        presentActivityViewControllerWhenReady(activityVC)
    }

    func markdownForShare(option: ShareContentOption) -> String? {
        switch option {
        case .light:
            return nil
        case .medium:
            return buildMediumMarkdown()
        case .full:
            return buildFullMarkdown() ?? buildMediumMarkdown()
        }
    }
    
    func toggleKnowledgeSave() async {
        guard let currentContent = content else { return }

        do {
            let targetSavedState = !currentContent.isSavedToKnowledge
            content?.isSavedToKnowledge = targetSavedState
            if targetSavedState {
                let response = try await contentService.saveToKnowledge(id: currentContent.id)
                content?.isSavedToKnowledge = response.isSavedToKnowledge
            } else {
                let response = try await contentService.removeFromKnowledge(id: currentContent.id)
                content?.isSavedToKnowledge = response.isSavedToKnowledge
            }
        } catch {
            content?.isSavedToKnowledge = currentContent.isSavedToKnowledge
            errorMessage = "Failed to update knowledge save"
        }
    }

    func saveLinkedArticleAsKnowledge() async {
        guard let currentContent = content, currentContent.contentType == .news else {
            return
        }

        do {
            let response: ConvertNewsResponse
            if contentType == .news {
                response = try await contentService.convertNewsItemToArticle(id: currentContent.id)
            } else {
                response = try await contentService.convertNewsToArticle(id: currentContent.id)
            }

            if response.alreadyExists {
                ToastService.shared.show("Article already saved to Knowledge", type: .info)
            } else {
                ToastService.shared.showSuccess("Saved linked article to Knowledge")
            }
        } catch {
            ToastService.shared.showError("Failed to save linked article: \(error.localizedDescription)")
        }
    }

    func discussionLinkAddState(for linkID: String) -> DiscussionLinkAddState {
        readLaterLinkStates[linkID] ?? .idle
    }

    func relevantLinkReadLaterState(for linkID: String) -> LinkReadLaterState {
        readLaterLinkStates[linkID] ?? .idle
    }

    func addRelevantLinkToReadLater(_ link: RelevantLink) async {
        guard let url = URL(string: link.url) else {
            ToastService.shared.showError("Invalid link URL")
            return
        }

        await addLinkToReadLater(
            id: link.id,
            url: url,
            title: link.title,
            alreadyExistsMessage: "Already in Read Later",
            successMessage: "Added to Read Later",
            errorPrefix: "Failed to add to Read Later"
        )
    }

    func addDiscussionLinkToLongForm(_ link: DiscussionLink) async {
        guard let url = URL(string: link.url) else {
            ToastService.shared.showError("Invalid link URL")
            return
        }

        await addLinkToReadLater(
            id: link.id,
            url: url,
            title: link.title,
            alreadyExistsMessage: "Already in Long Form",
            successMessage: "Added to Long Form",
            errorPrefix: "Failed to add to Long Form"
        )
    }

    private func addLinkToReadLater(
        id linkID: String,
        url: URL,
        title: String?,
        alreadyExistsMessage: String,
        successMessage: String,
        errorPrefix: String
    ) async {
        let state = readLaterLinkStates[linkID] ?? .idle
        guard state != .adding && state != .added else {
            return
        }

        readLaterLinkStates[linkID] = .adding

        do {
            let response = try await submitLinkToLongFormHandler(url, title)
            readLaterLinkStates[linkID] = .added
            if response.alreadyExists {
                ToastService.shared.show(alreadyExistsMessage, type: .info)
            } else {
                ToastService.shared.showSuccess(successMessage)
            }
        } catch {
            readLaterLinkStates[linkID] = .failed
            ToastService.shared.showError("\(errorPrefix): \(error.localizedDescription)")
        }
    }

    /// Subscribe to the detected feed for this content.
    func subscribeToDetectedFeed() async {
        guard let feed = content?.detectedFeed else {
            feedSubscriptionError = "No feed detected"
            return
        }

        isSubscribingToFeed = true
        feedSubscriptionError = nil

        do {
            _ = try await scraperConfigService.subscribeFeed(
                feedURL: feed.url,
                feedType: feed.type,
                displayName: feed.title
            )
            feedSubscriptionSuccess = true
            logger.info("[ContentDetail] Successfully subscribed to feed | url=\(feed.url, privacy: .public) type=\(feed.type, privacy: .public)")
        } catch {
            feedSubscriptionError = error.localizedDescription
            logger.error("[ContentDetail] Failed to subscribe to feed | error=\(error.localizedDescription)")
        }

        isSubscribingToFeed = false
    }

    func downloadMoreFromSeries(count: Int) async {
        guard let contentId = content?.id else { return }

        do {
            let response = try await contentService.downloadMoreFromSeries(
                contentId: contentId,
                count: count
            )
            let savedCount = response.saved
            if savedCount > 0 {
                ToastService.shared.showSuccess("Added \(savedCount) new items")
            } else {
                ToastService.shared.show("Download started", type: .info)
            }
        } catch {
            ToastService.shared.showError("Failed to download more: \(error.localizedDescription)")
        }
    }

    private func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func uniqueNonEmpty(_ values: [String]) -> [String] {
        var seen: Set<String> = []
        var result: [String] = []

        for value in values {
            guard let normalized = normalizedText(value) else { continue }
            let key = normalized.lowercased()
            if seen.insert(key).inserted {
                result.append(normalized)
            }
        }

        return result
    }

    private func resolvedShareURLString(for content: ContentDetail) -> String? {
        if content.contentType == .news,
           let articleURL = content.resolvedNewsArticleURL {
            return articleURL
        }
        return normalizedText(content.url)
    }

    private func resolvedOverviewText(for content: ContentDetail) -> String? {
        if let overview = normalizedText(content.structuredSummary?.overview) {
            return overview
        }
        if let hook = normalizedText(content.interleavedSummaryV2?.hook) {
            return hook
        }
        if let hook = normalizedText(content.interleavedSummary?.hook) {
            return hook
        }
        if let editorialSummary = content.editorialSummary,
           let firstParagraph = editorialSummary.narrativeParagraphs.first,
           let narrative = normalizedText(firstParagraph) {
            return narrative
        }
        if content.contentType == .news {
            return nil
        }
        if let newsSummary = content.resolvedNewsSummaryText {
            return newsSummary
        }
        return nil
    }

    private func resolvedKeyPointTexts(for content: ContentDetail) -> [String] {
        var points: [String] = []

        if let structuredSummary = content.structuredSummary {
            points.append(contentsOf: structuredSummary.bulletPoints.map(\.text))
        }
        if let interleavedSummaryV2 = content.interleavedSummaryV2 {
            points.append(contentsOf: interleavedSummaryV2.keyPoints.map(\.text))
        }
        if let interleavedSummary = content.interleavedSummary {
            points.append(contentsOf: interleavedSummary.insights.map(\.insight))
        }
        if let bulletedSummary = content.bulletedSummary {
            points.append(contentsOf: bulletedSummary.points.map(\.text))
        }
        if let editorialSummary = content.editorialSummary {
            points.append(contentsOf: editorialSummary.keyPoints.map(\.point))
        }
        points.append(contentsOf: content.bulletPoints.map(\.text))

        if content.contentType == .news {
            points.append(contentsOf: content.resolvedNewsKeyPoints)
        }

        if points.isEmpty {
            if let summary = resolvedOverviewText(for: content) {
                points = [summary]
            }
        }

        return uniqueNonEmpty(points)
    }

    private func resolvedQuoteTexts(for content: ContentDetail) -> [String] {
        var quotes: [String] = []

        if let structuredSummary = content.structuredSummary {
            quotes.append(contentsOf: structuredSummary.quotes.map(\.text))
        }
        if let interleavedSummaryV2 = content.interleavedSummaryV2 {
            quotes.append(contentsOf: interleavedSummaryV2.quotes.map(\.text))
        }
        if let interleavedSummary = content.interleavedSummary {
            quotes.append(
                contentsOf: interleavedSummary.insights.compactMap { insight in
                    normalizedText(insight.supportingQuote)
                }
            )
        }
        if let bulletedSummary = content.bulletedSummary {
            for point in bulletedSummary.points {
                quotes.append(contentsOf: point.quotes.map(\.text))
            }
        }
        if let editorialSummary = content.editorialSummary {
            quotes.append(contentsOf: editorialSummary.quotes.map(\.text))
        }
        quotes.append(contentsOf: content.quotes.map(\.text))

        return uniqueNonEmpty(quotes)
    }

    private func markdownHeading(_ title: String, level: Int) -> String {
        let clampedLevel = min(max(level, 1), 6)
        return String(repeating: "#", count: clampedLevel) + " " + title
    }

    private func buildLongformArtifactSummaryMarkdown(
        _ artifact: LongformArtifactEnvelope,
        headingLevel: Int
    ) -> String? {
        let payload = artifact.artifact.payload
        var sections: [String] = []

        if let summaryText = normalizedText(payload.overview) ?? normalizedText(artifact.oneLine) {
            sections.append("\(markdownHeading("Summary", level: headingLevel))\n\(summaryText)")
        }

        if let takeaway = normalizedText(payload.takeaway) {
            sections.append("\(markdownHeading("Takeaway", level: headingLevel))\n\(takeaway)")
        }

        let keyPointLines = payload.keyPoints.compactMap { point -> String? in
            guard let heading = normalizedText(point.heading),
                  let content = normalizedText(point.content) else {
                return nil
            }
            return "- \(heading): \(content)"
        }
        if !keyPointLines.isEmpty {
            sections.append(
                "\(markdownHeading("Key Points", level: headingLevel))\n"
                + keyPointLines.joined(separator: "\n")
            )
        }

        let quoteBlocks = payload.quotes.compactMap { quote -> String? in
            guard let text = normalizedText(quote.text) else { return nil }
            var block = "> \(text)"
            if let attribution = normalizedText(quote.attribution) {
                block += "\n> - \(attribution)"
            }
            return block
        }
        if !quoteBlocks.isEmpty {
            sections.append(
                "\(markdownHeading("Source Quotes", level: headingLevel))\n"
                + quoteBlocks.joined(separator: "\n\n")
            )
        }

        let extraSections = LongformExtrasSection.orderedSections(from: payload.extrasRaw)
        let extraHeadingLevel = min(headingLevel + 1, 6)
        let extraBlocks = extraSections.compactMap { section -> String? in
            let items = uniqueNonEmpty(section.items)
            guard !items.isEmpty else { return nil }
            let bullets = items.map { "- \($0)" }.joined(separator: "\n")
            return "\(markdownHeading(section.title, level: extraHeadingLevel))\n\(bullets)"
        }
        if !extraBlocks.isEmpty {
            sections.append(
                "\(markdownHeading("Extra", level: headingLevel))\n\n"
                + extraBlocks.joined(separator: "\n\n")
            )
        }

        return sections.isEmpty ? nil : sections.joined(separator: "\n\n")
    }

    private func buildFullMarkdown() -> String? {
        guard let content = content else { return nil }

        var fullText = "# \(content.displayTitle)\n\n"

        // Add metadata
        if let source = content.source { fullText += "Source: \(source)\n" }
        if let pubDate = content.publicationDate { fullText += "Published: \(pubDate)\n" }
        if let shareURL = resolvedShareURLString(for: content) {
            fullText += "URL: \(shareURL)\n"
        }
        fullText += "\n---\n\n"

        let overview = resolvedOverviewText(for: content)
        let keyPoints = resolvedKeyPointTexts(for: content)
        let quotes = resolvedQuoteTexts(for: content)
        let hasTemplateSummaryData =
            overview != nil
            || !keyPoints.isEmpty
            || !quotes.isEmpty
            || content.bulletedSummary != nil
            || content.interleavedSummary != nil
            || content.interleavedSummaryV2 != nil
            || content.editorialSummary != nil

        let longformArtifactSummaryMarkdown = content.longformArtifact.flatMap {
            buildLongformArtifactSummaryMarkdown($0, headingLevel: 3)
        }

        if let longformArtifactSummaryMarkdown {
            fullText += "## Summary\n\n"
            fullText += longformArtifactSummaryMarkdown
            fullText += "\n\n---\n\n"
        } else if hasTemplateSummaryData {
            fullText += "## Summary\n\n"

            if let overview {
                fullText += "### Overview\n\(overview)\n\n"
            }

            if let editorialSummary = content.editorialSummary,
               !editorialSummary.narrativeParagraphs.isEmpty {
                fullText += "### Narrative\n"
                fullText += editorialSummary.narrativeParagraphs.joined(separator: "\n\n")
                fullText += "\n\n"
            }

            if !keyPoints.isEmpty {
                fullText += "### Key Points\n"
                fullText += keyPoints.map { "- \($0)" }.joined(separator: "\n")
                fullText += "\n\n"
            }

            if let interleavedSummaryV2 = content.interleavedSummaryV2,
               !interleavedSummaryV2.topics.isEmpty {
                let topicBlocks = interleavedSummaryV2.topics.compactMap { topic -> String? in
                    let bullets = topic.bullets
                        .compactMap { normalizedText($0.text) }
                        .map { "  - \($0)" }
                        .joined(separator: "\n")
                    guard !bullets.isEmpty else { return nil }
                    return "- \(topic.topic)\n\(bullets)"
                }
                if !topicBlocks.isEmpty {
                    fullText += "### Topic Breakdown\n"
                    fullText += topicBlocks.joined(separator: "\n")
                    fullText += "\n\n"
                }
            }

            if let interleavedSummary = content.interleavedSummary,
               !interleavedSummary.insights.isEmpty {
                let insightLines = interleavedSummary.insights.compactMap { insight -> String? in
                    guard let text = normalizedText(insight.insight) else { return nil }
                    if let topic = normalizedText(insight.topic) {
                        return "- \(topic): \(text)"
                    }
                    return "- \(text)"
                }
                if !insightLines.isEmpty {
                    fullText += "### Insights\n"
                    fullText += insightLines.joined(separator: "\n")
                    fullText += "\n\n"
                }
            }

            if let bulletedSummary = content.bulletedSummary, !bulletedSummary.points.isEmpty {
                let pointDetails = bulletedSummary.points.compactMap { point -> String? in
                    guard let text = normalizedText(point.text),
                          let detail = normalizedText(point.detail) else {
                        return nil
                    }
                    var block = "- \(text)\n  \(detail)"
                    if let quote = point.quotes.first,
                       let quoteText = normalizedText(quote.text) {
                        block += "\n  > \(quoteText)"
                    }
                    return block
                }
                if !pointDetails.isEmpty {
                    fullText += "### Point Details\n"
                    fullText += pointDetails.joined(separator: "\n")
                    fullText += "\n\n"
                }
            }

            if !quotes.isEmpty {
                fullText += "### Notable Quotes\n"
                fullText += quotes.map { "> \($0)" }.joined(separator: "\n")
                fullText += "\n\n"
            }

            fullText += "---\n\n"
        }

        // Full content / transcript
        if let contentBody {
            fullText += content.contentType == .podcast ? "## Full Transcript\n\n" : "## Full Article\n\n"
            fullText += contentBody.text
        } else if content.contentType == .podcast, let podcastMetadata = content.podcastMetadata, let transcript = podcastMetadata.transcript {
            fullText += "## Full Transcript\n\n" + transcript
        } else if let fullMarkdown = content.fullMarkdown {
            fullText += (content.contentType == .podcast ? "## Transcript\n\n" : "## Full Article\n\n")
            fullText += fullMarkdown
        }
        return fullText
    }

    private func buildMediumMarkdown() -> String? {
        guard let content = content else { return nil }

        var sections: [String] = []
        sections.append("# \(content.displayTitle)")

        let longformArtifactSummaryMarkdown = content.longformArtifact.flatMap {
            buildLongformArtifactSummaryMarkdown($0, headingLevel: 2)
        }

        if let longformArtifactSummaryMarkdown {
            sections.append(longformArtifactSummaryMarkdown)
        } else {
            if let overview = resolvedOverviewText(for: content) {
                sections.append("## Summary\n\(overview)")
            }

            let keyPoints = resolvedKeyPointTexts(for: content)
            if !keyPoints.isEmpty {
                let bullets = keyPoints.map { "- \($0)" }.joined(separator: "\n")
                sections.append("## Key Points\n\(bullets)")
            }

            let quotes = resolvedQuoteTexts(for: content)
            if !quotes.isEmpty {
                let quoteText = quotes.map { "> \($0)" }.joined(separator: "\n")
                sections.append("## Quotes\n\(quoteText)")
            }
        }

        if let shareURL = resolvedShareURLString(for: content) {
            sections.append("Link: \(shareURL)")
        }

        guard sections.count > 1 else { return nil }
        return sections.joined(separator: "\n\n")
    }

    private func buildShareItems(option: ShareContentOption) -> [Any] {
        guard let content = content else { return [] }

        switch option {
        case .light:
            var items: [Any] = [content.displayTitle]
            if let shareURL = resolvedShareURLString(for: content) {
                if let url = URL(string: shareURL) {
                    items.append(url)
                } else {
                    items.append(shareURL)
                }
            }
            return items
        case .medium:
            if let mediumText = markdownForShare(option: .medium) {
                return [MarkdownItemProvider(markdown: mediumText, subject: content.displayTitle)]
            }
            return buildShareItems(option: .light)
        case .full:
            if let fullText = markdownForShare(option: .full) {
                return [MarkdownItemProvider(markdown: fullText, subject: content.displayTitle)]
            }
            return buildShareItems(option: .medium)
        }
    }

    func openInChatGPT() async {
        // Strategy:
        // 1) Build full markdown and offer it via the share sheet so ChatGPT's share extension can receive the text.
        // 2) As a convenience, also put the text on the clipboard (user can paste if needed in the app).
        // 3) Use custom item provider to preserve line breaks in Mail by converting to HTML.

        guard let content = content else { return }
        let fullText = buildFullMarkdown() ?? content.displayTitle

        // Put on clipboard (helps in case target app reads clipboard or the user wants to paste manually)
        UIPasteboard.general.string = fullText

        // Create custom item provider that converts markdown to HTML for Mail
        let itemProvider = MarkdownItemProvider(markdown: fullText, subject: content.displayTitle)

        // Prepare share sheet with custom provider
        let activityVC = UIActivityViewController(activityItems: [itemProvider], applicationActivities: nil)
        activityVC.excludedActivityTypes = [.assignToContact, .saveToCameraRoll, .addToReadingList, .postToFacebook, .postToTwitter]
        presentActivityViewControllerWhenReady(activityVC)
    }

    private func presentActivityViewControllerWhenReady(
        _ activityViewController: UIActivityViewController,
        attempt: Int = 0
    ) {
        let maxAttempts = 8
        let retryDelaySeconds = 0.08

        guard let rootViewController = activeRootViewController() else { return }

        let topViewController = topVisibleViewController(from: rootViewController)
            ?? rootViewController

        // The share options bottom sheet is dismissed right before sharing. Retry until
        // UIKit finishes the dismissal, then present from the current top controller.
        if rootViewController.presentedViewController?.isBeingDismissed == true
            || topViewController.isBeingPresented
            || topViewController.isBeingDismissed {
            guard attempt < maxAttempts else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + retryDelaySeconds) { [weak self] in
                self?.presentActivityViewControllerWhenReady(
                    activityViewController,
                    attempt: attempt + 1
                )
            }
            return
        }

        topViewController.present(activityViewController, animated: true)
    }

    private func activeRootViewController() -> UIViewController? {
        let activeWindowScenes = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .filter { $0.activationState == .foregroundActive }

        let activeWindow = activeWindowScenes
            .flatMap(\.windows)
            .first(where: { $0.isKeyWindow })
            ?? activeWindowScenes
                .flatMap(\.windows)
                .first(where: { !$0.isHidden })

        return activeWindow?.rootViewController
    }

    private func topVisibleViewController(from root: UIViewController?) -> UIViewController? {
        guard let root else { return nil }

        if let navigationController = root as? UINavigationController {
            return topVisibleViewController(from: navigationController.visibleViewController)
        }

        if let tabBarController = root as? UITabBarController {
            return topVisibleViewController(from: tabBarController.selectedViewController)
        }

        if let presentedViewController = root.presentedViewController,
           !presentedViewController.isBeingDismissed {
            return topVisibleViewController(from: presentedViewController)
        }

        return root
    }
}

// MARK: - Custom Item Provider for Markdown Sharing
class MarkdownItemProvider: NSObject, UIActivityItemSource {
    private let markdown: String
    private let subject: String?

    init(markdown: String, subject: String? = nil) {
        self.markdown = markdown
        self.subject = subject
        super.init()
    }

    func activityViewControllerPlaceholderItem(_ activityViewController: UIActivityViewController) -> Any {
        return markdown
    }

    func activityViewController(_ activityViewController: UIActivityViewController, itemForActivityType activityType: UIActivity.ActivityType?) -> Any? {
        switch shareActivityKind(activityType) {
        case .mail:
            return convertMarkdownToHTML(markdown).data(using: .utf8)
        case .gmail:
            return gmailFriendlyText(markdown)
        case .other:
            return markdown
        }
    }

    func activityViewController(
        _ activityViewController: UIActivityViewController,
        subjectForActivityType activityType: UIActivity.ActivityType?
    ) -> String {
        if let subject, !subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return subject
        }
        return markdown
            .components(separatedBy: .newlines)
            .first(where: { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    func activityViewController(
        _ activityViewController: UIActivityViewController,
        dataTypeIdentifierForActivityType activityType: UIActivity.ActivityType?
    ) -> String {
        switch shareActivityKind(activityType) {
        case .mail:
            return UTType.html.identifier
        case .gmail, .other:
            return UTType.plainText.identifier
        }
    }

    private enum ShareActivityKind {
        case mail
        case gmail
        case other
    }

    private func shareActivityKind(_ activityType: UIActivity.ActivityType?) -> ShareActivityKind {
        if activityType == .mail {
            return .mail
        }

        guard let rawValue = activityType?.rawValue.lowercased() else {
            return .other
        }

        if rawValue.contains("gmail") {
            return .gmail
        }

        return .other
    }

    private func convertMarkdownToHTML(_ markdown: String) -> String {
        var html = "<html><body style='font-family: \(AppFontFamily.sans), -apple-system, sans-serif; font-size: 14px; line-height: 1.6;'>"

        // Split into paragraphs and convert
        let paragraphs = markdown.components(separatedBy: "\n\n")

        for paragraph in paragraphs {
            var processedParagraph = paragraph

            // Convert headers
            if processedParagraph.hasPrefix("### ") {
                processedParagraph = "<h3>" + processedParagraph.dropFirst(4) + "</h3>"
            } else if processedParagraph.hasPrefix("## ") {
                processedParagraph = "<h2>" + processedParagraph.dropFirst(3) + "</h2>"
            } else if processedParagraph.hasPrefix("# ") {
                processedParagraph = "<h1>" + processedParagraph.dropFirst(2) + "</h1>"
            } else if processedParagraph.hasPrefix("---") {
                processedParagraph = "<hr/>"
            } else if processedParagraph.contains("\n- ") || processedParagraph.hasPrefix("- ") {
                // Convert bullet lists
                let items = processedParagraph.components(separatedBy: "\n").filter { $0.hasPrefix("- ") }
                let listItems = items.map { "<li>" + $0.dropFirst(2) + "</li>" }.joined()
                processedParagraph = "<ul>" + listItems + "</ul>"
            } else if processedParagraph.contains("\n> ") || processedParagraph.hasPrefix("> ") {
                // Convert quotes
                let quotes = processedParagraph.components(separatedBy: "\n").filter { $0.hasPrefix("> ") }
                let quoteText = quotes.map { String($0.dropFirst(2)) }.joined(separator: "<br/>")
                processedParagraph = "<blockquote style='border-left: 3px solid #ccc; padding-left: 10px; margin: 10px 0;'>" + quoteText + "</blockquote>"
            } else if !processedParagraph.isEmpty {
                // Regular paragraph - convert single newlines to <br/>
                processedParagraph = "<p>" + processedParagraph.replacingOccurrences(of: "\n", with: "<br/>") + "</p>"
            }

            html += processedParagraph
        }

        html += "</body></html>"
        return html
    }

    private func gmailFriendlyText(_ markdown: String) -> String {
        let normalized = markdown.replacingOccurrences(of: "\r\n", with: "\n")
        let lines = normalized.components(separatedBy: "\n")
        var outputLines: [String] = []
        var lastLineWasSpacer = false

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty {
                if !outputLines.isEmpty, !lastLineWasSpacer {
                    outputLines.append("")
                    lastLineWasSpacer = true
                }
                continue
            }

            if trimmed == "---" {
                if !outputLines.isEmpty, !lastLineWasSpacer {
                    outputLines.append("")
                    lastLineWasSpacer = true
                }
                continue
            }

            let nextLine: String
            if trimmed.hasPrefix("### ") {
                nextLine = String(trimmed.dropFirst(4)) + ":"
            } else if trimmed.hasPrefix("## ") {
                nextLine = String(trimmed.dropFirst(3)) + ":"
            } else if trimmed.hasPrefix("# ") {
                nextLine = String(trimmed.dropFirst(2)) + ":"
            } else if trimmed.hasPrefix("- ") {
                nextLine = "- " + String(trimmed.dropFirst(2))
            } else if trimmed.hasPrefix("> ") {
                nextLine = "\"\(String(trimmed.dropFirst(2)))\""
            } else {
                nextLine = trimmed
            }

            outputLines.append(nextLine)
            lastLineWasSpacer = false
        }

        while outputLines.last == "" {
            outputLines.removeLast()
        }

        return outputLines.joined(separator: "\n")
    }
}
