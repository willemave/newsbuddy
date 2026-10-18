//
//  KnowledgeSourceActions.swift
//  newsly
//

import SwiftUI

/// Commands for an existing deep dive; creation remains a source swipe action.
enum KnowledgeRelatedAction {
    case openChat(ChatSessionSummary)
    case deleteChat(ChatSessionSummary)
    case openDeck(LearningDeck)
    case regenerateDeck(LearningDeck)
    case deleteDeck(LearningDeck)
}

struct KnowledgeSourceActivityMenu: View {
    let entry: KnowledgeSavedTimelineEntry
    let busyDeckIDs: Set<Int>
    let perform: (KnowledgeRelatedAction) -> Void

    var body: some View {
        ForEach(entry.chats) { session in
            Menu {
                Button {
                    perform(.openChat(session))
                } label: {
                    Label("Open chat", systemImage: "message")
                }
                .accessibilityIdentifier("knowledge.chat.\(session.id).open")
                Button(role: .destructive) {
                    perform(.deleteChat(session))
                } label: {
                    Label("Delete chat", systemImage: "trash")
                }
                .accessibilityIdentifier("knowledge.chat.\(session.id).delete")
            } label: {
                Label(session.displayTitle, systemImage: "message")
            }
            .accessibilityIdentifier("knowledge.chat.\(session.id).menu")
        }
        ForEach(entry.decks) { deck in
            Button {
                perform(.openDeck(deck))
            } label: {
                Label("Open deck", systemImage: "rectangle.on.rectangle")
            }
            .accessibilityIdentifier("knowledge.deck.\(deck.id).open")
            Button {
                perform(.regenerateDeck(deck))
            } label: {
                Label("Regenerate deck", systemImage: "arrow.clockwise")
            }
            .disabled(deck.hasActiveLatestRun || busyDeckIDs.contains(deck.id))
            .accessibilityIdentifier("knowledge.deck.\(deck.id).regenerate")
            Button(role: .destructive) {
                perform(.deleteDeck(deck))
            } label: {
                Label("Delete deck", systemImage: "trash")
            }
            .accessibilityIdentifier("knowledge.deck.\(deck.id).delete")
        }
    }
}

/// Deep dives offered from a saved Knowledge row's swipe actions.
enum KnowledgeSourceAction: Hashable {
    case chat
    case deck
    case listen

    static let leading: [Self] = [.chat, .deck, .listen]

    var title: String {
        switch self {
        case .chat: "Chat"
        case .deck: "Deck"
        case .listen: "Listen"
        }
    }

    var systemImage: String {
        switch self {
        case .chat: "message"
        case .deck: "rectangle.on.rectangle"
        case .listen: "headphones"
        }
    }

    var tint: Color {
        switch self {
        case .chat: Color.brandPrimary
        case .deck: Color.onSurfaceSecondary
        case .listen: Color.onSurfaceTertiary
        }
    }

    /// Spoken name of what this action leaves behind for the source.
    fileprivate var resultName: String {
        switch self {
        case .chat: "chat"
        case .deck: "deck"
        case .listen: "narration"
        }
    }
}

extension KnowledgeSourceDerivatives {
    private static let markerOrder: [(Self, KnowledgeSourceAction)] = [
        (.deck, .deck),
        (.chat, .chat),
        (.narration, .listen)
    ]

    /// Existing deep dives, as the actions that made them, in marker order.
    var actions: [KnowledgeSourceAction] {
        Self.markerOrder.filter { contains($0.0) }.map(\.1)
    }

    var accessibilitySummary: String {
        let names = actions.map(\.resultName)
        return names.isEmpty ? "" : "Has \(names.joined(separator: ", "))"
    }
}

