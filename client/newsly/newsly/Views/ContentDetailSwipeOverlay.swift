//
//  ContentDetailSwipeOverlay.swift
//  newsly
//

import SwiftUI
import UIKit
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
        itemCount: Int
    ) -> DetailSwipeAction {
        guard isHorizontalSwipe(translation, threshold: actionThreshold) else {
            return .ignore
        }

        switch origin {
        case .leadingEdge where translation.width > actionThreshold && currentIndex > 0:
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

struct ContentDetailSwipeOverlay: View {
    let currentIndex: Int
    let contentIds: [Int]
    let surfaceName: String
    let edgeWidth: CGFloat
    @Binding var dragAmount: CGFloat
    @Binding var isLeadingEdgeSwipeActive: Bool
    let onDismiss: () -> Void
    let onNext: () -> Void
    let onPrevious: () -> Void

    @State private var didTriggerHaptic = false

    var body: some View {
        GeometryReader { proxy in
            HStack(spacing: 0) {
                swipeHitArea(origin: .leadingEdge, viewportWidth: proxy.size.width)

                Spacer(minLength: 0)

                swipeHitArea(origin: .trailingEdge, viewportWidth: proxy.size.width)
            }
        }
    }

    private func swipeHitArea(origin: DetailSwipeOrigin, viewportWidth: CGFloat) -> some View {
        Color.clear
            .frame(width: edgeWidth)
            .contentShape(Rectangle())
            .simultaneousGesture(swipeGesture(origin: origin, viewportWidth: viewportWidth))
    }

    private func swipeGesture(origin: DetailSwipeOrigin, viewportWidth: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 50, coordinateSpace: .local)
            .onChanged { value in
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
                    let generator = UIImpactFeedbackGenerator(style: .light)
                    generator.impactOccurred()
                    didTriggerHaptic = true
                }
            }
            .onEnded { value in
                didTriggerHaptic = false
                isLeadingEdgeSwipeActive = false

                let action = DetailSwipePolicy.endAction(
                    origin: origin,
                    translation: value.translation,
                    currentIndex: currentIndex,
                    itemCount: contentIds.count
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
        withAnimation(.easeOut(duration: 0.2)) {
            dragAmount = viewportWidth
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
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
        withAnimation(.easeOut(duration: 0.2)) {
            dragAmount = -viewportWidth
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
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
        withAnimation(.easeOut(duration: 0.2)) {
            dragAmount = viewportWidth
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
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
        withAnimation(.interactiveSpring(response: 0.3, dampingFraction: 0.8)) {
            dragAmount = 0
        }
    }

    private func triggerCompletionHaptic() {
        let generator = UIImpactFeedbackGenerator(style: .medium)
        generator.impactOccurred()
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
}

private let detailSwipeLogger = Logger(subsystem: "com.newsly", category: "ContentDetailView")
