//
//  ArticleWebView.swift
//  newsly
//

import SwiftUI
import WebKit

/// The original page inside the article reader. Links that ask for a new window
/// load in place so the reader's toolbar stays in charge.
struct ArticleWebView: UIViewRepresentable {
    let url: URL
    enum LoadState: Equatable {
        case loading
        case loaded
        case failed(String)
    }

    @Binding var state: LoadState
    let reloadID: Int

    func makeCoordinator() -> Coordinator {
        Coordinator(state: $state, reloadID: reloadID)
    }

    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.accessibilityIdentifier = "article.reader.web"
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.reloadID != reloadID else { return }
        context.coordinator.reloadID = reloadID
        webView.load(URLRequest(url: context.coordinator.retryURL ?? webView.url ?? url))
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        private let state: Binding<LoadState>
        var reloadID: Int
        private(set) var retryURL: URL?

        init(state: Binding<LoadState>, reloadID: Int) {
            self.state = state
            self.reloadID = reloadID
        }

        private func failed(_ error: Error) {
            let failure = error as NSError
            guard failure.domain != NSURLErrorDomain || failure.code != NSURLErrorCancelled else { return }
            if let url = failure.userInfo[NSURLErrorFailingURLErrorKey] as? URL {
                retryURL = url
            }
            state.wrappedValue = .failed(error.localizedDescription)
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }
            let scheme = url.scheme?.lowercased()
            switch scheme {
            case "http", "https", "about", "blob", "data":
                break
            default:
                if navigationAction.navigationType == .linkActivated {
                    UIApplication.shared.open(url)
                }
                decisionHandler(.cancel)
                return
            }
            if navigationAction.targetFrame?.isMainFrame != false {
                retryURL = url
            }
            decisionHandler(.allow)
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            state.wrappedValue = .loading
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            state.wrappedValue = .loaded
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            failed(error)
        }

        func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation!,
            withError error: Error
        ) {
            failed(error)
        }

        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            if navigationAction.targetFrame == nil {
                webView.load(navigationAction.request)
            }
            return nil
        }
    }
}
