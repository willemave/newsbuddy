import SwiftUI
import UIKit

/// What a card swipe does when the finger lifts.
enum BriefingCardSwipeRelease: Equatable {
    case markRead
    case chat
    case deck
}

/// The two options a left-to-right swipe offers, chosen by moving the finger up or down.
enum BriefingCardSwipeOption: Equatable, CaseIterable {
    case chat
    case deck

    var title: String {
        switch self {
        case .chat: BriefingSourceAction.chat.title
        case .deck: "Deck"
        }
    }

    var systemImage: String {
        switch self {
        case .chat: BriefingSourceAction.chat.systemImage
        case .deck: BriefingSourceAction.createDeck.systemImage
        }
    }
}

/// Pure swipe geometry for one card. A right-to-left drag marks the card read; a
/// left-to-right drag reveals Chat above and Deck below the finger's starting height, and the
/// finger's vertical movement picks one. Nothing runs unless the drag is past its threshold
/// when the finger lifts.
struct BriefingCardSwipe: Equatable {
    static let markReadDistance: CGFloat = 72
    static let optionsDistance: CGFloat = 56
    static let selectionDeadZone: CGFloat = 18
    /// How far the card follows a drag before resisting.
    static let cardTravel: CGFloat = 120
    /// The card's opacity at full progress, so the actions set into the background show
    /// through it.
    static let revealedCardOpacity: Double = 0.12
    /// The card shrinks a little as it is dragged, as if lifted off the page.
    static let liftedCardScale: CGFloat = 0.9

    enum Phase: Equatable {
        case idle
        case markRead(armed: Bool)
        case options(selection: BriefingCardSwipeOption?, armed: Bool)
    }

    var translation: CGSize = .zero

    var phase: Phase {
        if translation.width < 0 {
            return .markRead(armed: -translation.width >= Self.markReadDistance)
        }
        if translation.width > 0 {
            let selection = selection
            return .options(
                selection: selection,
                armed: selection != nil && translation.width >= Self.optionsDistance
            )
        }
        return .idle
    }

    var selection: BriefingCardSwipeOption? {
        guard translation.width > 0 else { return nil }
        if translation.height <= -Self.selectionDeadZone { return .chat }
        if translation.height >= Self.selectionDeadZone { return .deck }
        return nil
    }

    /// 0 at rest, 1 once the active direction's threshold is reached.
    var progress: CGFloat {
        if translation.width < 0 {
            return min(-translation.width / Self.markReadDistance, 1)
        }
        if translation.width > 0 {
            return min(translation.width / Self.optionsDistance, 1)
        }
        return 0
    }

    /// Horizontal card offset: follows the finger, then resists past `cardTravel`.
    var cardOffset: CGFloat {
        let travel = abs(translation.width)
        let limit = Self.cardTravel
        let resisted = travel <= limit ? travel : limit + (travel - limit) * 0.3
        return translation.width < 0 ? -resisted : resisted
    }

    /// Eases from full size to `liftedCardScale` as the drag reaches its threshold.
    var cardScale: CGFloat {
        let eased = 1 - pow(1 - progress, 2)
        return 1 - (1 - Self.liftedCardScale) * eased
    }

    /// The card turns translucent as the drag progresses, revealing the actions behind it.
    var cardOpacity: Double {
        1 - (1 - Self.revealedCardOpacity) * Double(progress)
    }

    var release: BriefingCardSwipeRelease? {
        switch phase {
        case .markRead(armed: true): .markRead
        case .options(selection: .chat, armed: true): .chat
        case .options(selection: .deck, armed: true): .deck
        default: nil
        }
    }
}

/// A one-finger horizontal pan that wins over the lens pager when it starts on a card.
/// Vertical drags fail it immediately, so the list keeps scrolling.
struct BriefingCardSwipeGesture: UIGestureRecognizerRepresentable {
    let onChanged: (_ translation: CGSize, _ start: CGPoint) -> Void
    let onEnded: (_ translation: CGSize, _ cancelled: Bool) -> Void

    func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator {
        Coordinator()
    }

