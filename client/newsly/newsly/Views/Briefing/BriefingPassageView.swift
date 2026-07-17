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
            onOpenDiscussion: onOpenDiscussion,
            onDig: onDig
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
            .foregroundColor: UIColor.appAccent,
            .underlineStyle: NSUnderlineStyle.single.rawValue
        ]
        textView.onDigDeeper = { selection in
            context.coordinator.onDig(selection, textView.attributedText.string)
        }
        let tap = UITapGestureRecognizer(
            target: context.coordinator,
            action: #selector(Coordinator.handleInsightTap(_:))
        )
        tap.cancelsTouchesInView = false
        textView.addGestureRecognizer(tap)
        context.coordinator.textView = textView
        return textView
    }

    func updateUIView(_ uiView: DigDeeperTextView, context: Context) {
        context.coordinator.onOpenSource = onOpenSource
        context.coordinator.onOpenDiscussion = onOpenDiscussion
        context.coordinator.onDig = onDig
        uiView.floatingExclusionSize = floatingExclusionSize
        let scaledText = Self.scaledAttributedText(
            content.attributedText,
            compatibleWith: uiView.traitCollection
        )
        if !uiView.attributedText.isEqual(to: scaledText) {
            uiView.attributedText = scaledText
            context.coordinator.measurement = nil
            context.coordinator.contentRevision += 1
            uiView.invalidateIntrinsicContentSize()
        }
        uiView.onDigDeeper = { selection in
            context.coordinator.onDig(selection, content.plainText)
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
        var onDig: (String, String) -> Void
        var contentRevision = 0
        var measurement: (fingerprint: MeasurementFingerprint, size: CGSize)?
        weak var textView: DigDeeperTextView?

        init(
            onOpenSource: @escaping (String) -> Void,
            onOpenDiscussion: @escaping (String) -> Void,
            onDig: @escaping (String, String) -> Void
        ) {
            self.onOpenSource = onOpenSource
            self.onOpenDiscussion = onOpenDiscussion
            self.onDig = onDig
        }

        func textView(
            _ textView: UITextView,
            shouldInteractWith URL: URL,
            in characterRange: NSRange,
            interaction: UITextItemInteraction
        ) -> Bool {
            if let sourceKey = discussionSourceKey(from: URL) {
                onOpenDiscussion(sourceKey)
                return false
            }
            if let sourceKey = sourceKey(from: URL) {
                onOpenSource(sourceKey)
                return false
            }
            return true
        }

        @objc func handleInsightTap(_ recognizer: UITapGestureRecognizer) {
            guard recognizer.state == .ended,
                  let textView,
                  let attributedText = textView.attributedText,
                  attributedText.length > 0
            else { return }

            var location = recognizer.location(in: textView)
            location.x -= textView.textContainerInset.left
            location.y -= textView.textContainerInset.top
            let index = textView.layoutManager.characterIndex(
                for: location,
                in: textView.textContainer,
                fractionOfDistanceBetweenInsertionPoints: nil
            )
            guard index < attributedText.length else { return }
            if let link = attributedText.attribute(.link, at: index, effectiveRange: nil) {
                if let sourceKey = discussionSourceKey(from: link) {
                    onOpenDiscussion(sourceKey)
                    return
                }
                if let sourceKey = sourceKey(from: link) {
                    onOpenSource(sourceKey)
                    return
                }
            }
            // Plain-text tap: highlight the enclosing sentence and offer the
            // edit menu (Dig Deeper, Copy, …) instead of doing nothing.
            let sentenceRange = Self.sentenceRange(
                around: index,
                in: attributedText.string
            )
            guard sentenceRange.length > 0 else { return }
            // Tapping the already-selected sentence deselects it.
            if textView.selectedRange == sentenceRange {
                textView.clearSelection()
                return
            }
            textView.selectedRange = sentenceRange
            textView.presentSelectionMenu(at: recognizer.location(in: textView))
        }

        static func sentenceRange(around index: Int, in string: String) -> NSRange {
            let nsString = string as NSString
            var found = NSRange(location: NSNotFound, length: 0)
            nsString.enumerateSubstrings(
                in: NSRange(location: 0, length: nsString.length),
                options: [.bySentences, .substringNotRequired]
            ) { _, range, _, stop in
                if NSLocationInRange(index, range) {
                    found = range
                    stop.pointee = true
                }
            }
            guard found.location != NSNotFound else { return NSRange(location: 0, length: 0) }
            // Trim trailing whitespace/newlines so the highlight hugs the sentence.
            var length = found.length
            let whitespace = CharacterSet.whitespacesAndNewlines
            while length > 0 {
                let character = nsString.character(at: found.location + length - 1)
                guard let scalar = Unicode.Scalar(character), whitespace.contains(scalar) else { break }
                length -= 1
            }
            return NSRange(location: found.location, length: length)
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

        private func sourceKey(from link: Any) -> String? {
            resolveURL(from: link).flatMap { sourceKey(from: $0) }
        }

        private func discussionSourceKey(from link: Any) -> String? {
            resolveURL(from: link).flatMap { discussionSourceKey(from: $0) }
        }

        private func resolveURL(from link: Any) -> URL? {
            if let url = link as? URL {
                return url
            }
            if let rawURL = link as? String {
                return URL(string: rawURL)
            }
            return nil
        }
    }
}
