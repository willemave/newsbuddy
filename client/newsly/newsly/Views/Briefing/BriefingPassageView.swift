import SwiftUI
import UIKit

struct BriefingPassageView: UIViewRepresentable {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.colorSchemeContrast) private var accessibilityContrast

    let content: BriefingAttributedTextBuilder.Result
    var floatingExclusionSize: CGSize? = nil
    var floatingExclusionAlignment: APIBriefingFigureAlignment = .right
    let onOpenSource: (String) -> Void
    var onOpenDiscussion: (String) -> Void = { _ in }
    var onSourceAction: ((String, BriefingSourceAction) -> Void)? = nil
    let onDig: (String, String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(
            onOpenSource: onOpenSource,
            onOpenDiscussion: onOpenDiscussion,
            onSourceAction: onSourceAction
        )
    }

    func makeUIView(context: Context) -> DigDeeperTextView {
        let textView = DigDeeperTextView()
        textView.accessibilityIdentifier = "briefing.passage"
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

    fileprivate static func makeMeasuringView() -> DigDeeperTextView {
        let textView = DigDeeperTextView()
        textView.isScrollEnabled = false
        textView.textContainerInset = .zero
        textView.textContainer.lineFragmentPadding = 0
        return textView
    }

    func updateUIView(_ uiView: DigDeeperTextView, context: Context) {
        context.coordinator.onOpenSource = onOpenSource
        context.coordinator.onOpenDiscussion = onOpenDiscussion
        context.coordinator.onSourceAction = onSourceAction
        uiView.floatingExclusionSize = floatingExclusionSize
        uiView.floatingExclusionAlignment = floatingExclusionAlignment
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
            floatingExclusionAlignment: floatingExclusionAlignment,
            dynamicTypeSize: dynamicTypeSize,
            colorScheme: colorScheme,
            accessibilityContrast: accessibilityContrast
        )
        if let measurement = context.coordinator.measurement,
           measurement.fingerprint == measurementFingerprint {
            return measurement.size
        }
        let signpostState = BriefingPerformance.signposter.beginInterval("passage-measurement")
        // Measure on a detached view: SwiftUI probes widths it never places, and
        // resizing the displayed view would leave its exclusion at a probe width.
        let measuringView = context.coordinator.measuringView
        measuringView.attributedText = uiView.attributedText
        measuringView.floatingExclusionSize = floatingExclusionSize
        measuringView.floatingExclusionAlignment = floatingExclusionAlignment
        measuringView.bounds.size.width = width
        measuringView.updateFloatingExclusion(forWidth: width)
        let fittingSize = measuringView.sizeThatFits(
            CGSize(width: width, height: .greatestFiniteMagnitude)
        )
        let minimumHeight = floatingExclusionSize?.height ?? 0
        let size = CGSize(width: width, height: max(fittingSize.height, minimumHeight))
        BriefingPerformance.signposter.endInterval("passage-measurement", signpostState)
        context.coordinator.measurement = (measurementFingerprint, size)
        return size
    }

    /// Vertical distance between passage baselines at the given text size.
    static func lineStep(for dynamicTypeSize: DynamicTypeSize) -> CGFloat {
        let traits = UITraitCollection(
            preferredContentSizeCategory: UIContentSizeCategory(dynamicTypeSize)
        )
        let font = UIFontMetrics(forTextStyle: .callout).scaledFont(
            for: BriefingAttributedTextBuilder.passageBodyFont(),
            compatibleWith: traits
        )
        return font.lineHeight + BriefingAttributedTextBuilder.passageLineSpacing
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
            let floatingExclusionAlignment: APIBriefingFigureAlignment
            let dynamicTypeSize: DynamicTypeSize
            let colorScheme: ColorScheme
            let accessibilityContrast: ColorSchemeContrast
        }

        var onOpenSource: (String) -> Void
        var onOpenDiscussion: (String) -> Void
        var onSourceAction: ((String, BriefingSourceAction) -> Void)?
        let measuringView = BriefingPassageView.makeMeasuringView()
        var contentRevision = 0
        var measurement: (fingerprint: MeasurementFingerprint, size: CGSize)?
        private var renderFingerprint: RenderFingerprint?

        init(
            onOpenSource: @escaping (String) -> Void,
            onOpenDiscussion: @escaping (String) -> Void,
            onSourceAction: ((String, BriefingSourceAction) -> Void)? = nil
        ) {
            self.onOpenSource = onOpenSource
            self.onOpenDiscussion = onOpenDiscussion
            self.onSourceAction = onSourceAction
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
            // Taps still open the source; long presses offer source actions below.
            return UIAction(
                title: defaultAction.title,
                image: defaultAction.image
            ) { [weak self] _ in
                self?.openBriefingLink(url)
            }
        }

        func textView(
            _ textView: UITextView,
            menuConfigurationFor textItem: UITextItem,
            defaultMenu: UIMenu
        ) -> UITextItem.MenuConfiguration? {
            guard case .link(let url) = textItem.content,
                  let menu = sourceMenu(for: url)
            else { return UITextItem.MenuConfiguration(menu: defaultMenu) }
            return UITextItem.MenuConfiguration(preview: nil, menu: menu)
        }

        func sourceMenu(for url: URL) -> UIMenu? {
            guard let sourceKey = discussionSourceKey(from: url) ?? sourceKey(from: url),
                  let onSourceAction
            else { return nil }
            let actions = BriefingSourceAction.allCases.map { action in
                UIAction(
                    title: action.title,
                    image: UIImage(systemName: action.systemImage),
                    identifier: UIAction.Identifier("briefing.source_action.\(action.rawValue)")
                ) { _ in
                    onSourceAction(sourceKey, action)
                }
            }
            return UIMenu(children: actions)
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
