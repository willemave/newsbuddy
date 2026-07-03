import SwiftUI
import UIKit

struct BriefingPassageView: UIViewRepresentable {
    let block: APIBriefingBlock
    var floatingExclusionSize: CGSize? = nil
    let onOpenSource: (String) -> Void
    let onDig: (String, String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onOpenSource: onOpenSource, onDig: onDig)
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
        context.coordinator.onDig = onDig
        uiView.floatingExclusionSize = floatingExclusionSize
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(paragraphs: block.paragraphs ?? [], weight: block.weight)
        if !uiView.attributedText.isEqual(to: result.attributedText) {
            uiView.attributedText = result.attributedText
            uiView.invalidateIntrinsicContentSize()
        }
        uiView.onDigDeeper = { selection in
            context.coordinator.onDig(selection, result.plainText)
        }
    }

    func sizeThatFits(
        _ proposal: ProposedViewSize,
        uiView: DigDeeperTextView,
        context: Context
    ) -> CGSize? {
        guard let width = proposal.width, width.isFinite, width > 0 else { return nil }
        uiView.updateFloatingExclusion(forWidth: width)
        let fittingSize = uiView.sizeThatFits(
            CGSize(width: width, height: .greatestFiniteMagnitude)
        )
        let minimumHeight = floatingExclusionSize?.height ?? 0
        return CGSize(width: width, height: max(fittingSize.height, minimumHeight))
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        var onOpenSource: (String) -> Void
        var onDig: (String, String) -> Void
        weak var textView: DigDeeperTextView?

        init(
            onOpenSource: @escaping (String) -> Void,
            onDig: @escaping (String, String) -> Void
        ) {
            self.onOpenSource = onOpenSource
            self.onDig = onDig
        }

        func textView(
            _ textView: UITextView,
            shouldInteractWith URL: URL,
            in characterRange: NSRange,
            interaction: UITextItemInteraction
        ) -> Bool {
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
            if let link = attributedText.attribute(.link, at: index, effectiveRange: nil),
               let sourceKey = sourceKey(from: link) {
                onOpenSource(sourceKey)
                return
            }
            var effectiveRange = NSRange(location: 0, length: 0)
            let insight = attributedText.attribute(
                BriefingInsightAttributeName,
                at: index,
                effectiveRange: &effectiveRange
            )
            if insight != nil,
               effectiveRange.location != NSNotFound,
               NSMaxRange(effectiveRange) <= attributedText.length,
               let range = Range(effectiveRange, in: attributedText.string) {
                textView.selectedRange = effectiveRange
                onDig(String(attributedText.string[range]), attributedText.string)
                return
            }

            // Plain-text tap: highlight the enclosing sentence and offer the
            // edit menu (Dig Deeper, Copy, …) instead of doing nothing.
            let sentenceRange = Self.sentenceRange(
                around: index,
                in: attributedText.string
            )
            guard sentenceRange.length > 0 else { return }
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
            guard url.scheme == "newsly", url.host == "briefing" else { return nil }
            let components = url.pathComponents.filter { $0 != "/" }
            guard components.count == 2 else { return nil }
            return "\(components[0]):\(components[1])"
        }

        private func sourceKey(from link: Any) -> String? {
            if let url = link as? URL {
                return sourceKey(from: url)
            }
            if let rawURL = link as? String, let url = URL(string: rawURL) {
                return sourceKey(from: url)
            }
            return nil
        }
    }
}
