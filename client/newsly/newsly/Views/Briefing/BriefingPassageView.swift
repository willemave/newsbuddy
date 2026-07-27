import SwiftUI
import UIKit

struct BriefingPassageView: UIViewRepresentable {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.colorSchemeContrast) private var accessibilityContrast

    let content: BriefingAttributedTextBuilder.Result
    var floatingExclusionSize: CGSize? = nil
    let onOpenSource: (String) -> Void
    var onOpenDiscussion: (String) -> Void = { _ in }
    let onDig: (String, String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(
            onOpenSource: onOpenSource,
            onOpenDiscussion: onOpenDiscussion
        )
    }

    func makeUIView(context: Context) -> DigDeeperTextView {
        let textView = DigDeeperTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.isScrollEnabled = false
        textView.adjustsFontForContentSizeCategory = true
        textView.backgroundColor = .clear
        textView.textContainerInset = .zero
        textView.textContainer.lineFragmentPadding = 0
        textView.delegate = context.coordinator
        textView.tintColor = UIColor.appAccent
        textView.linkTextAttributes = [
            .foregroundColor: UIColor.appAccent
        ]
        textView.digSelectionNormalizer = BriefingDigSelectionPolicy.normalize
        return textView
    }

    func updateUIView(_ uiView: DigDeeperTextView, context: Context) {
        context.coordinator.onOpenSource = onOpenSource
        context.coordinator.onOpenDiscussion = onOpenDiscussion
        uiView.floatingExclusionSize = floatingExclusionSize
        if let scaledText = context.coordinator.scaledTextIfNeeded(
            content.attributedText,
            compatibleWith: uiView.traitCollection
        ) {
            uiView.attributedText = scaledText
            context.coordinator.measurement = nil
            context.coordinator.contentRevision += 1
            uiView.invalidateIntrinsicContentSize()
        }
        uiView.onDigDeeper = { selection in
            onDig(selection, content.plainText)
        }
    }

    func sizeThatFits(
        _ proposal: ProposedViewSize,
        uiView: DigDeeperTextView,
        context: Context
    ) -> CGSize? {
        guard let width = proposal.width, width.isFinite, width > 0 else { return nil }
        let measurementFingerprint = Coordinator.MeasurementFingerprint(
            contentRevision: context.coordinator.contentRevision,
            width: width,
            floatingExclusionSize: floatingExclusionSize,
            dynamicTypeSize: dynamicTypeSize,
            colorScheme: colorScheme,
            accessibilityContrast: accessibilityContrast
        )
        if let measurement = context.coordinator.measurement,
           measurement.fingerprint == measurementFingerprint {
            return measurement.size
        }
        if abs(uiView.bounds.width - width) > .ulpOfOne {
            uiView.bounds.size.width = width
        }
        let signpostState = BriefingPerformance.signposter.beginInterval("passage-measurement")
        uiView.updateFloatingExclusion(forWidth: width)
        let fittingSize = uiView.sizeThatFits(
            CGSize(width: width, height: .greatestFiniteMagnitude)
        )
        let minimumHeight = floatingExclusionSize?.height ?? 0
        let size = CGSize(width: width, height: max(fittingSize.height, minimumHeight))
        BriefingPerformance.signposter.endInterval("passage-measurement", signpostState)
        context.coordinator.measurement = (measurementFingerprint, size)
        return size
    }

    static func scaledAttributedText(
        _ attributedText: NSAttributedString,
        compatibleWith traitCollection: UITraitCollection
    ) -> NSAttributedString {
        let scaledText = NSMutableAttributedString(attributedString: attributedText)
        let fullRange = NSRange(location: 0, length: scaledText.length)
        scaledText.enumerateAttribute(.font, in: fullRange) { value, range, _ in
            guard let font = value as? UIFont else { return }
            let scaledFont = UIFontMetrics(forTextStyle: .callout).scaledFont(
                for: font,
                compatibleWith: traitCollection
            )
            scaledText.addAttribute(.font, value: scaledFont, range: range)
        }
        return scaledText
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        private struct RenderFingerprint: Equatable {
            let contentIdentity: ObjectIdentifier
            let contentSizeCategory: UIContentSizeCategory
        }

        struct MeasurementFingerprint: Equatable {
            let contentRevision: Int
            let width: CGFloat
            let floatingExclusionSize: CGSize?
            let dynamicTypeSize: DynamicTypeSize
            let colorScheme: ColorScheme
            let accessibilityContrast: ColorSchemeContrast
        }

        var onOpenSource: (String) -> Void
        var onOpenDiscussion: (String) -> Void
        var contentRevision = 0
        var measurement: (fingerprint: MeasurementFingerprint, size: CGSize)?
        private var renderFingerprint: RenderFingerprint?

        init(
            onOpenSource: @escaping (String) -> Void,
            onOpenDiscussion: @escaping (String) -> Void
        ) {
            self.onOpenSource = onOpenSource
            self.onOpenDiscussion = onOpenDiscussion
        }

        func scaledTextIfNeeded(
            _ attributedText: NSAttributedString,
            compatibleWith traitCollection: UITraitCollection
        ) -> NSAttributedString? {
            let fingerprint = RenderFingerprint(
                contentIdentity: ObjectIdentifier(attributedText),
                contentSizeCategory: traitCollection.preferredContentSizeCategory
            )
            guard renderFingerprint != fingerprint else { return nil }

            let signpostState = BriefingPerformance.signposter.beginInterval("passage-scaling")
            let scaledText = BriefingPassageView.scaledAttributedText(
                attributedText,
                compatibleWith: traitCollection
            )
            BriefingPerformance.signposter.endInterval("passage-scaling", signpostState)
            renderFingerprint = fingerprint
            return scaledText
        }

        func textView(
            _ textView: UITextView,
            primaryActionFor textItem: UITextItem,
            defaultAction: UIAction
        ) -> UIAction? {
            guard case .link(let url) = textItem.content,
                  isBriefingLink(url)
            else { return defaultAction }
            // Customize only activation; UIKit retains its native long-press menu.
            return UIAction(
                title: defaultAction.title,
                image: defaultAction.image
            ) { [weak self] _ in
                self?.openBriefingLink(url)
            }
        }

        func openBriefingLink(_ url: URL) {
            if let sourceKey = discussionSourceKey(from: url) {
                onOpenDiscussion(sourceKey)
                return
            }
            if let sourceKey = sourceKey(from: url) {
                onOpenSource(sourceKey)
            }
        }

        private func isBriefingLink(_ url: URL) -> Bool {
            discussionSourceKey(from: url) != nil || sourceKey(from: url) != nil
        }

        private func sourceKey(from url: URL) -> String? {
            guard (url.scheme == "newsly" || url.scheme == "news"), url.host == "briefing" else {
                return nil
            }
            let components = url.pathComponents.filter { $0 != "/" }
            guard components.count == 2 else { return nil }
            return "\(components[0]):\(components[1])"
        }

        private func discussionSourceKey(from url: URL) -> String? {
            guard (url.scheme == "newsly" || url.scheme == "news"), url.host == "briefing" else {
                return nil
            }
            let components = url.pathComponents.filter { $0 != "/" }
            guard components.count == 3, components[0] == "discussion" else { return nil }
            return "\(components[1]):\(components[2])"
        }
    }
}
