from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWS_ROOT = REPO_ROOT / "client/newsly/newsly/Views"
VIEW_MODELS_ROOT = REPO_ROOT / "client/newsly/newsly/ViewModels"


def test_scroll_pagination_uses_shared_threshold_modifier() -> None:
    helper_source = (VIEWS_ROOT / "Shared/PaginationScrollTrigger.swift").read_text()

    assert "func onPaginationThresholdReached" in helper_source
    assert "onScrollGeometryChange(for: Bool.self)" in helper_source
    assert "paginationTriggerDepth: CGFloat = 0.8" in helper_source

    expected_usages = {
        VIEWS_ROOT / "ShortFormView.swift": "await viewModel.loadNextPage()",
        VIEWS_ROOT / "LongFormView.swift": "await viewModel.loadNextPage()",
        VIEWS_ROOT / "Library/FavoritesView.swift": "await viewModel.loadMoreContent()",
        VIEWS_ROOT / "RecentlyReadView.swift": "await viewModel.loadMoreContent()",
        VIEWS_ROOT / "SubmissionsView.swift": "await viewModel.loadMore()",
    }

    for path, load_call in expected_usages.items():
        source = path.read_text()
        assert ".onPaginationThresholdReached {" in source
        assert load_call in source
        assert "onScrollGeometryChange(for: Bool.self)" not in source


def test_secondary_lists_do_not_paginate_from_last_row_on_appear() -> None:
    saved_source = (VIEWS_ROOT / "Library/FavoritesView.swift").read_text()
    recently_read_source = (VIEWS_ROOT / "RecentlyReadView.swift").read_text()
    submissions_source = (VIEWS_ROOT / "SubmissionsView.swift").read_text()

    assert 'Label("Load more", systemImage: "chevron.down")' not in saved_source
    assert "Task { await viewModel.loadMoreContent() }" not in saved_source
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


def test_short_form_uses_view_model_day_groups() -> None:
    view_source = (VIEWS_ROOT / "ShortFormView.swift").read_text()
    view_model_source = (VIEW_MODELS_ROOT / "ShortNewsListViewModel.swift").read_text()
    view_model_docs = (REPO_ROOT / "docs/codebase/client/70-view-models.md").read_text()

    assert "let dayGroups = viewModel.dayGroups" in view_source
    assert "ForEach(Array(dayGroups.enumerated()), id: \\.element.id)" in view_source
    assert "Array(items.enumerated())" not in view_source
    assert "calendarDayKey" not in view_source
    assert "var dayGroups: [ShortNewsDayGroup]" in view_model_source
    assert "`ShortNewsListViewModel` projects contiguous day groups" in view_model_docs


def test_root_tab_reselection_scrolls_feeds_without_long_form_refresh() -> None:
    content_view_source = (REPO_ROOT / "client/newsly/newsly/ContentView.swift").read_text()
    selection_source = (REPO_ROOT / "client/newsly/newsly/RootTabSelectionModel.swift").read_text()
    root_tabs_source = (VIEWS_ROOT / "RootTabs.swift").read_text()
    short_form_source = (VIEWS_ROOT / "ShortFormView.swift").read_text()
    long_form_source = (VIEWS_ROOT / "LongFormView.swift").read_text()
    coordinator_source = (VIEW_MODELS_ROOT / "TabCoordinatorViewModel.swift").read_text()
    root_docs = (REPO_ROOT / "docs/codebase/client/20-app-target-root.md").read_text()

    assert (
        "onLongFormRetap: { requestScrollToTop($longFormScrollToTopRequest) }"
        in content_view_source
    )
    assert (
        "onShortFormRetap: { requestScrollToTop($shortFormScrollToTopRequest) }"
        in content_view_source
    )
    assert (
        ".sensoryFeedback(.impact(weight: .light), trigger: tabRetapFeedbackTrigger)"
        in content_view_source
    )
    assert "guard tabCoordinator.selectedTab != availableTab else" in selection_source
    assert "requestScrollToTop(for: availableTab)" in selection_source
    assert "case .longContent where longFormPathIsEmpty:" in selection_source
    assert "case .shortNews where shortFormPathIsEmpty:" in selection_source
    assert "guard newTab != previousTab else { return }" in coordinator_source
    assert "long-form is not time-sensitive" in coordinator_source

    for source in (root_tabs_source, short_form_source, long_form_source):
        assert "scrollToTopRequest" in source

    assert "withAnimation(AppMotion.panel)" in short_form_source
    assert "scrollProxy.scrollTo(Self.topAnchorID, anchor: .top)" in short_form_source
    assert "withAnimation(AppMotion.panel)" in long_form_source
    assert "scrollProxy.scrollTo(Self.topAnchorID, anchor: .top)" in long_form_source
    assert "Re-selecting the active long-form or fast-news root tab" in root_docs
