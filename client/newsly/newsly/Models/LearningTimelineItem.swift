//
//  LearningTimelineItem.swift
//  newsly
//

import Foundation

enum LearningTimelineItem: Identifiable {
    case chat(ChatSessionSummary)
    case deck(LearningDeck)
    case narration(AudioEpisode)

    var id: String {
        switch self {
        case .chat(let session): "chat-\(session.id)"
        case .deck(let deck): "deck-\(deck.id)"
        case .narration(let episode): "narration-\(episode.id)"
        }
    }

    var activityDate: Date {
        switch self {
        case .chat(let session): session.lastActivityDate ?? session.createdAt
        case .deck(let deck): deck.updatedAt ?? deck.latestRun?.updatedAt ?? deck.createdAt
        case .narration(let episode): episode.updatedAt ?? episode.createdAt
        }
    }

    static func merged(
        chats: [ChatSessionSummary],
        decks: [LearningDeck],
        narrations: [AudioEpisode]
    ) -> [LearningTimelineItem] {
        let items = chats.map(Self.chat) + decks.map(Self.deck) + narrations.map(Self.narration)
        return items.sorted { lhs, rhs in
            if lhs.activityDate == rhs.activityDate {
                return lhs.id < rhs.id
            }
            return lhs.activityDate > rhs.activityDate
        }
    }
}

struct LearningTimelineSection: Identifiable {
    let day: Date
    let label: String
    let items: [LearningTimelineItem]

    var id: Date { day }
}

enum LearningTimelineGrouper {
    static func sections(
        for items: [LearningTimelineItem],
        now: Date = AppClock.now,
        calendar: Calendar = .current
    ) -> [LearningTimelineSection] {
        var sections: [LearningTimelineSection] = []

        for item in items {
            let day = calendar.startOfDay(for: item.activityDate)
            if let last = sections.last, calendar.isDate(last.day, inSameDayAs: day) {
                sections[sections.count - 1] = LearningTimelineSection(
                    day: last.day,
                    label: last.label,
                    items: last.items + [item]
                )
            } else {
                sections.append(
                    LearningTimelineSection(
                        day: day,
                        label: TimelineDayLabel.text(for: item.activityDate, now: now, calendar: calendar),
                        items: [item]
                    )
                )
            }
        }

        return sections
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
