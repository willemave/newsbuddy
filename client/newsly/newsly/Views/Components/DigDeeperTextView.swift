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

    private lazy var tapEditMenuInteraction = UIEditMenuInteraction(delegate: self)

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

    /// Present the edit menu (Dig Deeper, Copy, …) for the current selection,
    /// e.g. after a tap programmatically selected a sentence.
    func presentSelectionMenu(at point: CGPoint) {
        guard selectedRange.length > 0 else { return }
        if tapEditMenuInteraction.view == nil {
            addInteraction(tapEditMenuInteraction)
        }
        _ = becomeFirstResponder()
        tapEditMenuInteraction.presentEditMenu(
            with: UIEditMenuConfiguration(identifier: nil, sourcePoint: point)
        )
    }

    /// Remove the sentence/insight highlight left behind by tap selection.
    func clearSelection() {
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

        // Clear the highlight before returning to SwiftUI so nothing lingers
        // behind the dig panel.
        clearSelection()
        DispatchQueue.main.async {
            callback?(captured)
        }
    }

    private func applyAdaptiveTextColor() {
        guard let adaptiveTextColor else { return }
        textColor = adaptiveTextColor.resolvedColor(with: traitCollection)
    }
}

extension DigDeeperTextView: UIEditMenuInteractionDelegate {
    func editMenuInteraction(
        _ interaction: UIEditMenuInteraction,
        willDismissMenuFor configuration: UIEditMenuConfiguration,
        animator: any UIEditMenuInteractionAnimating
    ) {
        // Deselect once the menu goes away. Deferred a runloop so a chosen
        // action (Dig Deeper, Copy) still sees the selection it acts on, and
        // skipped when the dismissal came from selecting a different sentence.
        let rangeAtDismiss = selectedRange
        DispatchQueue.main.async { [weak self] in
            guard let self, NSEqualRanges(self.selectedRange, rangeAtDismiss) else { return }
            self.clearSelection()
        }
    }

    func editMenuInteraction(
        _ interaction: UIEditMenuInteraction,
        menuFor configuration: UIEditMenuConfiguration,
        suggestedActions: [UIMenuElement]
    ) -> UIMenu? {
        // The suggested actions already include "Dig Deeper" via buildMenu(with:),
        // so only synthesize a menu when the system offers nothing.
        guard suggestedActions.isEmpty else {
            return UIMenu(children: suggestedActions)
        }
        var children: [UIMenuElement] = []
        if onDigDeeper != nil {
            children.append(
                UIAction(
                    title: "Dig Deeper",
                    image: UIImage(systemName: "magnifyingglass")
                ) { [weak self] _ in
                    self?.performDigDeeper()
                }
            )
        }
        children.append(
            UIAction(title: "Copy", image: UIImage(systemName: "doc.on.doc")) { [weak self] _ in
                guard let self,
                      let selectedTextRange,
                      let selectedText = text(in: selectedTextRange)
                else { return }
                UIPasteboard.general.string = selectedText
            }
        )
        return UIMenu(children: children)
    }
}
