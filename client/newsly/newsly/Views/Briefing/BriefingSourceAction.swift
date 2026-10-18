import SwiftUI

enum BriefingSourceAction: String, CaseIterable, Identifiable {
    case addToKnowledge
    case chat
    case createDeck

    var id: String { rawValue }

    var title: String {
        switch self {
        case .addToKnowledge: "Add to Knowledge"
        case .chat: "Chat"
        case .createDeck: "Create Deck"
        }
    }

    var systemImage: String {
        switch self {
        case .addToKnowledge: "bookmark"
        case .chat: "message"
        case .createDeck: "rectangle.on.rectangle"
        }
    }
}

extension View {
    func briefingSourceMenu(
        sourceKey: String?,
        onAction: ((String, BriefingSourceAction) -> Void)?
    ) -> some View {
        contextMenu {
            if let sourceKey, let onAction {
                ForEach(BriefingSourceAction.allCases) { action in
                    Button {
                        onAction(sourceKey, action)
                    } label: {
                        Label(action.title, systemImage: action.systemImage)
                    }
                    .accessibilityIdentifier("briefing.source_action.\(action.rawValue)")
                }
            }
        }
    }
}
