//
//  ContentDetailSwipeOverlay.swift
//  newsly
//

import SwiftUI
import os.log

enum DetailSwipeOrigin: String {
    case leadingEdge
    case trailingEdge
}

enum DetailSwipeAction: Equatable {
    case ignore
    case dismiss
    case previous
    case next
}

enum DetailSwipePolicy {
    private static let previewThreshold: CGFloat = 30
    private static let actionThreshold: CGFloat = 80

    static func dragOffset(
        origin: DetailSwipeOrigin,
        translation: CGSize,
        currentIndex: Int,
        itemCount: Int,
        canDismiss: Bool = true
    ) -> CGFloat? {
        guard isHorizontalSwipe(translation, threshold: previewThreshold) else {
            return nil
        }

        switch origin {
        case .leadingEdge where translation.width > previewThreshold && (currentIndex > 0 || canDismiss):
            return translation.width * 0.6
        case .trailingEdge where translation.width < -previewThreshold && currentIndex < itemCount - 1:
            return translation.width * 0.6
        default:
            return nil
        }
    }

    static func endAction(
        origin: DetailSwipeOrigin,
        translation: CGSize,
        currentIndex: Int,
        itemCount: Int,
        canDismiss: Bool = true
    ) -> DetailSwipeAction {
        guard isHorizontalSwipe(translation, threshold: actionThreshold) else {
            return .ignore
        }

        switch origin {
        case .leadingEdge
            where translation.width > actionThreshold
                && currentIndex > 0:
            return .previous
        case .leadingEdge where translation.width > actionThreshold && canDismiss:
            return .dismiss
        case .trailingEdge where translation.width < -actionThreshold && currentIndex < itemCount - 1:
            return .next
        default:
            return .ignore
        }
    }

    private static func isHorizontalSwipe(_ translation: CGSize, threshold: CGFloat) -> Bool {
        let horizontalAmount = abs(translation.width)
        let verticalAmount = abs(translation.height)
        return horizontalAmount > verticalAmount * 2 && horizontalAmount > threshold
    }
}

struct ContentDetailSwipeContainer<Content: View>: View {
    private struct SwipeDragState: Equatable {
        var offset: CGFloat = 0
        var isLeadingEdgeActive = false
    }

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let currentIndex: Int
    let contentIds: [Int]
    let surfaceName: String
    let edgeWidth: CGFloat
    let topHitExclusionHeight: CGFloat
    /// Nil where the detail has nothing to return to (the open display's
    /// pane root), so a leading-edge swipe does nothing there.
    let onDismiss: (() -> Void)?
    let onNext: () -> Void
    let onPrevious: () -> Void
    let content: Content

    @GestureState private var dragState = SwipeDragState()
    @State private var exitOffset: CGFloat = 0
    @State private var didTriggerHaptic = false
    @State private var thresholdFeedbackTrigger = 0
    @State private var completionFeedbackTrigger = 0

    init(
        currentIndex: Int,
        contentIds: [Int],
        surfaceName: String,
        edgeWidth: CGFloat,
        topHitExclusionHeight: CGFloat = 0,
        onDismiss: (() -> Void)?,
        onNext: @escaping () -> Void,
        onPrevious: @escaping () -> Void,
        @ViewBuilder content: () -> Content
    ) {
        self.currentIndex = currentIndex
        self.contentIds = contentIds
        self.surfaceName = surfaceName
        self.edgeWidth = edgeWidth
        self.topHitExclusionHeight = topHitExclusionHeight
        self.onDismiss = onDismiss
        self.onNext = onNext
        self.onPrevious = onPrevious
        self.content = content()
    }

    var body: some View {
        GeometryReader { proxy in
            content
                .overlay(alignment: .leading) {
                    if dragState.offset > 30 && (currentIndex > 0 || dragState.isLeadingEdgeActive) {
                        swipeIndicator(direction: .previous, progress: min(1.0, dragState.offset / 100))
                    }
                }
                .overlay(alignment: .trailing) {
                    if dragState.offset < -30 && currentIndex < contentIds.count - 1 {
                        swipeIndicator(direction: .next, progress: min(1.0, abs(dragState.offset) / 100))
                    }
                }
                .simultaneousGesture(swipeGesture(viewportWidth: proxy.size.width))
                .offset(x: dragState.offset)
                .animation(
                    dragState.offset == 0
                        ? AppMotion.respectingReduceMotion(
                            reduceMotion,
                            exitOffset != 0 ? AppMotion.subtle : AppMotion.press
                        )
                        : nil,
                    value: dragState.offset
                )
                .offset(x: exitOffset)
                .onChange(of: dragState.offset) { _, newOffset in
                    handleDragOffsetChange(newOffset)
                }
                .sensoryFeedback(.impact(weight: .light), trigger: thresholdFeedbackTrigger)
                .sensoryFeedback(.impact(weight: .medium), trigger: completionFeedbackTrigger)
        }
    }

    private func handleDragOffsetChange(_ newOffset: CGFloat) {
        if newOffset == 0 {
            didTriggerHaptic = false
        } else if abs(newOffset) > 80 && !didTriggerHaptic {
            thresholdFeedbackTrigger += 1
            didTriggerHaptic = true
        }
    }

    /// Edge swipes are recognized by where the touch STARTS rather than via
    /// hit-testable edge strips overlaid on the content: an overlaid strip
    /// swallows plain taps, making buttons under it (e.g. the trailing
    /// action-bar icon) untappable.
    private func swipeOrigin(
        startLocation: CGPoint,
        viewportWidth: CGFloat
    ) -> DetailSwipeOrigin? {
        guard startLocation.y >= topHitExclusionHeight else { return nil }
        if startLocation.x <= edgeWidth {
            return .leadingEdge
        }
        if startLocation.x >= viewportWidth - edgeWidth {
            return .trailingEdge
        }
        return nil
    }

