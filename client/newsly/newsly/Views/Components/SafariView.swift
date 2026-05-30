//
//  SafariView.swift
//  newsly
//

import SafariServices
import SwiftUI

struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url.newslySafariCompatibleLocalURL)
    }

    func updateUIViewController(_ uiViewController: SFSafariViewController, context: Context) {
        // No-op
    }
}

private extension URL {
    var newslySafariCompatibleLocalURL: URL {
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
