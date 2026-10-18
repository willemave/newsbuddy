import SwiftUI
import WebKit
import XCTest
@testable import newsly

@MainActor
final class ArticleWebViewTests: XCTestCase {
    func testNavigationFailureIsVisibleAndNextLoadClearsIt() {
        var state: ArticleWebView.LoadState = .loading
        let coordinator = ArticleWebView.Coordinator(
            state: Binding(get: { state }, set: { state = $0 }), reloadID: 0
        )
        let webView = WKWebView()
        let error = URLError(.notConnectedToInternet)
        coordinator.webView(webView, didFailProvisionalNavigation: nil, withError: error)
        XCTAssertEqual(state, .failed(error.localizedDescription))
        coordinator.webView(webView, didStartProvisionalNavigation: nil)
        XCTAssertEqual(state, .loading)
        coordinator.webView(webView, didFinish: nil)
        XCTAssertEqual(state, .loaded)
    }

    func testFailureRetainsActualNavigationTargetForRetry() {
        var state: ArticleWebView.LoadState = .loaded
        let coordinator = ArticleWebView.Coordinator(
            state: Binding(get: { state }, set: { state = $0 }), reloadID: 0
        )
        let destination = URL(string: "https://example.com/failed-link")!
        let failure = NSError(domain: NSURLErrorDomain, code: NSURLErrorCannotConnectToHost,
                              userInfo: [NSURLErrorFailingURLErrorKey: destination])
        coordinator.webView(WKWebView(), didFailProvisionalNavigation: nil, withError: failure)
        XCTAssertEqual(coordinator.retryURL, destination)
        XCTAssertEqual(state, .failed(failure.localizedDescription))
    }

    func testCancelledNavigationDoesNotReplaceCurrentPageWithError() {
        var state: ArticleWebView.LoadState = .loaded
        let coordinator = ArticleWebView.Coordinator(
            state: Binding(get: { state }, set: { state = $0 }), reloadID: 0
        )
        coordinator.webView(WKWebView(), didFail: nil, withError: URLError(.cancelled))
        XCTAssertEqual(state, .loaded)
    }
}
