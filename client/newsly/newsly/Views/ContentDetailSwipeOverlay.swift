//
//  ContentDetailSwipeOverlay.swift
//  newsly
//

import SwiftUI
import os.log

enum DetailSwipeOrigin: String {
    case content
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
        itemCount: Int
    ) -> CGFloat? {
        guard isHorizontalSwipe(translation, threshold: previewThreshold) else {
            return nil
        }

        switch origin {
        case .leadingEdge where translation.width > previewThreshold:
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
        leadingEdgePreviousEnabled: Bool = true
    ) -> DetailSwipeAction {
        guard isHorizontalSwipe(translation, threshold: actionThreshold) else {
            return .ignore
        }

        switch origin {
        case .leadingEdge
            where translation.width > actionThreshold
                && leadingEdgePreviousEnabled
                && currentIndex > 0:
            return .previous
        case .leadingEdge where translation.width > actionThreshold:
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
    let currentIndex: Int
    let contentIds: [Int]
    let surfaceName: String
    let edgeWidth: CGFloat
    let topHitExclusionHeight: CGFloat
    let leadingEdgePreviousEnabled: Bool
    let onDismiss: () -> Void
    let onNext: () -> Void
    let onPrevious: () -> Void
    let content: Content

    @State private var dragAmount: CGFloat = 0
    @State private var isLeadingEdgeSwipeActive = false
    @State private var didTriggerHaptic = false
    @State private var thresholdFeedbackTrigger = 0
    @State private var completionFeedbackTrigger = 0

    init(
        currentIndex: Int,
        contentIds: [Int],
        surfaceName: String,
        edgeWidth: CGFloat,
        topHitExclusionHeight: CGFloat = 0,
        leadingEdgePreviousEnabled: Bool = true,
        onDismiss: @escaping () -> Void,
        onNext: @escaping () -> Void,
        onPrevious: @escaping () -> Void,
        @ViewBuilder content: () -> Content
    ) {
        self.currentIndex = currentIndex
        self.contentIds = contentIds
        self.surfaceName = surfaceName
        self.edgeWidth = edgeWidth
        self.topHitExclusionHeight = topHitExclusionHeight
        self.leadingEdgePreviousEnabled = leadingEdgePreviousEnabled
        self.onDismiss = onDismiss
        self.onNext = onNext
        self.onPrevious = onPrevious
        self.content = content()
    }

    var body: some View {
        GeometryReader { proxy in
            content
                .overlay(alignment: .leading) {
                    if dragAmount > 30 && (currentIndex > 0 || isLeadingEdgeSwipeActive) {
                        swipeIndicator(direction: .previous, progress: min(1.0, dragAmount / 100))
                    }
                }
                .overlay(alignment: .trailing) {
                    if dragAmount < -30 && currentIndex < contentIds.count - 1 {
                        swipeIndicator(direction: .next, progress: min(1.0, abs(dragAmount) / 100))
                    }
                }
                .simultaneousGesture(swipeGesture(viewportWidth: proxy.size.width))
                .offset(x: dragAmount)
                .animation(AppMotion.press, value: dragAmount)
                .sensoryFeedback(.impact(weight: .light), trigger: thresholdFeedbackTrigger)
                .sensoryFeedback(.impact(weight: .medium), trigger: completionFeedbackTrigger)
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
            .onChanged { value in
                guard let origin = swipeOrigin(
                    startLocation: value.startLocation,
                    viewportWidth: viewportWidth
                ) else {
                    return
                }

                guard let newOffset = DetailSwipePolicy.dragOffset(
                    origin: origin,
                    translation: value.translation,
                    currentIndex: currentIndex,
                    itemCount: contentIds.count
                ) else {
                    return
                }

                isLeadingEdgeSwipeActive = origin == .leadingEdge && newOffset > 0
                dragAmount = newOffset

                if abs(newOffset) > 80 && !didTriggerHaptic {
                    thresholdFeedbackTrigger += 1
                    didTriggerHaptic = true
                }
            }
            .onEnded { value in
                didTriggerHaptic = false
                isLeadingEdgeSwipeActive = false

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
                    leadingEdgePreviousEnabled: leadingEdgePreviousEnabled
                )

                switch action {
                case .dismiss:
                    completeDismissSwipe(origin: origin, value: value, viewportWidth: viewportWidth)
                case .next:
                    completeNextSwipe(origin: origin, value: value, viewportWidth: viewportWidth)
                case .previous:
                    completePreviousSwipe(origin: origin, value: value, viewportWidth: viewportWidth)
                case .ignore:
                    snapBackAfterIgnoredSwipe(origin: origin, value: value)
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
        withAnimation(AppMotion.subtle, completionCriteria: .logicallyComplete) {
            dragAmount = viewportWidth
        } completion: {
            onDismiss()
        }
    }

    private func completeNextSwipe(
        origin: DetailSwipeOrigin,
        value: DragGesture.Value,
        viewportWidth: CGFloat
    ) {
        logSwipeDecision("next", origin: origin, value: value)
        triggerCompletionHaptic()
        withAnimation(AppMotion.subtle, completionCriteria: .logicallyComplete) {
            dragAmount = -viewportWidth
        } completion: {
            resetDragWithoutAnimation()
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
        withAnimation(AppMotion.subtle, completionCriteria: .logicallyComplete) {
            dragAmount = viewportWidth
        } completion: {
            resetDragWithoutAnimation()
            onPrevious()
        }
    }

    private func snapBackAfterIgnoredSwipe(origin: DetailSwipeOrigin, value: DragGesture.Value) {
        if DetailSwipePolicy.dragOffset(
            origin: origin,
            translation: value.translation,
            currentIndex: currentIndex,
            itemCount: contentIds.count
        ) != nil {
            logSwipeDecision("snap_back", origin: origin, value: value)
        }
        withAnimation(AppMotion.press) {
            dragAmount = 0
        }
    }

    private func triggerCompletionHaptic() {
        completionFeedbackTrigger += 1
    }

    private func resetDragWithoutAnimation() {
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            dragAmount = 0
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
                    .foregroundColor(.white)
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
