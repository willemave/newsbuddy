//
//  KnowledgeTimelineItem.swift
//  newsly
//

import Foundation

enum KnowledgeTimelineItem: Identifiable {
    case saved(KnowledgeSavedTimelineEntry)
    case chat(session: ChatSessionSummary, preview: String?)
    case deck(LearningDeck)
    case narration(AudioEpisode)

    var id: String {
        switch self {
        case .saved(let entry):
            entry.content.contentType == .news ? "saved-news-\(entry.content.id)" : "saved-\(entry.content.id)"
        case .chat(let session, _): "chat-\(session.id)"
        case .deck(let deck): "deck-\(deck.id)"
        case .narration(let episode): "narration-\(episode.id)"
        }
    }

    var activityDate: Date {
        switch self {
        case .saved(let entry): entry.activityDate
        case .chat(let session, _): session.lastActivityDate
        case .deck(let deck): deck.knowledgeActivityDate
        case .narration(let episode): episode.updatedAt ?? episode.createdAt
        }
    }

    static func merged(
        saved: [ContentSummary],
        chats: [ChatSessionSummary],
        decks: [LearningDeck],
        narrations: [AudioEpisode]
    ) -> [KnowledgeTimelineItem] {
        let savedSources = Set(saved.map(KnowledgeSource.init))
        let sourceChats = Dictionary(grouping: chats, by: \.knowledgeSource)
        let sourceDecks = Dictionary(grouping: decks, by: \.knowledgeSource)
        let savedItems = saved.map { content in
            let source = KnowledgeSource(content)
            return Self.saved(KnowledgeSavedTimelineEntry(
                content: content,
                chats: sourceChats[source] ?? [],
                decks: sourceDecks[source] ?? []
            ))
        }
        let chatItems = chats.filter { session in
            !(session.knowledgeSource.map(savedSources.contains) ?? false)
        }.map { session in
            Self.chat(
                session: session,
                preview: KnowledgeChatPreview.make(for: session)
            )
        }
        let deckItems = decks.filter { deck in
            !(deck.knowledgeSource.map(savedSources.contains) ?? false)
        }.map(Self.deck)
        let items = savedItems + chatItems + deckItems + narrations.map(Self.narration)
        return items.sorted { lhs, rhs in
            if lhs.activityDate == rhs.activityDate {
                return lhs.id < rhs.id
            }
            return lhs.activityDate > rhs.activityDate
        }
    }
}

/// One saved source and the chats/decks opened from it. The source row keeps its
/// identity while related work updates its activity and processing state.
struct KnowledgeSavedTimelineEntry {
    let content: ContentSummary
    let chats: [ChatSessionSummary]
    let decks: [LearningDeck]
    // Stored presentation values let SwiftUI see activity changes even when a
    // related chat's equality compares only its identity.
    let activityDate: Date
    let statusText: String?
    let isBusy: Bool

    init(content: ContentSummary, chats: [ChatSessionSummary] = [], decks: [LearningDeck] = []) {
        self.content = content
        self.chats = chats.sorted {
            if $0.lastActivityDate == $1.lastActivityDate { return $0.id < $1.id }
            return $0.lastActivityDate > $1.lastActivityDate
        }
        self.decks = decks.sorted {
            if $0.knowledgeActivityDate == $1.knowledgeActivityDate { return $0.id < $1.id }
            return $0.knowledgeActivityDate > $1.knowledgeActivityDate
        }
        let savedDate = content.knowledgeActivityDate
        activityDate = max(
            savedDate,
            max(self.chats.first?.lastActivityDate ?? savedDate, self.decks.first?.knowledgeActivityDate ?? savedDate)
        )

        var parts: [String] = []
        if let deck = self.decks.first(where: \.hasActiveLatestRun) ?? self.decks.first {
            parts.append("Deck \(deck.statusLabel.lowercased())")
        }
        if chats.contains(where: \.isPreparingChat) {
            parts.append("Chat preparing")
        } else if chats.contains(where: \.isProcessing) {
            parts.append("Chat answering")
        }
        statusText = parts.isEmpty ? nil : parts.joined(separator: " · ")

        isBusy = decks.contains(where: \.hasActiveLatestRun)
            || chats.contains { $0.isPreparingChat || $0.isProcessing }
            || (content.savedLibraryItemState == .processing && !content.hasStalledKnowledgePreparation)
    }
}

extension ChatSessionSummary {
    var knowledgeSource: KnowledgeSource? {
        if let contentId { return .content(contentId) }
        if let newsItemId { return .news(newsItemId) }
        return nil
    }
}

extension ContentSummary {
    var hasStalledKnowledgePreparation: Bool {
        guard savedLibraryItemState == .processing,
              let createdAt = ContentTimestampFormatter.parse(createdAt)
        else { return false }
        return AppClock.now.timeIntervalSince(createdAt) > 24 * 60 * 60
    }
}

extension LearningDeck {
    var knowledgeActivityDate: Date {
        let runActivity = latestRun.map { max($0.createdAt, $0.updatedAt ?? $0.createdAt) } ?? createdAt
        return max(createdAt, max(updatedAt ?? createdAt, runActivity))
    }

