//
//  OpenDisplayInterstitial.swift
//  newsly
//

import SwiftUI

/// The trailing page before anything is picked. Nothing is ever selected on
/// the reader's behalf (opening an item marks it read), so the page invites a
/// pick and, for Briefing tiers, offers the lens's own audio.
struct OpenDisplayInterstitial: View {
    enum Kind {
        case news
        case podcasts
        case articles
        case caughtUp
        case knowledge

        var title: String {
            switch self {
            case .news: "Hot off the press."
            case .podcasts: "Tune in."
            case .articles: "Pull up a chair."
            case .caughtUp: "All caught up."
            case .knowledge: "Your shelf."
            }
        }

        var message: String {
            switch self {
            case .news: "Tap a story and it opens here, beside the news."
            case .podcasts: "Choose an episode and it opens here, ready to play."
            case .articles: "Pick an article and it opens right here."
            case .caughtUp: "New pieces land here as your briefing updates."
            case .knowledge: "Saved pieces, chats, and decks open here when you pick one."
            }
        }
    }

    struct Action {
        let title: String
        let systemImage: String
        let perform: () -> Void
    }

    let kind: Kind
    var meta: String?
    var action: Action?

    var body: some View {
        VStack(spacing: 0) {
            Image("BuddyMark")
                .resizable()
                .scaledToFit()
                .frame(width: 88, height: 88)
                .accessibilityHidden(true)
                .padding(.bottom, 16)

            Text(kind.title)
                .font(.terracottaDisplayLarge)
                .foregroundStyle(Color.onSurface)
                .multilineTextAlignment(.center)

            Text(kind.message)
                .font(.appCallout)
                .foregroundStyle(Color.onSurfaceSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
                .padding(.top, 8)

            if let action {
                Button(action: action.perform) {
                    Label(action.title, systemImage: action.systemImage)
                        .font(.appSubheadline.weight(.semibold))
                        .foregroundStyle(Color.surfacePrimary)
                        .padding(.horizontal, 18)
                        .padding(.vertical, 11)
                        .background(Capsule().fill(Color.appChromeAccent))
                }
                .buttonStyle(.plain)
                .padding(.top, 22)
                .accessibilityIdentifier("open.interstitial.action")
            }

            if let meta {
                Text(meta.uppercased())
                    .kicker(color: .onSurfaceTertiary)
                    .padding(.top, 12)
            }
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("open.interstitial")
    }
}

/// "73 episodes waiting · about 61 hours" / "9 unread · about 1 hr 40 min of
/// reading". Nil when nothing is unread.
func openDisplayInterstitialMeta(isPodcasts: Bool, unreadCount: Int, totalSeconds: Int) -> String? {
    guard unreadCount > 0 else { return nil }
    let count = isPodcasts
        ? "\(unreadCount) \(unreadCount == 1 ? "episode" : "episodes") waiting"
        : "\(unreadCount) unread"
    guard totalSeconds > 0 else { return count }
    let minutes = max(Int((Double(totalSeconds) / 60).rounded()), 1)
    let duration: String
    if minutes < 60 {
        duration = "about \(minutes) min"
    } else if minutes < 600 {
        let hours = minutes / 60, rest = minutes % 60
        duration = rest == 0 ? "about \(hours) hr" : "about \(hours) hr \(rest) min"
    } else {
        duration = "about \(Int((Double(minutes) / 60).rounded())) hours"
    }
    return isPodcasts ? "\(count) · \(duration)" : "\(count) · \(duration) of reading"
}
