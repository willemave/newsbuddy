//
//  LearningDeckWebView.swift
//  newsly
//

import SwiftUI
import UIKit
import WebKit

/// Bridges the SwiftUI reader to the underlying reveal.js `WKWebView`: exposes a
/// load phase for loading/error overlays and forwards slide navigation commands.
final class LearningDeckReaderWebController: ObservableObject {
    enum LoadPhase {
        case loading
        case loaded
        case failed
    }

    @Published private(set) var phase: LoadPhase = .loading

    private weak var webView: WKWebView?
    private var url: URL?

    func attach(_ webView: WKWebView, url: URL) {
        self.webView = webView
        self.url = url
    }

    func markLoading() { phase = .loading }
    func markLoaded() { phase = .loaded }
    func markFailed() { phase = .failed }

    func reload() {
        guard let webView, let url else { return }
        phase = .loading
        webView.load(URLRequest(url: url))
    }

    func goNext() { evaluate("if (window.Reveal && Reveal.next) { Reveal.next(); }") }
    func goPrevious() { evaluate("if (window.Reveal && Reveal.prev) { Reveal.prev(); }") }
    func toggleOverview() { evaluate("if (window.Reveal && Reveal.toggleOverview) { Reveal.toggleOverview(); }") }

    private func evaluate(_ javascript: String) {
        webView?.evaluateJavaScript(javascript)
    }
}

struct LearningDeckWebView: UIViewRepresentable {
    let url: URL
    let controller: LearningDeckReaderWebController
    @Binding var slideContext: LearningDeckSlideContext

    func makeCoordinator() -> Coordinator {
        Coordinator(controller: controller, slideContext: $slideContext)
    }

    func makeUIView(context: Context) -> WKWebView {
        let contentController = WKUserContentController()
        contentController.add(context.coordinator, name: Coordinator.messageHandlerName)

        let configuration = WKWebViewConfiguration()
        configuration.userContentController = contentController
        configuration.allowsInlineMediaPlayback = true

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.isOpaque = false
        webView.backgroundColor = .clear
        controller.attach(webView, url: url)
        controller.markLoading()
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard webView.url != url else { return }
        controller.markLoading()
        webView.load(URLRequest(url: url))
    }

    static func dismantleUIView(_ webView: WKWebView, coordinator: Coordinator) {
        webView.configuration.userContentController.removeScriptMessageHandler(
            forName: Coordinator.messageHandlerName
        )
        webView.navigationDelegate = nil
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        static let messageHandlerName = "newslyLearningDeck"

        private let controller: LearningDeckReaderWebController
        @Binding private var slideContext: LearningDeckSlideContext
        private var lastAnnouncedKey: String?

        init(
            controller: LearningDeckReaderWebController,
            slideContext: Binding<LearningDeckSlideContext>
        ) {
            self.controller = controller
            _slideContext = slideContext
        }

        func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
            controller.markLoading()
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            controller.markLoaded()
            injectSlideBridge(into: webView)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            controller.markFailed()
        }

        func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation!,
            withError error: Error
        ) {
            controller.markFailed()
        }

        func userContentController(
            _ userContentController: WKUserContentController,
            didReceive message: WKScriptMessage
        ) {
            guard let context = LearningDeckSlideContext(scriptPayload: message.body) else {
                return
            }
            slideContext = context
            announceIfNeeded(context)
        }

        private func injectSlideBridge(into webView: WKWebView) {
            webView.evaluateJavaScript(Self.installSlideChangedHookScript)
        }

        private func announceIfNeeded(_ context: LearningDeckSlideContext) {
            guard context.horizontalIndex != nil || context.verticalIndex != nil else { return }
            let horizontal = (context.horizontalIndex ?? 0) + 1
            let vertical = context.verticalIndex ?? 0
            let key = "\(horizontal).\(vertical)"
            guard key != lastAnnouncedKey else { return }
            lastAnnouncedKey = key

            var label = "Slide \(horizontal)"
            if vertical > 0 { label += ".\(vertical + 1)" }
            if let total = context.totalSlides, total > 0 { label += " of \(total)" }
            if let title = context.title, !title.isEmpty { label += ", \(title)" }
            UIAccessibility.post(notification: .announcement, argument: label)
        }

        private static let installSlideChangedHookScript = """
        (function () {
          if (window.__newslyLearningDeckBridgeInstalled) return;
          window.__newslyLearningDeckBridgeInstalled = true;
          function compact(value) {
            return String(value || "").replace(/\\s+/g, " ").trim();
          }
          function payload() {
            var reveal = window.Reveal;
            var indices = reveal && typeof reveal.getIndices === "function"
              ? reveal.getIndices()
              : {};
            var total = reveal && typeof reveal.getHorizontalSlides === "function"
              ? reveal.getHorizontalSlides().length
              : null;
            var slide = reveal && typeof reveal.getCurrentSlide === "function"
              ? reveal.getCurrentSlide()
              : document.querySelector(".reveal .slides section.present") || document.querySelector("section.present");
            var titleNode = slide ? slide.querySelector("h1, h2, h3, .slide-title") : null;
            return {
              h: Number.isFinite(indices.h) ? indices.h : null,
              v: Number.isFinite(indices.v) ? indices.v : null,
              total: Number.isFinite(total) ? total : null,
              title: compact(titleNode ? titleNode.innerText || titleNode.textContent : ""),
              text: compact(slide ? slide.innerText || slide.textContent : document.body.innerText)
            };
          }
          function send() {
            if (!window.webkit || !window.webkit.messageHandlers || !window.webkit.messageHandlers.newslyLearningDeck) return;
            window.webkit.messageHandlers.newslyLearningDeck.postMessage(payload());
          }
          function bindReveal() {
            if (window.Reveal && typeof window.Reveal.on === "function") {
              window.Reveal.on("ready", send);
              window.Reveal.on("slidechanged", send);
              send();
              return;
            }
            window.setTimeout(bindReveal, 150);
          }
          bindReveal();
          window.setTimeout(send, 250);
          window.setTimeout(send, 1000);
        }());
        """
    }
}
