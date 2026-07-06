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
        let rawType = E2ETestLaunch.openContentType ?? APIContentType.news.rawValue
        let contentType = APIContentType(rawValue: rawType)
        let route = ContentDetailRoute(
            contentId: contentId,
            contentType: contentType,
            allContentIds: [contentId],
            navigationSurface: contentType == .news ? .fastNews : .longForm
        )

        Task { @MainActor in
            await Task.yield()
            openContentRoute(route)
        }
    }
}
