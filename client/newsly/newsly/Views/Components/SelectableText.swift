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
    let maxWidth: CGFloat
    @Binding var calculatedHeight: CGFloat
    var onDigDeeper: ((String) -> Void)?

    init(
        _ text: String,
        textColor: UIColor = .label,
        font: UIFont = .preferredFont(forTextStyle: .callout),
        maxWidth: CGFloat = UIScreen.main.bounds.width,
        calculatedHeight: Binding<CGFloat> = .constant(.zero),
        onDigDeeper: ((String) -> Void)? = nil
    ) {
        self.text = text
        self.textColor = textColor
        self.font = font
        self.maxWidth = maxWidth
        self._calculatedHeight = calculatedHeight
        self.onDigDeeper = onDigDeeper
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onDigDeeper: onDigDeeper)
    }

    func makeUIView(context: Context) -> DigDeeperTextView {
        let textView = DigDeeperTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.isScrollEnabled = false
        textView.backgroundColor = .clear
        textView.textContainerInset = .zero
        textView.textContainer.lineFragmentPadding = 0
        textView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        textView.dataDetectorTypes = [.link]
        textView.onDigDeeper = context.coordinator.onDigDeeper
        return textView
    }

    func updateUIView(_ uiView: DigDeeperTextView, context: Context) {
        uiView.text = text
        uiView.textColor = textColor
        uiView.font = font
        uiView.onDigDeeper = context.coordinator.onDigDeeper
        let fittingSize = uiView.sizeThatFits(
            CGSize(width: maxWidth, height: .greatestFiniteMagnitude)
        )
        uiView.frame.size = fittingSize
        DispatchQueue.main.async {
            calculatedHeight = fittingSize.height
        }
    }

    class Coordinator {
        var onDigDeeper: ((String) -> Void)?

        init(onDigDeeper: ((String) -> Void)?) {
            self.onDigDeeper = onDigDeeper
        }
    }
}

struct SelectableAttributedText: UIViewRepresentable {
    let attributedText: NSAttributedString
    let textColor: UIColor
    let maxWidth: CGFloat
    @Binding var calculatedHeight: CGFloat
    var onDigDeeper: ((String) -> Void)?

    init(
        attributedText: NSAttributedString,
        textColor: UIColor,
        maxWidth: CGFloat = UIScreen.main.bounds.width,
        calculatedHeight: Binding<CGFloat> = .constant(.zero),
        onDigDeeper: ((String) -> Void)? = nil
    ) {
        self.attributedText = attributedText
        self.textColor = textColor
        self.maxWidth = maxWidth
        self._calculatedHeight = calculatedHeight
        self.onDigDeeper = onDigDeeper
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onDigDeeper: onDigDeeper)
    }

    func makeUIView(context: Context) -> DigDeeperTextView {
        let textView = DigDeeperTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.isScrollEnabled = false
        textView.backgroundColor = .clear
        textView.textContainerInset = .zero
        textView.textContainer.lineFragmentPadding = 0
        textView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        textView.dataDetectorTypes = [.link]
        textView.onDigDeeper = context.coordinator.onDigDeeper
        return textView
    }

    func updateUIView(_ uiView: DigDeeperTextView, context: Context) {
        let mutableAttr = NSMutableAttributedString(attributedString: attributedText)
        mutableAttr.addAttribute(
            .foregroundColor,
            value: textColor,
            range: NSRange(location: 0, length: mutableAttr.length)
        )
        uiView.attributedText = mutableAttr
        uiView.onDigDeeper = context.coordinator.onDigDeeper
        let fittingSize = uiView.sizeThatFits(
            CGSize(width: maxWidth, height: .greatestFiniteMagnitude)
        )
        uiView.frame.size = fittingSize
        DispatchQueue.main.async {
            calculatedHeight = fittingSize.height
        }
    }

    class Coordinator {
        var onDigDeeper: ((String) -> Void)?

        init(onDigDeeper: ((String) -> Void)?) {
            self.onDigDeeper = onDigDeeper
        }
    }
}
