#if DEBUG
import SwiftUI

/// Exercises the real reader and keyboard without a backend or generated artifact.
struct E2ELearningDeckReaderVisualState: View {
    @State private var activeChats: ActiveChatSessionManager
    private let viewerURL: URL
    private let deck: LearningDeck

    init() {
        let service = E2EDeckReaderStatusService()
        _activeChats = State(initialValue: ActiveChatSessionManager(
            messageCompletionRegistry: ChatMessageCompletionRegistry(statusService: service),
            notificationService: E2EDeckReaderNotifications(),
            startsPolling: false
        ))
        deck = LearningDeck(
            id: 999_904, title: "Finetuning with sampling", sourceKind: .content,
            sourceURL: nil, sourceContentId: nil, sourceTitle: "Teaching fixture",
            sourceMetadata: [:], status: .completed, shareEnabled: false,
            viewerAvailable: true, latestSuccessfulRunId: 999_905, latestRun: nil,
            createdAt: Date(timeIntervalSince1970: 0), updatedAt: nil
        )
        viewerURL = FileManager.default.temporaryDirectory.appendingPathComponent("deck-reader-fixture.html")
        try? Self.html.write(to: viewerURL, atomically: true, encoding: .utf8)
    }

    var body: some View {
        Color.surfacePrimary
            .fullScreenCover(isPresented: .constant(true)) {
                LearningDeckReaderView(deck: deck, viewerURL: viewerURL)
                    .environment(activeChats)
                    .ignoresSafeArea(.container)
            }
    }

    private static let html = """
    <!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>html,body{margin:0;height:100%;overflow:hidden;background:#faf6ea;color:#24221a;font:18px -apple-system}
    section{box-sizing:border-box;height:100%;padding:125px 24px 120px}h1{font-size:32px;line-height:1.1}p{line-height:1.5}
    .flow{border-top:1px solid #b9b096;padding:18px 0}.note{font-size:13px}</style></head>
    <body><section class="present"><p class="note">LEARNING DECK · METHOD</p>
    <h1>Finetuning with sampling</h1><p>Make the examples work for this learner.</p>
    <div class="flow">1 · Start with expert traces</div>
    <div class="flow">2 · Sample and transform</div>
    <div class="flow">3 · Learn with the usual objective</div>
    <p class="note">A deterministic fixture for reader controls and keyboard layout.</p></section>
    <script>window.Reveal={on:function(){},getIndices:function(){return {h:0,v:0}},
    getHorizontalSlides:function(){return [document.querySelector('section')]},
    getCurrentSlide:function(){return document.querySelector('section')},toggleOverview:function(){}};</script>
    </body></html>
    """
}

private final class E2EDeckReaderStatusService: MessageStatusFetching {
    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        throw CancellationError()
    }
}

@MainActor
private final class E2EDeckReaderNotifications: ChatCompletionNotifying {
    func showChatCompletedNotification(sessionId: Int, title: String, message: String) {}
}
#endif