extension View {
    /// Chat, Deck, and Listen on the leading edge (full swipe chats); Remove is trailing.
    /// Deep dives appear only once the source is ready.
    func knowledgeSourceSwipeActions(
        for content: ContentSummary,
        onRemove: @escaping () -> Void,
        perform: @escaping (KnowledgeSourceAction) -> Void
    ) -> some View {
        let isReady = content.savedLibraryItemState == .ready
        return self
            .swipeActions(edge: .leading, allowsFullSwipe: true) {
                if isReady {
                    ForEach(KnowledgeSourceAction.leading, id: \.self) { action in
                        KnowledgeSourceActionButton(action: action, contentID: content.id, perform: perform)
                    }
                }
            }
            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                Button(role: .destructive, action: onRemove) {
                    Label("Remove", systemImage: "bookmark.slash")
                }
            }
    }

    /// Slides the row open once to reveal the leading swipe actions, then closes it.
    func knowledgeSwipeHint(isActive: Bool, onFinished: @escaping () -> Void) -> some View {
        modifier(KnowledgeSwipeHintModifier(isActive: isActive, onFinished: onFinished))
    }
}

private struct KnowledgeSourceActionButton: View {
    let action: KnowledgeSourceAction
    let contentID: Int
    let perform: (KnowledgeSourceAction) -> Void

    var body: some View {
        Button {
            perform(action)
        } label: {
            Label(action.title, systemImage: action.systemImage)
        }
        .tint(action.tint)
        .accessibilityIdentifier("knowledge.saved.\(contentID).\(action.title.lowercased())")
    }
}

/// Trailing row button that jumps straight into a source's existing chat or deck.
struct KnowledgeSourceShortcutButton: View {
    let action: KnowledgeSourceAction
    let contentID: Int
    let perform: () -> Void

    var body: some View {
        Button(action: perform) {
            Image(systemName: action.systemImage)
                .font(.appSymbol(size: 12, weight: .semibold))
                .foregroundStyle(Color.brandPrimary)
                .frame(width: 30, height: 30)
                .background(Color.surfaceSecondary, in: Circle())
                .frame(width: 36, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Open \(action.resultName)")
        .accessibilityIdentifier("knowledge.saved.\(contentID).open_\(action.resultName)")
    }
}

extension KnowledgeSavedTimelineEntry {
    /// Existing deep dives the source row opens directly, in shortcut order.
    var openableActions: [KnowledgeSourceAction] {
        (chats.isEmpty ? [] : [.chat]) + (decks.isEmpty ? [] : [.deck])
    }
}

private struct KnowledgeSwipeHintModifier: ViewModifier {
    let isActive: Bool
    let onFinished: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var offset: CGFloat = 0

    func body(content: Content) -> some View {
        content
            .offset(x: offset)
            .background(alignment: .leading) {
                if isActive {
                    KnowledgeSwipeHintActions()
                        .frame(width: offset, alignment: .trailing)
                        .clipped()
                        .accessibilityHidden(true)
                }
            }
            .task(id: isActive) { await play() }
    }

    private func play() async {
        guard isActive else { return }
        do {
            try await Task.sleep(for: .milliseconds(700))
            withAnimation(reduceMotion ? nil : .spring(response: 0.45, dampingFraction: 0.82)) {
                offset = KnowledgeSwipeHintActions.width
            }
            try await Task.sleep(for: .milliseconds(1_600))
        } catch {
            offset = 0
            return
        }
        withAnimation(reduceMotion ? nil : .spring(response: 0.5, dampingFraction: 0.9)) {
            offset = 0
        } completion: {
            onFinished()
        }
    }
}

/// Static copy of the leading swipe actions, mirroring the system's round buttons with captions.
private struct KnowledgeSwipeHintActions: View {
    private static let actionWidth: CGFloat = 70
    static let width = actionWidth * CGFloat(KnowledgeSourceAction.leading.count)

    var body: some View {
        HStack(spacing: 0) {
            ForEach(KnowledgeSourceAction.leading, id: \.self) { action in
                VStack(spacing: 5) {
                    Image(systemName: action.systemImage)
                        .font(.appSymbol(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 58, height: 40)
                        .background(action.tint, in: Capsule())
                    Text(action.title)
                        .font(.appCaption2)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
                .frame(width: Self.actionWidth)
            }
        }
        .frame(width: Self.width)
    }
}
