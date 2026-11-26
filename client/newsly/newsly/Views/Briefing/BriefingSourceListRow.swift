import SwiftUI

/// An Articles or Podcasts source as a Knowledge-style row, for the
/// open-display sidebar beside the detail pane. It keeps every card action:
/// tap to open, the same swipe (mark read, chat, deck), the source menu plus
/// Mark as Read, and Save.
struct BriefingSourceListRow: View {
    let model: BriefingSegmentRenderModel
    let card: BriefingSourceCard
    let isSelected: Bool
    var savedSources: BriefingSavedSources?
    let onOpenSource: (String) -> Void
    var onSourceAction: ((String, BriefingSourceAction) -> Void)?
    let onMarkRead: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var swipe = BriefingCardSwipe()
    @State private var swipeAnchorY: CGFloat = 0
    @State private var markReadFeedbackTrigger = 0

    private var isRead: Bool { model.allSourcesRead }
    private var isSaved: Bool { savedSources?.isSaved(card.sourceKey) ?? false }
    private var isSaving: Bool { savedSources?.isPending(card.sourceKey) ?? false }

    var body: some View {
        rowBody
            .opacity(swipe.cardOpacity)
            .offset(x: swipe.cardOffset)
            .background { swipeBackdrop }
            .gesture(
                BriefingCardSwipeGesture(
                    onChanged: { translation, start in
                        swipe.translation = translation
                        swipeAnchorY = start.y
                    },
                    onEnded: endSwipe
                )
            )
            .sensoryFeedback(.impact(weight: .light), trigger: markReadFeedbackTrigger)
            .sensoryFeedback(.success, trigger: isSaved) { wasSaved, saved in !wasSaved && saved }
            .sensoryFeedback(.selection, trigger: swipe.selection) { _, selection in selection != nil }
            .accessibilityIdentifier("briefing.segment.\(model.segment.id)")
    }

    private var rowBody: some View {
        HStack(spacing: 0) {
            Button {
                onOpenSource(card.sourceKey)
            } label: {
                KnowledgeTimelineRow(
                    icon: card.kind == .podcast ? "waveform" : "doc.text",
                    imageURL: card.thumbnailURL ?? card.imageURL,
                    title: card.title,
                    subtitle: Self.summaryLine(model.segment.narrationText),
                    kicker: kicker
                ) {
                    EmptyView()
                }
                .padding(.trailing, -Spacing.appHorizontalMargin)
                .saturation(isRead && !isSelected ? 0.2 : 1)
                .opacity(isRead && !isSelected ? 0.72 : 1)
            }
            .buttonStyle(.plain)
            .briefingSourceMenu(
                sourceKey: card.sourceKey,
                onAction: onSourceAction,
                onMarkRead: isRead ? nil : markRead
            )
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(card.accessibilityLabel)
            .accessibilityValue(
                [isRead ? "Read" : "Unread", isSaved ? "Saved to Knowledge" : nil]
                    .compactMap { $0 }
                    .joined(separator: ", ")
            )
            .accessibilityHint("Opens beside the list")
            .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
            .accessibilityActions {
                if !isRead { Button("Mark as read", action: markRead) }
                if !isSaved { Button("Save to Knowledge", action: save) }
                Button(BriefingSourceAction.chat.title) { onSourceAction?(card.sourceKey, .chat) }
                Button(BriefingSourceAction.createDeck.title) { onSourceAction?(card.sourceKey, .createDeck) }
            }

            saveButton
        }
        .padding(.trailing, 8)
        .background {
            RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                .fill(isSelected ? Color.surfaceContainer : Color.surfacePrimary.opacity(0.001))
                .padding(.horizontal, 6)
        }
        .overlay(alignment: .leading) {
            if !isRead {
                Circle()
                    .fill(Color.appChromeAccent)
                    .frame(width: 6, height: 6)
                    .padding(.leading, 8)
                    .accessibilityHidden(true)
            }
        }
        .animation(.easeInOut(duration: 0.25), value: isRead)
    }

    private var saveButton: some View {
        Button(action: save) {
            Group {
                if isSaving {
                    ProgressView()
                        .controlSize(.small)
                        .tint(Color.onSurfaceSecondary)
                } else {
                    KnowledgeSaveIcon(
                        isSaved: isSaved,
                        size: 16,
                        unsavedColor: .onSurfaceSecondary,
                        badgeForegroundColor: .surfacePrimary
                    )
                }
            }
            .frame(width: 40, height: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isSaving)
        .accessibilityLabel(isSaved ? "Saved to Knowledge" : "Save to Knowledge")
        .accessibilityIdentifier("briefing.row.save.\(model.segment.id)")
    }

    /// Publisher (the work's attribution), length, and publication date;
    /// the kind fills in for a missing publisher or date.
    private var kicker: String {
        [card.publisher ?? card.kind.label, card.length, card.publicationDate]
            .compactMap { $0 }
            .joined(separator: " · ")
            .uppercased()
    }

    /// The composed summary's first sentence, so the row still carries the
    /// briefing's voice while the pane beside it holds the full detail.
    static func summaryLine(_ text: String) -> String? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        if let end = trimmed.firstIndex(where: { ".!?".contains($0) }) {
            return String(trimmed[...end])
        }
        return trimmed
    }

    @ViewBuilder
    private var swipeBackdrop: some View {
        if swipe.phase != .idle {
            BriefingCardSwipeBackdrop(
                phase: swipe.phase,
                progress: swipe.progress,
                isRead: isRead,
                anchorY: swipeAnchorY
            )
        }
    }

    private func endSwipe(_ translation: CGSize, cancelled: Bool) {
        let release = cancelled ? nil : BriefingCardSwipe(translation: translation).release
        withAnimation(AppMotion.respectingReduceMotion(reduceMotion, AppMotion.emphasized)) {
            swipe = BriefingCardSwipe()
        }
        switch release {
        case .markRead: markRead()
        case .chat: onSourceAction?(card.sourceKey, .chat)
        case .deck: onSourceAction?(card.sourceKey, .createDeck)
        case nil: break
        }
    }

    private func markRead() {
        guard !isRead else { return }
        markReadFeedbackTrigger += 1
        onMarkRead()
    }

    private func save() {
        guard !isSaving else { return }
        onSourceAction?(card.sourceKey, .addToKnowledge)
    }
}
