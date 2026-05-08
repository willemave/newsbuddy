//
//  SelectableText.swift
//  newsly
//

import SwiftUI
import UIKit

struct SelectableText: UIViewRepresentable {
    let text: String
    let textColor: UIColor
    let font: UIFont
    let lineLimit: Int?
    let lineBreakMode: NSLineBreakMode
    var onDigDeeper: ((String) -> Void)?

    init(
        _ text: String,
        textColor: UIColor = .label,
        font: UIFont = .preferredFont(forTextStyle: .callout),
        lineLimit: Int? = nil,
        lineBreakMode: NSLineBreakMode = .byWordWrapping,
        onDigDeeper: ((String) -> Void)? = nil
    ) {
        self.text = text
        self.textColor = textColor
        self.font = font
        self.lineLimit = lineLimit
        self.lineBreakMode = lineBreakMode
        self.onDigDeeper = onDigDeeper
    }

    func makeUIView(context: Context) -> DigDeeperTextView {
        let textView = DigDeeperTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.isScrollEnabled = false
        textView.backgroundColor = .clear
        textView.textContainerInset = .zero
        textView.textContainer.lineFragmentPadding = 0
        textView.textContainer.maximumNumberOfLines = lineLimit ?? 0
        textView.textContainer.lineBreakMode = lineBreakMode
        textView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        textView.setContentHuggingPriority(.defaultLow, for: .horizontal)
        textView.dataDetectorTypes = [.link]
        textView.onDigDeeper = onDigDeeper
        return textView
    }

    func updateUIView(_ uiView: DigDeeperTextView, context: Context) {
        uiView.text = text
        uiView.textColor = textColor.resolvedColor(with: uiView.traitCollection)
        uiView.font = font
        uiView.textContainer.maximumNumberOfLines = lineLimit ?? 0
        uiView.textContainer.lineBreakMode = lineBreakMode
        uiView.onDigDeeper = onDigDeeper
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: DigDeeperTextView, context: Context) -> CGSize? {
        guard let width = proposal.width, width.isFinite, width > 0 else { return nil }
        let fittingSize = uiView.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude))
        let height = limitedHeight(fittingSize.height, font: uiView.font)
        return CGSize(width: width, height: height)
    }

    private func limitedHeight(_ height: CGFloat, font: UIFont?) -> CGFloat {
        guard let lineLimit, let font else { return height }
        let maxHeight = ceil(font.lineHeight * CGFloat(lineLimit))
        return min(height, maxHeight)
    }
}
