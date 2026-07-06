from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "client/newsly/newsly"
VIEWS_ROOT = APP_ROOT / "Views"


def test_phase5_zoom_transitions_pair_matched_sources_with_destinations() -> None:
    helper_source = (VIEWS_ROOT / "ContentZoomTransition.swift").read_text()
    root_tabs_source = (VIEWS_ROOT / "RootTabs.swift").read_text()
    routes_source = (VIEWS_ROOT / "ContentRoutes.swift").read_text()
    long_form_source = (VIEWS_ROOT / "LongFormView.swift").read_text()
    knowledge_history_source = (VIEWS_ROOT / "KnowledgeChatHistorySection.swift").read_text()
    chat_history_source = (VIEWS_ROOT / "ChatSessionHistoryView.swift").read_text()
    detail_source = (VIEWS_ROOT / "ContentDetailView.swift").read_text()
    detail_action_bar_source = (
        VIEWS_ROOT / "Components/DetailActionBar.swift"
    ).read_text()
    views_docs = (REPO_ROOT / "docs/codebase/client/80-views.md").read_text()

    assert "func matchedContentZoomSource" in helper_source
    assert "matchedTransitionSource(id: id, in: namespace)" in helper_source
    assert "func contentZoomNavigationTransition" in helper_source
    assert "navigationTransition(.zoom(sourceID: id, in: namespace))" in helper_source

    assert "@Namespace private var contentTransitionNamespace" in root_tabs_source
    assert "@Namespace private var chatTransitionNamespace" in root_tabs_source
    assert "contentTransitionNamespace: contentTransitionNamespace" in root_tabs_source
    assert "chatTransitionNamespace: chatTransitionNamespace" in root_tabs_source

    assert (
        ".matchedContentZoomSource(id: content.id, namespace: contentTransitionNamespace)"
        in long_form_source
    )
    content_destination_transition = (
        ".contentZoomNavigationTransition("
        "id: route.contentId, namespace: contentTransitionNamespace)"
    )
    assert content_destination_transition in routes_source

    assert (
        ".matchedContentZoomSource(id: session.id, namespace: chatTransitionNamespace)"
        in knowledge_history_source
    )
    assert (
        ".matchedContentZoomSource(id: session.id, namespace: chatTransitionNamespace)"
        in chat_history_source
    )
    assert (
        ".contentZoomNavigationTransition(id: route.sessionId, namespace: chatTransitionNamespace)"
        in routes_source
    )

    assert "@Namespace private var readerTransitionNamespace" in detail_source
    assert "readerTransitionNamespace: readerTransitionNamespace" in detail_source
    assert (
        ".matchedContentZoomSource(id: content.id, namespace: readerTransitionNamespace)"
        in detail_action_bar_source
    )
    assert "ArticleReaderView(" in detail_source
    assert (
        ".contentZoomNavigationTransition(id: content.id, namespace: readerTransitionNamespace)"
        in detail_source
    )

    assert "Long-form cards, Knowledge chat rows, and article-reader entrypoints" in views_docs