    private func swipeGesture(viewportWidth: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 50, coordinateSpace: .local)
            .updating($dragState) { value, state, _ in
                guard let origin = swipeOrigin(
                    startLocation: value.startLocation,
                    viewportWidth: viewportWidth
                ),
                let newOffset = DetailSwipePolicy.dragOffset(
                    origin: origin,
                    translation: value.translation,
                    currentIndex: currentIndex,
                    itemCount: contentIds.count,
                    canDismiss: onDismiss != nil
                ) else {
                    state = SwipeDragState()
                    return
                }

                state = SwipeDragState(
                    offset: newOffset,
                    isLeadingEdgeActive: origin == .leadingEdge && newOffset > 0
                )
            }
            .onEnded { value in
                guard let origin = swipeOrigin(
                    startLocation: value.startLocation,
                    viewportWidth: viewportWidth
                ) else {
                    return
                }

                let action = DetailSwipePolicy.endAction(
                    origin: origin,
                    translation: value.translation,
                    currentIndex: currentIndex,
                    itemCount: contentIds.count,
                    canDismiss: onDismiss != nil
                )

                switch action {
                case .dismiss:
                    completeDismissSwipe(origin: origin, value: value, viewportWidth: viewportWidth)
                case .next:
                    completeNextSwipe(origin: origin, value: value, viewportWidth: viewportWidth)
                case .previous:
                    completePreviousSwipe(origin: origin, value: value, viewportWidth: viewportWidth)
                case .ignore:
                    logIgnoredSwipeIfNeeded(origin: origin, value: value)
                }
            }
    }

    private func completeDismissSwipe(
        origin: DetailSwipeOrigin,
        value: DragGesture.Value,
        viewportWidth: CGFloat
    ) {
        logSwipeDecision("dismiss", origin: origin, value: value)
        triggerCompletionHaptic()
        withAnimation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle),
            completionCriteria: .logicallyComplete
        ) {
            exitOffset = viewportWidth
        } completion: {
            onDismiss?()
            var reset = Transaction()
            reset.disablesAnimations = true
            withTransaction(reset) {
                exitOffset = 0
            }
        }
    }

    private func completeNextSwipe(
        origin: DetailSwipeOrigin,
        value: DragGesture.Value,
        viewportWidth: CGFloat
    ) {
        logSwipeDecision("next", origin: origin, value: value)
        triggerCompletionHaptic()
        withAnimation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle),
            completionCriteria: .logicallyComplete
        ) {
            exitOffset = -viewportWidth
        } completion: {
            resetExitOffsetWithoutAnimation()
            onNext()
        }
    }

    private func completePreviousSwipe(
        origin: DetailSwipeOrigin,
        value: DragGesture.Value,
        viewportWidth: CGFloat
    ) {
        logSwipeDecision("previous", origin: origin, value: value)
        triggerCompletionHaptic()
        withAnimation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle),
            completionCriteria: .logicallyComplete
        ) {
            exitOffset = viewportWidth
        } completion: {
            resetExitOffsetWithoutAnimation()
            onPrevious()
        }
    }

    private func logIgnoredSwipeIfNeeded(origin: DetailSwipeOrigin, value: DragGesture.Value) {
        if DetailSwipePolicy.dragOffset(
            origin: origin,
            translation: value.translation,
            currentIndex: currentIndex,
            itemCount: contentIds.count,
            canDismiss: onDismiss != nil
        ) != nil {
            logSwipeDecision("snap_back", origin: origin, value: value)
        }
    }

    private func triggerCompletionHaptic() {
        completionFeedbackTrigger += 1
    }

    private func resetExitOffsetWithoutAnimation() {
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            exitOffset = 0
        }
    }

    private func logSwipeDecision(
        _ decision: String,
        origin: DetailSwipeOrigin,
        value: DragGesture.Value
    ) {
        detailSwipeLogger.info(
            "[DetailSwipe] decision=\(decision, privacy: .public) surface=\(surfaceName, privacy: .public) contentId=\(contentIdLogValue, privacy: .public) index=\(currentIndex, privacy: .public) idsCount=\(contentIds.count, privacy: .public) origin=\(origin.rawValue, privacy: .public) translationX=\(Int(value.translation.width), privacy: .public) translationY=\(Int(value.translation.height), privacy: .public) edgeWidth=\(Int(edgeWidth), privacy: .public)"
        )
    }

    private var contentIdLogValue: String {
        guard currentIndex >= 0, currentIndex < contentIds.count else {
            return "nil"
        }
        return String(contentIds[currentIndex])
    }

    @ViewBuilder
    private func swipeIndicator(direction: SwipeIndicatorDirection, progress: CGFloat) -> some View {
        let iconName = direction == .previous ? "chevron.left" : "chevron.right"

        VStack {
            Spacer()
            HStack {
                if direction == .next { Spacer() }
                Image(systemName: iconName)
                    .font(.appSymbol(size: 24, weight: .semibold))
                    .foregroundColor(Color.surfacePrimary)
                    .frame(width: 44, height: 44)
                    .background(
                        Circle()
                            .fill(Color.brandPrimary.opacity(0.9))
                    )
                    .scaleEffect(0.8 + (progress * 0.4))
                    .opacity(Double(progress))
                    .padding(.horizontal, 8)
                if direction == .previous { Spacer() }
            }
            Spacer()
        }
    }
}

private enum SwipeIndicatorDirection {
    case previous
    case next
}

private let detailSwipeLogger = Logger(subsystem: "com.newsly", category: "ContentDetailView")
