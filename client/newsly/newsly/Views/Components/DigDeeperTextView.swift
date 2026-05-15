//
//  DigDeeperTextView.swift
//  newsly
//

import UIKit

/// Custom UITextView that adds "Dig Deeper" to the edit menu.
class DigDeeperTextView: UITextView {
    var onDigDeeper: ((String) -> Void)?
    var adaptiveTextColor: UIColor? {
        didSet {
            applyAdaptiveTextColor()
        }
    }

    override func traitCollectionDidChange(_ previousTraitCollection: UITraitCollection?) {
        super.traitCollectionDidChange(previousTraitCollection)
        guard previousTraitCollection?.hasDifferentColorAppearance(comparedTo: traitCollection) != false else {
            return
        }
        applyAdaptiveTextColor()
    }

    override func canPerformAction(_ action: Selector, withSender sender: Any?) -> Bool {
        if action == #selector(digDeeperAction(_:)) {
            return onDigDeeper != nil && selectedRange.length > 0
        }
        return super.canPerformAction(action, withSender: sender)
    }

    override func buildMenu(with builder: any UIMenuBuilder) {
        super.buildMenu(with: builder)
        guard onDigDeeper != nil else { return }

        let digDeeperAction = UIAction(
            title: "Dig Deeper",
            image: UIImage(systemName: "magnifyingglass")
        ) { [weak self] _ in
            self?.performDigDeeper()
        }

        let menu = UIMenu(title: "", options: .displayInline, children: [digDeeperAction])
        builder.insertChild(menu, atStartOfMenu: .standardEdit)
    }

    @objc func digDeeperAction(_ sender: Any?) {
        performDigDeeper()
    }

    private func performDigDeeper() {
        guard let selectedTextRange,
              let selectedText = text(in: selectedTextRange),
              !selectedText.isEmpty
        else { return }

        let callback = onDigDeeper
        let captured = selectedText

        // Resign first responder to dismiss selection before returning to SwiftUI.
        resignFirstResponder()
        DispatchQueue.main.async {
            callback?(captured)
        }
    }

    private func applyAdaptiveTextColor() {
        guard let adaptiveTextColor else { return }
        textColor = adaptiveTextColor.resolvedColor(with: traitCollection)
    }
}
