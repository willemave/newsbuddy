//
//  E2ERouteInjector.swift
//  newsly
//

@MainActor
final class E2ERouteInjector {
    private var hasAppliedOpenChatRoute = false
    private var hasAppliedOpenContentRoute = false

    func applyOpenChatRouteIfNeeded(
        openChatSession: @escaping @MainActor (ChatSessionRoute) -> Void
    ) {
        guard !hasAppliedOpenChatRoute else { return }
        guard let sessionId = E2ETestLaunch.openChatSessionId else { return }

        hasAppliedOpenChatRoute = true
        Task { @MainActor in
            await Task.yield()
            openChatSession(ChatSessionRoute(sessionId: sessionId))
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
