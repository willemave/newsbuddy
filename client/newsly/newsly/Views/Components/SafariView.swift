//
//  SafariView.swift
//  newsly
//

import SafariServices
import SwiftUI

struct SafariView: UIViewControllerRepresentable {
    @Environment(RootDependencyFactory.self) private var dependencyFactory
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(
            url: url.newslyBrowserCompatibleLocalURL(
                serverHost: dependencyFactory.appSettings.serverHost
            )
        )
    }

    func updateUIViewController(_ uiViewController: SFSafariViewController, context: Context) {
        // No-op
    }
}

extension URL {
    func newslyBrowserCompatibleLocalURL(serverHost: String) -> URL {
#if targetEnvironment(simulator)
        guard host == "127.0.0.1",
              serverHost.caseInsensitiveCompare("localhost") == .orderedSame,
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
