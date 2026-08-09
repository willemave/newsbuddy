//
//  LearningTimelineItem.swift
//  newsly
//

import Foundation

enum LearningTimelineItem: Identifiable {
    case chat(session: ChatSessionSummary, preview: String?)
    case deck(LearningDeck)
    case narration(AudioEpisode)

    var id: String {
        switch self {
        case .chat(let session, _): "chat-\(session.id)"
        case .deck(let deck): "deck-\(deck.id)"
        case .narration(let episode): "narration-\(episode.id)"
        }
    }

    var activityDate: Date {
        switch self {
        case .chat(let session, _): session.lastActivityDate ?? session.createdAt
        case .deck(let deck): deck.updatedAt ?? deck.latestRun?.updatedAt ?? deck.createdAt
        case .narration(let episode): episode.updatedAt ?? episode.createdAt
        }
    }

    static func merged(
        chats: [ChatSessionSummary],
        decks: [LearningDeck],
        narrations: [AudioEpisode]
    ) -> [LearningTimelineItem] {
        let chatItems = chats.map { session in
            Self.chat(
                session: session,
                preview: LearningChatPreview.make(for: session)
            )
        }
        let items = chatItems + decks.map(Self.deck) + narrations.map(Self.narration)
        return items.sorted { lhs, rhs in
            if lhs.activityDate == rhs.activityDate {
                return lhs.id < rhs.id
            }
            return lhs.activityDate > rhs.activityDate
        }
    }
}

private enum LearningChatPreview {
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