    var knowledgeSource: KnowledgeSource? {
        guard sourceKind == .content, let sourceContentId else { return nil }
        return .content(sourceContentId)
    }
}

enum KnowledgePaginationSource: Equatable {
    case saved
    case chats

    static func next(savedOldest: Date?, chatOldest: Date?) -> Self? {
        switch (savedOldest, chatOldest) {
        case let (saved?, chat?): saved >= chat ? .saved : .chats
        case (.some, .none): .saved
        case (.none, .some): .chats
        case (.none, .none): nil
        }
    }
}

/// A saved source. Content and news item IDs are separate namespaces.
enum KnowledgeSource: Hashable {
    case content(Int)
    case news(Int)

    init(_ content: ContentSummary) {
        self = content.contentType == .news ? .news(content.id) : .content(content.id)
    }

    var contentId: Int? {
        if case .content(let id) = self { id } else { nil }
    }

    var newsItemId: Int? {
        if case .news(let id) = self { id } else { nil }
    }
}

/// Deep dives already made from a saved source, shown as row markers.
struct KnowledgeSourceDerivatives: OptionSet, Hashable {
    let rawValue: Int

    static let chat = Self(rawValue: 1 << 0)
    static let deck = Self(rawValue: 1 << 2)
    static let narration = Self(rawValue: 1 << 3)

    static func index(
        chats: [ChatSessionSummary],
        decks: [LearningDeck],
        narrations: [AudioEpisode]
    ) -> [KnowledgeSource: Self] {
        var index: [KnowledgeSource: Self] = [:]
        func mark(_ source: KnowledgeSource, _ derivative: Self) {
            index[source, default: []].insert(derivative)
        }

        for session in chats {
            if let contentId = session.contentId { mark(.content(contentId), .chat) }
            if let newsItemId = session.newsItemId { mark(.news(newsItemId), .chat) }
        }
        for deck in decks where deck.sourceKind == .content {
            if let contentId = deck.sourceContentId { mark(.content(contentId), .deck) }
        }
        for episode in narrations {
            episode.sourceContentIds.forEach { mark(.content($0), .narration) }
            episode.sourceItemIds.forEach { mark(.news($0), .narration) }
        }
        return index
    }
}

struct KnowledgeTimelineDayGroup: Identifiable {
    let day: Date
    let title: String
    let items: [KnowledgeTimelineItem]

    var id: Date { day }

    static func group(_ items: [KnowledgeTimelineItem]) -> [Self] {
        let calendar = Calendar.current
        return Dictionary(grouping: items) { calendar.startOfDay(for: $0.activityDate) }
            .map { day, items in
                Self(day: day, title: title(for: day, calendar: calendar), items: items)
            }
            .sorted { $0.day > $1.day }
    }

    private static func title(for day: Date, calendar: Calendar) -> String {
        if calendar.isDateInToday(day) { return "TODAY" }
        if calendar.isDateInYesterday(day) { return "YESTERDAY" }
        return day.formatted(.dateTime.weekday(.wide).month(.abbreviated).day()).uppercased()
    }
}

private enum KnowledgeChatPreview {
    private static let markdownPrefixPattern = try! NSRegularExpression(
        pattern: #"(?m)^\s{0,3}(#{1,6}(\s+|$)|>\s+|[-*+]\s+|\d+\.\s+)"#
    )
    private static let whitespacePattern = try! NSRegularExpression(pattern: #"\s+"#)

    static func make(for session: ChatSessionSummary) -> String? {
        if let lastMessage = nonEmptyTrimmed(session.lastMessagePreview) {
            return plainText(lastMessage)
        }
        if let articleSummary = nonEmptyTrimmed(session.articleSummary) {
            return "About: \(plainText(articleSummary))"
        }
        return session.displaySubtitle.map(plainText)
    }

    private static func plainText(_ markdown: String) -> String {
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        let attributed = try? AttributedString(markdown: markdown, options: options)
        let plainText = attributed.map { String($0.characters) } ?? markdown
        let withoutPrefixes = markdownPrefixPattern.stringByReplacingMatches(
            in: plainText,
            range: NSRange(plainText.startIndex..., in: plainText),
            withTemplate: ""
        )
        return whitespacePattern.stringByReplacingMatches(
            in: withoutPrefixes,
            range: NSRange(withoutPrefixes.startIndex..., in: withoutPrefixes),
            withTemplate: " "
        )
        .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

extension LearningDeck {
    var timelineSubtitle: String {
        guard let sourceTitle = nonEmptyTrimmed(sourceTitle) else {
            return "Interactive lesson"
        }

        let normalizedTitle = displayTitle.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
        let normalizedSource = sourceTitle.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
        guard normalizedTitle != normalizedSource,
              !normalizedTitle.hasPrefix(normalizedSource),
              !normalizedSource.hasPrefix(normalizedTitle) else {
            return "Interactive lesson"
        }
        return sourceTitle
    }
}
