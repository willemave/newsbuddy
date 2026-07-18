//
//  DigDeeperTextView.swift
//  newsly
//

import UIKit

/// Custom UITextView that adds "Dig Deeper" to the edit menu.
class DigDeeperTextView: UITextView {
    var onDigDeeper: ((String) -> Void)?
    var digSelectionNormalizer: (String) -> String? = { selection in
        selection.isEmpty ? nil : selection
    }
    var adaptiveTextColor: UIColor? {
        didSet {
            applyAdaptiveTextColor()
        }
    }

    /// When set, text wraps around a rectangle anchored to the top-right of the
    /// text container (used for briefing passages with an inline floated figure).
    var floatingExclusionSize: CGSize? {
        didSet {
            guard floatingExclusionSize != oldValue else { return }
            updateFloatingExclusion(forWidth: bounds.width)
            setNeedsLayout()
        }
    }

    func updateFloatingExclusion(forWidth width: CGFloat) {
        guard let size = floatingExclusionSize, width > size.width + 40 else {
            if !textContainer.exclusionPaths.isEmpty {
                textContainer.exclusionPaths = []
            }
            return
        }
        let rect = CGRect(x: width - size.width, y: 0, width: size.width, height: size.height)
        textContainer.exclusionPaths = [UIBezierPath(rect: rect)]
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        updateFloatingExclusion(forWidth: bounds.width)
    }

    private func clearSelection() {
        selectedTextRange = nil
        resignFirstResponder()
    }

    override func traitCollectionDidChange(_ previousTraitCollection: UITraitCollection?) {
        super.traitCollectionDidChange(previousTraitCollection)
        guard previousTraitCollection?.hasDifferentColorAppearance(comparedTo: traitCollection) != false else {
            return
        }
        applyAdaptiveTextColor()
    }

    override func buildMenu(with builder: any UIMenuBuilder) {
        super.buildMenu(with: builder)
        guard onDigDeeper != nil, selectedTextForDigDeeper != nil else { return }

        let digDeeperAction = UIAction(
            title: "Dig Deeper",
            image: UIImage(systemName: "magnifyingglass")
        ) { [weak self] _ in
            self?.performDigDeeper()
        }

        let menu = UIMenu(title: "", options: .displayInline, children: [digDeeperAction])
        builder.insertChild(menu, atStartOfMenu: .standardEdit)
    }

    private func performDigDeeper() {
        guard let selectedText = selectedTextForDigDeeper else { return }

        let callback = onDigDeeper
        let captured = selectedText

        // Clear the highlight before returning to SwiftUI so nothing lingers
        // behind the dig panel.
        clearSelection()
        DispatchQueue.main.async {
            callback?(captured)
        }
    }

    private var selectedTextForDigDeeper: String? {
        guard let selectedTextRange,
              let selectedText = text(in: selectedTextRange)
        else { return nil }
        return digSelectionNormalizer(selectedText)
    }

    private func applyAdaptiveTextColor() {
        guard let adaptiveTextColor else { return }
        textColor = adaptiveTextColor.resolvedColor(with: traitCollection)
    }
}
