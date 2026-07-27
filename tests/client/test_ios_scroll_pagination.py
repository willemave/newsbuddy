from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWS_ROOT = REPO_ROOT / "client/newsly/newsly/Views"


def test_scroll_pagination_uses_shared_threshold_modifier() -> None:
    helper_source = (VIEWS_ROOT / "Shared/PaginationScrollTrigger.swift").read_text()

    assert "func onPaginationThresholdReached" in helper_source
    assert "onScrollGeometryChange(for: Bool.self)" in helper_source
    assert "paginationTriggerDepth: CGFloat = 0.8" in helper_source

    expected_usages = {
        VIEWS_ROOT / "KnowledgeView.swift": "await viewModel.loadMoreContent()",
        VIEWS_ROOT / "RecentlyReadView.swift": "await viewModel.loadMoreContent()",
        VIEWS_ROOT / "SubmissionsView.swift": "await viewModel.loadMore()",
    }

    for path, load_call in expected_usages.items():
        source = path.read_text()
        assert ".onPaginationThresholdReached {" in source
        assert load_call in source
        assert "onScrollGeometryChange(for: Bool.self)" not in source


def test_secondary_lists_do_not_paginate_from_last_row_on_appear() -> None:
    saved_source = (VIEWS_ROOT / "KnowledgeView.swift").read_text()
    recently_read_source = (VIEWS_ROOT / "RecentlyReadView.swift").read_text()
    submissions_source = (VIEWS_ROOT / "SubmissionsView.swift").read_text()

    assert 'Label("Load more", systemImage: "chevron.down")' not in saved_source
    assert "content.id == viewModel.contents.last?.id" not in saved_source
    assert "content.id == viewModel.contents.last?.id" not in recently_read_source
    assert "Task { await viewModel.loadMoreContent() }" not in recently_read_source
    assert "submission.id == viewModel.submissions.last?.id" not in submissions_source
    assert "Task { await viewModel.loadMore() }" not in submissions_source


def test_scroll_pagination_is_documented() -> None:
    views_docs = (REPO_ROOT / "docs/codebase/client/80-views.md").read_text()
    shared_docs = (REPO_ROOT / "docs/codebase/client/84-views-shared.md").read_text()

    assert "onPaginationThresholdReached" in views_docs
    assert "`PaginationScrollTrigger`" in shared_docs
    assert "`PaginationScrollTrigger.swift`" in shared_docs
