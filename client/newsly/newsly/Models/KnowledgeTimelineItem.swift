//
//  KnowledgeTimelineItem.swift
//  newsly
//

import Foundation

enum KnowledgeTimelineItem: Identifiable {
    case saved(ContentSummary)
    case chat(session: ChatSessionSummary, preview: String?)
    case deck(LearningDeck)
    case narration(AudioEpisode)

    var id: String {
        switch self {
        case .saved(let content): "saved-\(content.id)"
        case .chat(let session, _): "chat-\(session.id)"
        case .deck(let deck): "deck-\(deck.id)"
        case .narration(let episode): "narration-\(episode.id)"
        }
    }

    var activityDate: Date {
        switch self {
        case .saved(let content): content.knowledgeActivityDate
        case .chat(let session, _): session.lastActivityDate ?? session.createdAt
        case .deck(let deck): deck.updatedAt ?? deck.latestRun?.updatedAt ?? deck.createdAt
        case .narration(let episode): episode.updatedAt ?? episode.createdAt
        }
    }

    static func merged(
        saved: [ContentSummary],
        chats: [ChatSessionSummary],
        decks: [LearningDeck],
        narrations: [AudioEpisode]
    ) -> [KnowledgeTimelineItem] {
        let chatItems = chats.map { session in
            Self.chat(
                session: session,
                preview: KnowledgeChatPreview.make(for: session)
            )
        }
        let items = saved.map(Self.saved) + chatItems + decks.map(Self.deck) + narrations.map(Self.narration)
        return items.sorted { lhs, rhs in
            if lhs.activityDate == rhs.activityDate {
                return lhs.id < rhs.id
            }
            return lhs.activityDate > rhs.activityDate
        }
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

/// Identifies a saved source; content and news item IDs are separate namespaces.
struct KnowledgeSourceKey: Hashable {
    let id: Int
    let isNews: Bool

    init(id: Int, isNews: Bool) {
        self.id = id
        self.isNews = isNews
    }

    init(_ content: ContentSummary) {
        self.init(id: content.id, isNews: content.contentType == .news)
    }
}

/// Deep dives already made from a saved source, shown as row markers.
struct KnowledgeSourceDerivatives: OptionSet, Hashable {
    let rawValue: Int

    static let chat = Self(rawValue: 1 << 0)
    static let council = Self(rawValue: 1 << 1)
    static let deck = Self(rawValue: 1 << 2)
    static let narration = Self(rawValue: 1 << 3)

    static func index(
        chats: [ChatSessionSummary],
        decks: [LearningDeck],
        narrations: [AudioEpisode]
    ) -> [KnowledgeSourceKey: Self] {
        var index: [KnowledgeSourceKey: Self] = [:]
        func mark(_ key: KnowledgeSourceKey, _ derivative: Self) {
            index[key, default: []].insert(derivative)
        }

        for session in chats {
            let derivative: Self = session.isCouncilMode ? .council : .chat
            if let contentId = session.contentId {
                mark(KnowledgeSourceKey(id: contentId, isNews: false), derivative)
            }
            if let newsItemId = session.newsItemId {
                mark(KnowledgeSourceKey(id: newsItemId, isNews: true), derivative)
            }
        }
        for deck in decks where deck.sourceKind == .content {
            if let contentId = deck.sourceContentId {
                mark(KnowledgeSourceKey(id: contentId, isNews: false), .deck)
            }
        }
        for episode in narrations {
            for contentId in episode.sourceContentIds {
                mark(KnowledgeSourceKey(id: contentId, isNews: false), .narration)
            }
            for newsItemId in episode.sourceItemIds {
                mark(KnowledgeSourceKey(id: newsItemId, isNews: true), .narration)
            }
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