    func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
        let recognizer = UIPanGestureRecognizer()
        recognizer.maximumNumberOfTouches = 1
        recognizer.delegate = context.coordinator
        return recognizer
    }

    func handleUIGestureRecognizerAction(
        _ recognizer: UIPanGestureRecognizer,
        context: Context
    ) {
        let point = recognizer.translation(in: recognizer.view)
        let translation = CGSize(width: point.x, height: point.y)
        switch recognizer.state {
        case .began:
            let location = context.converter.localLocation
            context.coordinator.start = CGPoint(
                x: location.x - point.x,
                y: location.y - point.y
            )
            onChanged(translation, context.coordinator.start)
        case .changed:
            onChanged(translation, context.coordinator.start)
        case .ended:
            onEnded(translation, false)
        case .cancelled, .failed:
            onEnded(translation, true)
        default:
            break
        }
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var start: CGPoint = .zero

        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            guard let pan = gestureRecognizer as? UIPanGestureRecognizer else { return false }
            let velocity = pan.velocity(in: pan.view)
            return abs(velocity.x) > abs(velocity.y) * 1.3
        }

        /// The lens pager pages with a paging scroll view; it waits for a card swipe to fail.
        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldBeRequiredToFailBy otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            (otherGestureRecognizer.view as? UIScrollView)?.isPagingEnabled == true
        }
    }
}

/// The actions set into the card's background, on the side the finger moves toward. As the
/// card slides and turns translucent, large panels show through: one "Read" panel on the left
/// half for a right-to-left drag, or Chat above and Deck below the finger's starting height
/// on the right half for a left-to-right drag. The
/// panel block spans 60% of the card's height, so each option is 30%, within bounds that
/// keep it on screen when a card is taller than the display.
struct BriefingCardSwipeBackdrop: View {
    let phase: BriefingCardSwipe.Phase
    let progress: CGFloat
    let isRead: Bool
    /// The finger's starting height in card coordinates.
    let anchorY: CGFloat

    static let blockFraction: CGFloat = 0.6
    static let widthFraction: CGFloat = 0.5
    static let optionHeightRange: ClosedRange<CGFloat> = 88...260
    private static let spacing: CGFloat = 10
    private static let inset: CGFloat = 12

    var body: some View {
        RoundedRectangle(cornerRadius: CornerRadius.card, style: .continuous)
            .fill(Color.surfaceContainer)
            .overlay {
                GeometryReader { geometry in
                    let size = geometry.size
                    let optionHeight = min(
                        max(size.height * Self.blockFraction / 2, Self.optionHeightRange.lowerBound),
                        Self.optionHeightRange.upperBound
                    )
                    let blockHeight = optionHeight * 2 + Self.spacing
                    let maxY = max(size.height - blockHeight - Self.inset, Self.inset)
                    let top = min(max(anchorY - blockHeight / 2, Self.inset), maxY)
                    let width = size.width * Self.widthFraction
                    actions(optionHeight: optionHeight)
                        .frame(width: width, height: blockHeight)
                        .offset(x: leadingX(cardWidth: size.width, width: width), y: top)
                }
            }
            .accessibilityHidden(true)
    }

    private func leadingX(cardWidth: CGFloat, width: CGFloat) -> CGFloat {
        if case .markRead = phase {
            return Self.inset
        }
        return cardWidth - width - Self.inset
    }

    @ViewBuilder
    private func actions(optionHeight: CGFloat) -> some View {
        switch phase {
        case .markRead(let armed):
            panel(
                title: "Read",
                systemImage: armed || isRead ? "checkmark.circle.fill" : "checkmark.circle",
                isSelected: true,
                isArmed: armed
            )
        case .options(let selection, let armed):
            VStack(spacing: Self.spacing) {
                ForEach(BriefingCardSwipeOption.allCases, id: \.self) { option in
                    panel(
                        title: option.title,
                        systemImage: option.systemImage,
                        isSelected: option == selection,
                        isArmed: armed && option == selection
                    )
                    .frame(height: optionHeight)
                }
            }
        case .idle:
            EmptyView()
        }
    }

    private func panel(
        title: String,
        systemImage: String,
        isSelected: Bool,
        isArmed: Bool
    ) -> some View {
        RoundedRectangle(cornerRadius: CornerRadius.card - 6, style: .continuous)
            // The one accent marks the choice; unchosen options stay a quiet neutral.
            .fill(isSelected ? Color.brandPrimary.opacity(isArmed ? 1 : 0.82) : Color.surfaceContainerHigh)
            .overlay {
                VStack(spacing: 10) {
                    Image(systemName: systemImage)
                        .font(.appSymbol(size: 34, weight: .medium))
                        .contentTransition(.symbolEffect(.replace))
                    Text(title)
                        .font(.appTitle3)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
                .foregroundStyle(isSelected ? Color.surfacePrimary : Color.onSurface)
            }
            .scaleEffect(isArmed ? 1 : 0.94 + 0.04 * progress)
            .opacity(Double(0.35 + 0.65 * progress))
            .animation(AppMotion.press, value: isSelected)
            .animation(AppMotion.press, value: isArmed)
    }
}
