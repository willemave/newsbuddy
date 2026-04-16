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
        var text = messageContent

        if let title = articleTitle {
            text = "**\(title)**\n\n\(text)"
        }

        if let url = articleUrl {
            text += "\n\n\(url)"
        }

        return text
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
