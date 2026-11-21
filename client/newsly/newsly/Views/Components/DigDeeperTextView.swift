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
