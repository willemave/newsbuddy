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

    /// When set, text wraps around a rectangle anchored to a top edge of the
    /// text container (used for briefing passages with an inline floated figure).
    var floatingExclusionSize: CGSize? {
        didSet {
            guard floatingExclusionSize != oldValue else { return }
            updateFloatingExclusion(forWidth: bounds.width)
            setNeedsLayout()
        }
    }
    var floatingExclusionAlignment: APIBriefingFigureAlignment = .right {
        didSet {
            guard floatingExclusionAlignment != oldValue else { return }
            updateFloatingExclusion(forWidth: bounds.width)
            setNeedsLayout()
        }
    }

    func updateFloatingExclusion(forWidth width: CGFloat) {
        let rect = floatingExclusionRect(forWidth: width)
        guard textContainer.exclusionPaths.first?.bounds != rect else { return }
        textContainer.exclusionPaths = rect.map { [UIBezierPath(rect: $0)] } ?? []
    }

    private func floatingExclusionRect(forWidth width: CGFloat) -> CGRect? {
        guard let size = floatingExclusionSize, width > size.width + 40 else { return nil }
        let x = floatingExclusionAlignment == .left ? 0 : width - size.width
        return CGRect(x: x, y: 0, width: size.width, height: size.height)
    }

    override func layoutSubviews() {
        // Place the exclusion before UIKit lays out text for the new bounds.
        updateFloatingExclusion(forWidth: bounds.width)
        super.layoutSubviews()
    }

    private func clearSelection() {
        selectedTextRange = nil
        resignFirstResponder()
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
}
