import SwiftUI
import UIKit

struct BriefingPassageView: UIViewRepresentable {
    let block: APIBriefingBlock
    var floatingExclusionSize: CGSize? = nil
    var discussionChips: [String: BriefingDiscussionChip] = [:]
    let onOpenSource: (String) -> Void
    var onOpenDiscussion: (String) -> Void = { _ in }
    let onDig: (String, String) -> Void
    var onSourceLinkPositionsChange: ([BriefingSourceLinkPosition]) -> Void = { _ in }

    func makeCoordinator() -> Coordinator {
        Coordinator(
            onOpenSource: onOpenSource,
            onOpenDiscussion: onOpenDiscussion,
            onDig: onDig,
            onSourceLinkPositionsChange: onSourceLinkPositionsChange
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
        context.coordinator.onSourceLinkPositionsChange = onSourceLinkPositionsChange
        uiView.floatingExclusionSize = floatingExclusionSize
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(
            paragraphs: block.paragraphs ?? [],
            weight: block.weight,
            discussionChips: discussionChips
        )
        if !uiView.attributedText.isEqual(to: result.attributedText) {
            uiView.attributedText = result.attributedText
            uiView.invalidateIntrinsicContentSize()
        }
        uiView.onDigDeeper = { selection in
            context.coordinator.onDig(selection, result.plainText)
        }
        context.coordinator.scheduleSourceLinkPositionPublish(from: uiView)
    }

    func sizeThatFits(
        _ proposal: ProposedViewSize,
        uiView: DigDeeperTextView,
        context: Context
    ) -> CGSize? {
        guard let width = proposal.width, width.isFinite, width > 0 else { return nil }
        if abs(uiView.bounds.width - width) > .ulpOfOne {
            uiView.bounds.size.width = width
        }
        uiView.updateFloatingExclusion(forWidth: width)
        let fittingSize = uiView.sizeThatFits(
            CGSize(width: width, height: .greatestFiniteMagnitude)
        )
        let minimumHeight = floatingExclusionSize?.height ?? 0
        context.coordinator.scheduleSourceLinkPositionPublish(from: uiView)
        return CGSize(width: width, height: max(fittingSize.height, minimumHeight))
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        var onOpenSource: (String) -> Void
        var onOpenDiscussion: (String) -> Void
        var onDig: (String, String) -> Void
        var onSourceLinkPositionsChange: ([BriefingSourceLinkPosition]) -> Void
        weak var textView: DigDeeperTextView?
        private var lastPublishedSourceLinkPositions: [BriefingSourceLinkPosition] = []

        init(
            onOpenSource: @escaping (String) -> Void,
            onOpenDiscussion: @escaping (String) -> Void,
            onDig: @escaping (String, String) -> Void,
            onSourceLinkPositionsChange: @escaping ([BriefingSourceLinkPosition]) -> Void
        ) {
            self.onOpenSource = onOpenSource
            self.onOpenDiscussion = onOpenDiscussion
            self.onDig = onDig
            self.onSourceLinkPositionsChange = onSourceLinkPositionsChange
        }

        func scheduleSourceLinkPositionPublish(from textView: UITextView) {
            DispatchQueue.main.async { [weak self, weak textView] in
                guard let self, let textView else { return }
                textView.layoutIfNeeded()
                self.publishSourceLinkPositions(from: textView)
            }
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
                let insightText = String(attributedText.string[range])
                // No lingering highlight behind the dig panel.
                textView.clearSelection()
                onDig(insightText, attributedText.string)
                return
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
            guard url.scheme == "newsly", url.host == "briefing" else { return nil }
            let components = url.pathComponents.filter { $0 != "/" }
            guard components.count == 2 else { return nil }
            return "\(components[0]):\(components[1])"
        }

        private func discussionSourceKey(from url: URL) -> String? {
            guard url.scheme == "newsly", url.host == "briefing" else { return nil }
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

        private func publishSourceLinkPositions(from textView: UITextView) {
            guard let attributedText = textView.attributedText,
                  attributedText.length > 0
            else {
                publishSourceLinkPositionsIfChanged([])
                return
            }

            let layoutManager = textView.layoutManager
            let textContainer = textView.textContainer
            guard textView.bounds.width > 0,
                  textContainer.size.width > 0,
                  textContainer.size.width.isFinite
            else { return }
            layoutManager.ensureLayout(for: textContainer)

            var maxYBySourceKey: [String: CGFloat] = [:]
            let fullRange = NSRange(location: 0, length: attributedText.length)
            attributedText.enumerateAttribute(.link, in: fullRange) { value, range, _ in
                guard let value,
                      let sourceKey = sourceKey(from: value),
                      range.length > 0
                else { return }

                let glyphRange = layoutManager.glyphRange(
                    forCharacterRange: range,
                    actualCharacterRange: nil
                )
                guard glyphRange.length > 0 else { return }

                let rect = layoutManager.boundingRect(
                    forGlyphRange: glyphRange,
                    in: textContainer
                )
                let maxY = rect.maxY + textView.textContainerInset.top
                maxYBySourceKey[sourceKey] = max(maxYBySourceKey[sourceKey] ?? 0, maxY)
            }

            let positions = maxYBySourceKey
                .map { BriefingSourceLinkPosition(sourceKey: $0.key, maxY: $0.value) }
                .sorted {
                    if $0.maxY == $1.maxY {
                        return $0.sourceKey < $1.sourceKey
                    }
                    return $0.maxY < $1.maxY
                }
            publishSourceLinkPositionsIfChanged(positions)
        }

        private func publishSourceLinkPositionsIfChanged(_ positions: [BriefingSourceLinkPosition]) {
            guard positions != lastPublishedSourceLinkPositions else { return }
            lastPublishedSourceLinkPositions = positions
            onSourceLinkPositionsChange(positions)
        }
    }
}
