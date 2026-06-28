//
//  SafariView.swift
//  newsly
//

import SafariServices
import SwiftUI

struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url.newslyBrowserCompatibleLocalURL)
    }

    func updateUIViewController(_ uiViewController: SFSafariViewController, context: Context) {
        // No-op
    }
}

extension URL {
    var newslyBrowserCompatibleLocalURL: URL {
#if targetEnvironment(simulator)
        guard host == "127.0.0.1",
              AppSettings.shared.serverHost.caseInsensitiveCompare("localhost") == .orderedSame,
              var components = URLComponents(url: self, resolvingAgainstBaseURL: false)
        else {
            return self
        }
        components.host = "localhost"
        return components.url ?? self
#else
        return self
#endif
    }
}
