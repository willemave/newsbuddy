//
//  E2ERouteInjector.swift
//  newsly
//

@MainActor
final class E2ERouteInjector {
    private var hasAppliedOpenChatRoute = false
    private var hasAppliedOpenContentRoute = false
    private var hasAppliedBriefingLens = false
    private var hasAppliedOpenScreen = false

    /// Opens Knowledge, Settings, or chat history at launch, for visual
    /// checks on devices where UI automation cannot tap (iPhone Duo).
    func applyOpenScreenIfNeeded(
        selectKnowledge: @escaping @MainActor () -> Void,
        openSettings: @escaping @MainActor () -> Void,
        openChatHistory: @escaping @MainActor () -> Void
    ) {
        guard !hasAppliedOpenScreen, let screen = E2ETestLaunch.openScreen else { return }
        hasAppliedOpenScreen = true
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(1))
            switch screen {
            case .knowledge: selectKnowledge()
            case .settings, .settingsDisplay: openSettings()
            case .chatHistory: openChatHistory()
            }
        }
    }

    /// Waits for the Briefing index, then selects the requested tier.
    func applyBriefingLensIfNeeded(viewModel: BriefingViewModel) {
        guard !hasAppliedBriefingLens, let lens = E2ETestLaunch.briefingLens else { return }
        hasAppliedBriefingLens = true
        Task { @MainActor in
            for _ in 0..<40 where viewModel.orderedLenses.isEmpty {
                try? await Task.sleep(for: .milliseconds(250))
            }
            if lens == "news" {
                viewModel.selectNewsTier()
            } else {
                viewModel.selectLens(key: lens)
            }
        }
    }

    func applyOpenChatRouteIfNeeded(
        openChatSession: @escaping @MainActor (ChatSessionRoute) -> Void,
        openAttachedChat: @escaping @MainActor (ChatSessionRoute) -> Void
    ) {
        guard !hasAppliedOpenChatRoute else { return }
        guard let sessionId = E2ETestLaunch.openChatSessionId else { return }

        hasAppliedOpenChatRoute = true
        let route = ChatSessionRoute(sessionId: sessionId)
        let attached = E2ETestLaunch.openChatAttached
        Task { @MainActor in
            await Task.yield()
            if attached {
                // Let the content route settle first, as a reader would.
                try? await Task.sleep(for: .seconds(1.5))
                openAttachedChat(route)
            } else {
                openChatSession(route)
            }
        }
    }

    func applyOpenContentRouteIfNeeded(
        openContentRoute: @escaping @MainActor (ContentDetailRoute) -> Void
    ) {
        guard !hasAppliedOpenContentRoute else { return }
        guard let contentId = E2ETestLaunch.openContentId else { return }

        hasAppliedOpenContentRoute = true
        let route = Self.contentRoute(
            contentId: contentId,
            rawContentType: E2ETestLaunch.openContentType
        )

        Task { @MainActor in
            await Task.yield()
            openContentRoute(route)
        }
    }

    static func contentRoute(
        contentId: Int,
        rawContentType: String?
    ) -> ContentDetailRoute {
        let contentType = APIContentType(
            rawValue: rawContentType ?? APIContentType.news.rawValue
        )
        return ContentDetailRoute(
            contentId: contentId,
            contentType: contentType,
            allContentIds: [contentId],
            navigationSurface: .briefing
        )
    }
}
