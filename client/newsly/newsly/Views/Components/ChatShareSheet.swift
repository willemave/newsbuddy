//
//  ChatShareSheet.swift
//  newsly
//

import SwiftUI
import UIKit

struct ShareContent: Identifiable {
    let id = UUID()
    let messageContent: String
    let articleTitle: String?
    let articleUrl: String?

    var shareText: String {
        var text = Self.plainText(fromMarkdown: messageContent)

        if let title = articleTitle {
            text = "\(title)\n\n\(text)"
        }

        if let url = articleUrl {
            text += "\n\n\(url)"
        }

        return text
    }

    static func plainText(fromMarkdown markdown: String) -> String {
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        guard let attributed = try? AttributedString(markdown: markdown, options: options) else {
            return markdown
        }
        return String(attributed.characters)
    }
}

struct ShareSheet: UIViewControllerRepresentable {
    let content: ShareContent

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let activityItems: [Any] = [content.shareText]
        return UIActivityViewController(
            activityItems: activityItems,
            applicationActivities: nil
        )
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
