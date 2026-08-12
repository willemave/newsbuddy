//
//  DetailKnowledgeActionsSheet.swift
//  newsly
//

import SwiftUI

struct DetailKnowledgeActionsSheet: View {
    private let actionError: String?
    private let isStartingAction: Bool
    private let onClose: () -> Void
    private let onStartChat: () -> Void
    private let onAskCouncil: () -> Void
    private let onCreateLearningDeck: () -> Void

    init(
        actionError: String?,
        isStartingAction: Bool,
        onClose: @escaping () -> Void,
        onStartChat: @escaping () -> Void,
        onAskCouncil: @escaping () -> Void,
        onCreateLearningDeck: @escaping () -> Void
    ) {
        self.actionError = actionError
        self.isStartingAction = isStartingAction
        self.onClose = onClose
        self.onStartChat = onStartChat
        self.onAskCouncil = onAskCouncil
        self.onCreateLearningDeck = onCreateLearningDeck
    }

    var body: some View {
        VStack(spacing: 0) {
            MiniSheetHeader(
                title: "Knowledge actions",
                titleAccessibilityIdentifier: "content.knowledge_actions.sheet",
                dismiss: onClose
            )

            ScrollView {
                VStack(spacing: 12) {
                    if let actionError {
                        errorBanner(actionError)
                    }

                    LazyVGrid(columns: actionTileColumns, spacing: 10) {
                        DetailKnowledgeActionTile(
                            icon: "message",
                            title: "Start Chat",
                            disabled: isStartingAction,
                            accessibilityIdentifier: "content.knowledge_actions.start_chat",
                            action: onStartChat
                        )

                        DetailKnowledgeActionTile(
                            icon: "person.3.sequence.fill",
                            title: "Ask a Council",
                            disabled: isStartingAction,
                            accessibilityIdentifier: "content.knowledge_actions.council",
                            action: onAskCouncil
                        )
                    }

                    DetailKnowledgeWideAction(
                        icon: "rectangle.on.rectangle",
                        title: "Create Learning Deck",
                        subtitle: "Turn this source into a visual study deck",
                        disabled: isStartingAction,
                        accessibilityIdentifier: "content.knowledge_actions.learning_deck",
                        action: onCreateLearningDeck
                    )
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.bottom, 20)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary)
    }

    private var actionTileColumns: [GridItem] {
        [
            GridItem(.flexible(), spacing: 10),
            GridItem(.flexible(), spacing: 10)
        ]
    }

    private func errorBanner(_ message: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.circle.fill")
                .foregroundColor(.statusDestructive)
            Text(message)
                .font(.appFootnote)
                .foregroundColor(.statusDestructive)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.statusDestructive.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

private struct DetailKnowledgeActionTile: View {
    let icon: String
    let title: String
    var disabled = false
    let accessibilityIdentifier: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                KnowledgeActionIcon(icon)

                Spacer(minLength: 0)

                Text(title)
                    .font(.appSubheadline.weight(.semibold))
                    .foregroundColor(Color.onSurface)
                    .lineLimit(2)
                    .minimumScaleFactor(0.84)
                    .multilineTextAlignment(.leading)
            }
            .knowledgeActionSurface(minHeight: 112)
        }
        .buttonStyle(PressableButtonStyle())
        .disabled(disabled)
        .opacity(disabled ? 0.55 : 1)
        .accessibilityLabel(title)
        .accessibilityIdentifier(accessibilityIdentifier)
    }
}

private struct DetailKnowledgeWideAction: View {
    let icon: String
    let title: String
    let subtitle: String
    var disabled = false
    let accessibilityIdentifier: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                KnowledgeActionIcon(icon)

                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.appSubheadline.weight(.semibold))
                        .foregroundColor(Color.onSurface)

                    Text(subtitle)
                        .font(.appCaption)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }

                Spacer(minLength: 8)

                Image(systemName: "chevron.right")
                    .font(.appSymbol(size: 13, weight: .semibold))
                    .foregroundColor(Color.onSurfaceTertiary)
                    .accessibilityHidden(true)
            }
            .knowledgeActionSurface(minHeight: 70)
        }
        .buttonStyle(PressableButtonStyle())
        .disabled(disabled)
        .opacity(disabled ? 0.55 : 1)
        .accessibilityLabel(title)
        .accessibilityIdentifier(accessibilityIdentifier)
    }
}

private struct KnowledgeActionIcon: View {
    private let icon: String

    init(_ icon: String) {
        self.icon = icon
    }

    var body: some View {
        Image(systemName: icon)
            .font(.appSymbol(size: 18, weight: .semibold))
            .foregroundColor(.brandPrimary)
            .frame(width: 42, height: 42)
            .background(Color.brandPrimary.opacity(0.15))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

private extension View {
    func knowledgeActionSurface(minHeight: CGFloat) -> some View {
        self
            .padding(12)
            .frame(maxWidth: .infinity, minHeight: minHeight, alignment: .topLeading)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(Color.surfaceSecondary)
                    .appShadow(.floating)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.34), lineWidth: 0.5)
            )
    }
}
