import SwiftUI

/// An Articles or Podcasts segment rendered as a card: a near-square hero with
/// the publisher and title over a dark fade, a length chip, the composed
/// summary, and a footer with the publication date, Mark read, and Save.
struct BriefingSourceCardView: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let model: BriefingSegmentRenderModel
    let card: BriefingSourceCard
    var savedSources: BriefingSavedSources?
    var contentTransitionNamespace: Namespace.ID? = nil
    let onOpenSource: (String) -> Void
    var onSourceAction: ((String, BriefingSourceAction) -> Void)?
    let onOpenDiscussion: (String) -> Void
    let onDig: (String, String) -> Void
    let onMarkRead: () -> Void

    @State private var markReadFeedbackTrigger = 0
    @State private var swipe = BriefingCardSwipe()
    /// The finger's starting height, where the Chat and Deck options appear.
    @State private var swipeAnchorY: CGFloat = 0

    /// The 16:9 artwork fills a 16:15 frame, so its height limits sharpness.
    /// The cache sizes by the longest side at a fixed 2x: 960pt decodes about
    /// 1,920×1,080px, which covers a 3x phone's ~1,020px hero height without
    /// decoding the 2,848px original.
    private static let heroTargetSize = CGSize(width: 960, height: 540)
    private static let heroAspectRatio: CGFloat = 16 / 15
    private static let stackedHeroAspectRatio: CGFloat = 16 / 9
    private static let overlayInk = Color(red: 1, green: 250 / 255, blue: 240 / 255)
    private static let scrim = Color(red: 20 / 255, green: 16 / 255, blue: 10 / 255)

    private var isRead: Bool { model.allSourcesRead }
    private var isSaved: Bool { savedSources?.isSaved(card.sourceKey) ?? false }
    private var isSaving: Bool { savedSources?.isPending(card.sourceKey) ?? false }
    /// Overlaid type must never clip, so accessibility sizes stack the title
    /// below a shorter image instead.
    private var stacksTitleBelowImage: Bool { dynamicTypeSize.isAccessibilitySize }

    var body: some View {
        cardBody
            .matchedContentZoomSource(id: card.contentId, namespace: contentTransitionNamespace)
            .opacity(swipe.cardOpacity)
            .scaleEffect(swipe.cardScale)
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
            .sensoryFeedback(.impact(weight: .medium), trigger: swipe.release) { previous, release in
                previous == nil && release != nil
            }
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("briefing.segment.\(model.segment.id)")
    }

    private var cardBody: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            // Read cards recede: the summary and footer fade and the hero
            // desaturates, while the overlaid title keeps its contrast.
            Group {
                summary
                footer
            }
            .opacity(isRead ? 0.72 : 1)
        }
        .clipShape(cardShape)
        .background {
            // Shadowing the plain shape, not the content, keeps scrolling cheap.
            cardShape
                .fill(Color.surfaceSecondary)
                .appShadow(colorScheme == .dark ? .none : .card)
        }
        .overlay {
            cardShape.strokeBorder(Color.borderSubtle, lineWidth: 1)
        }
        .animation(AppMotion.respectingReduceMotion(reduceMotion, AppMotion.readDim), value: isRead)
    }

    // MARK: Swipe

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
        case .markRead:
            markRead()
        case .chat:
            onSourceAction?(card.sourceKey, .chat)
        case .deck:
            onSourceAction?(card.sourceKey, .createDeck)
        case nil:
            break
        }
    }

    private var accessibilityState: String {
        [isRead ? "Read" : nil, isSaved ? "Saved to Knowledge" : nil]
            .compactMap { $0 }
            .joined(separator: ", ")
    }

    private var cardShape: RoundedRectangle {
        RoundedRectangle(cornerRadius: CornerRadius.card, style: .continuous)
    }

    // MARK: Header

    private var header: some View {
        Button {
            onOpenSource(card.sourceKey)
        } label: {
            if stacksTitleBelowImage {
                VStack(alignment: .leading, spacing: 0) {
                    hero(aspectRatio: Self.stackedHeroAspectRatio)
                    titleBlock(onImage: false)
                        .padding(.horizontal, 18)
                        .padding(.top, 16)
                }
            } else {
                hero(aspectRatio: Self.heroAspectRatio)
                    .overlay(alignment: .bottomLeading) {
                        titleBlock(onImage: true)
                            .padding(.horizontal, 18)
                            .padding(.top, 64)
                            .padding(.bottom, 16)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background { titleScrim }
                    }
            }
        }
        .buttonStyle(BriefingCardHeaderButtonStyle())
        .overlay(alignment: .topLeading) {
            if let length = card.length {
                lengthChip(length)
                    .padding(14)
                    .allowsHitTesting(false)
            }
        }
        .briefingSourceMenu(sourceKey: card.sourceKey, onAction: onSourceAction)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(card.accessibilityLabel)
        .accessibilityValue(accessibilityState)
        .accessibilityHint("Opens the full source")
        .accessibilityAddTraits([.isHeader, .isLink])
        .accessibilityActions {
            if !isRead {
                Button("Mark as read", action: markRead)
            }
            if !isSaved {
                Button("Save to Knowledge", action: save)
            }
            Button(BriefingSourceAction.chat.title) {
                onSourceAction?(card.sourceKey, .chat)
            }
            Button(BriefingSourceAction.createDeck.title) {
                onSourceAction?(card.sourceKey, .createDeck)
            }
        }
        .accessibilityIdentifier("briefing.card.header.\(model.segment.id)")
    }

    private func hero(aspectRatio: CGFloat) -> some View {
        Color.surfaceContainer
            .aspectRatio(aspectRatio, contentMode: .fit)
            .overlay {
                CachedAsyncImage(
                    url: card.imageURL,
                    thumbnailUrl: card.thumbnailURL,
                    targetSize: Self.heroTargetSize
                ) { image in
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                } placeholder: {
                    Color.surfaceContainer
                }
            }
            .saturation(isRead ? 0.2 : 1)
            .clipped()
            // The filled image overflows the frame; keep taps inside the card.
            .contentShape(Rectangle())
            .accessibilityHidden(true)
    }

    /// Sized by the title block itself, so a three-line title at a large
    /// text size still sits on a dark enough ground over any image.
    private var titleScrim: some View {
        LinearGradient(
            stops: [
                .init(color: Self.scrim.opacity(0), location: 0),
                .init(color: Self.scrim.opacity(0.62), location: 0.42),
                .init(color: Self.scrim.opacity(0.88), location: 1),
            ],
            startPoint: .top,
            endPoint: .bottom
        )
    }

    private func titleBlock(onImage: Bool) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if let publisher = card.publisher {
                Text(publisher.uppercased())
                    .font(.editorialMeta)
                    .tracking(1.6)
                    .foregroundStyle(onImage ? Self.overlayInk.opacity(0.86) : Color.onSurfaceSecondary)
                    .lineLimit(1)
            }
            Text(card.title)
                .font(.appSerif(size: 23, relativeTo: .title2, weight: .semibold))
                .foregroundStyle(onImage ? Self.overlayInk : Color.brandPrimary)
                .lineSpacing(1)
                .lineLimit(onImage ? 4 : nil)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
                .appShadow(onImage ? .overlayText : .none)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func lengthChip(_ length: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: card.kind == .podcast ? "waveform" : "doc.text")
                .font(.appSymbol(size: 11, weight: .semibold))
            Text(length)
                .font(.appCaption2.weight(.bold))
                .lineLimit(1)
        }
        .foregroundStyle(Color.onSurface)
        .padding(.horizontal, 11)
        .frame(minHeight: 30)
        .glassEffect(.floatingChrome, in: .capsule)
        .accessibilityHidden(true)
    }

    // MARK: Summary

    @ViewBuilder
    private var summary: some View {
        if !model.passageBlockIndices.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(model.passageBlockIndices, id: \.self) { index in
                    if let content = model.passageContentByBlockIndex[index] {
                        BriefingPassageView(
                            content: content,
                            onOpenSource: onOpenSource,
                            onOpenDiscussion: onOpenDiscussion,
                            onSourceAction: onSourceAction,
                            onDig: onDig
                        )
                    }
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 16)
        }
    }

    // MARK: Footer

    private var footer: some View {
        VStack(spacing: 0) {
            Rectangle()
                .fill(Color.outlineVariant)
                .frame(height: 1)
                .padding(.top, 14)

            HStack(spacing: 0) {
                Button {
                    onOpenSource(card.sourceKey)
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: card.kind == .podcast ? "waveform" : "doc.text")
                            .font(.appSymbol(size: 13, weight: .medium))
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .frame(width: 18)
                        Text(card.footerLine.uppercased())
                            .kicker()
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .briefingSourceMenu(sourceKey: card.sourceKey, onAction: onSourceAction)
                // The header already carries this metadata for VoiceOver.
                .accessibilityHidden(true)

                Button(action: markRead) {
                    Image(systemName: isRead ? "checkmark.circle.fill" : "checkmark.circle")
                        .font(.appSymbol(size: 20))
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .contentTransition(.symbolEffect(.replace))
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(isRead ? "Marked as read" : "Mark as read")
                .accessibilityIdentifier("briefing.card.mark_read.\(model.segment.id)")

                Button(action: save) {
                    Group {
                        if isSaving {
                            ProgressView()
                                .controlSize(.small)
                                .tint(Color.onSurfaceSecondary)
                        } else {
                            KnowledgeSaveIcon(
                                isSaved: isSaved,
                                size: 18,
                                unsavedColor: .onSurfaceSecondary,
                                badgeForegroundColor: .surfaceSecondary
                            )
                        }
                    }
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(isSaving)
                .accessibilityLabel(isSaved ? "Saved to Knowledge" : "Save to Knowledge")
                .accessibilityIdentifier("briefing.card.save.\(model.segment.id)")
            }
            .padding(.leading, 18)
            .padding(.trailing, 8)
            .padding(.vertical, 2)
        }
    }

    // MARK: Actions

    private func markRead() {
        // Read is one-way; the server owns retirement on the next refresh.
        guard !isRead else { return }
        markReadFeedbackTrigger += 1
        onMarkRead()
    }

    private func save() {
        guard !isSaving else { return }
        onSourceAction?(card.sourceKey, .addToKnowledge)
    }
}

/// Dims the hero while pressed. Scaling only the header would pull it away
/// from the card edges, so it darkens instead.
private struct BriefingCardHeaderButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .overlay {
                Color.black
                    .opacity(configuration.isPressed ? 0.12 : 0)
                    .allowsHitTesting(false)
            }
            .animation(AppMotion.subtle, value: configuration.isPressed)
    }
}
