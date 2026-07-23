import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.contracts import ContentType
from app.services.briefing import composer
from app.services.briefing.composer import (
    MAX_COMPOSE_ATTEMPTS,
    BriefingCompositionError,
    BriefingCompositionInvalidOutput,
    _news_clause,
    _parse_composer_layout_json,
    _source_payload,
    compose_window,
    plan_windows,
    process_generated_layout,
)
from app.services.briefing.layout_models import FigureBlock, PassageBlock, PullquoteBlock
from app.services.briefing.layout_policy import (
    BriefingLayoutDisposition,
    assess_briefing_layout,
)
from app.services.briefing.normalize import NormalizedLayout
from app.services.briefing.sources import BriefingSource

MALFORMED_BLOCKS = [
    {"type": "passage", "weight": "placeholder_gibberish_with_all_the_prose"},
    {"type": "figure", "weight": "source_key_markdown_placeholder_ignore"},
]
WELL_FORMED_BLOCKS = [
    {
        "type": "passage",
        "weight": "feature",
        "markdown": "[A useful article](newsly://briefing/content/1) explains the thesis.",
    }
]
SCALAR_DUMP_BLOCKS = [
    {"type": "passage", "weight": "brief", "markdown": "normal"},
    {"type": "pullquote", "text": "normal"},
    {"type": "figure", "source_key": "normal", "caption": "normal"},
]


@pytest.mark.parametrize(
    ("source_count", "expected_sizes"),
    [
        (1, [1]),
        (2, [2]),
        (3, [3]),
        (4, [4]),
        (5, [3, 2]),
        (6, [3, 3]),
        (7, [4, 3]),
        (8, [4, 4]),
        (9, [3, 3, 3]),
    ],
)
def test_plan_windows_balances_news_without_singleton_tails(
    source_count: int,
    expected_sizes: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = composer.get_settings()
    monkeypatch.setattr(settings, "briefing_news_window_max", 4)

    windows = plan_windows(list(range(source_count)), tier="news", settings=settings)

    assert [len(window) for window in windows] == expected_sizes


def test_plan_windows_keeps_longform_chunking_order(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = composer.get_settings()
    monkeypatch.setattr(settings, "briefing_window_max", 4)

    windows = plan_windows(list(range(5)), tier="longform", settings=settings)

    assert windows == [[0, 1, 2, 3], [4]]


def test_compose_window_falls_back_after_llm_unavailable() -> None:
    attempts: list[int] = []

    def fail_llm(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        raise TimeoutError("model stalled")

    segment = compose_window(
        [_source()],
        lens_key="articles",
        lens_title="Articles",
        tier="longform",
        window_index=1,
        use_llm=True,
        layout_generator=fail_llm,
    )

    assert len(attempts) == MAX_COMPOSE_ATTEMPTS
    assert segment.model == "deterministic"
    assert "llm_error_retry:1" in segment.warnings
    assert "llm_unavailable_fallback:TimeoutError" in segment.warnings
    assert segment.final_assessment is not None
    assert segment.final_assessment.disposition == BriefingLayoutDisposition.ACCEPT
    assert segment.blocks


def test_compose_window_raises_after_non_availability_errors() -> None:
    attempts: list[int] = []

    def fail_llm(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        raise ValueError("bad request")

    with pytest.raises(BriefingCompositionError, match="composition failed"):
        compose_window(
            [_source()],
            lens_key="articles",
            lens_title="Articles",
            tier="longform",
            window_index=1,
            use_llm=True,
            layout_generator=fail_llm,
        )

    assert len(attempts) == MAX_COMPOSE_ATTEMPTS


def test_layout_policy_repairs_auxiliary_debris_and_missing_coverage() -> None:
    blocks = [
        *WELL_FORMED_BLOCKS,
        {"type": "pullquote", "source_key": "content:1", "text": "normal"},
    ]
    sources = [_source(), _source(content_id=2)]

    processed = process_generated_layout(
        blocks,
        sources=sources,
        figure_budget=12,
        ensure_source_figures=True,
    )

    assert processed.raw_assessment.disposition == BriefingLayoutDisposition.REPAIR
    assert "low_signal_pullquote:1" in processed.raw_assessment.issues
    assert processed.raw_assessment.coverage.missing_source_keys == ["content:2"]
    assert processed.accepted is True
    assert processed.final_assessment is not None
    assert processed.final_assessment.disposition == BriefingLayoutDisposition.ACCEPT
    assert "low_signal_pullquote_dropped" in processed.warnings
    assert "coverage_repair:1" in processed.warnings


@pytest.mark.parametrize(
    ("blocks", "expected_issue"),
    [
        (SCALAR_DUMP_BLOCKS, "missing_usable_passage"),
        (
            [
                {
                    "type": "passage",
                    "markdown": (
                        "[Unknown](newsly://briefing/content/999) makes an unsupported claim."
                    ),
                }
            ],
            "unknown_passage_source_references:content:999",
        ),
        (
            [*WELL_FORMED_BLOCKS, {"type": "sidebar", "text": "Unexpected."}],
            "unknown_block_type:1:sidebar",
        ),
    ],
)
def test_layout_policy_retries_semantic_corruption(
    blocks: list[dict[str, object]],
    expected_issue: str,
) -> None:
    assessment = assess_briefing_layout(blocks, source_keys={"content:1"})

    assert assessment.disposition == BriefingLayoutDisposition.RETRY
    assert expected_issue in assessment.issues


def test_layout_policy_marks_unknown_auxiliary_references_repairable() -> None:
    blocks = [
        *WELL_FORMED_BLOCKS,
        {
            "type": "figure",
            "source_key": "content:999",
            "caption": "A useful contextual caption.",
        },
        {"type": "pullquote", "source_key": "content:998", "text": "A useful quote."},
    ]

    assessment = assess_briefing_layout(blocks, source_keys={"content:1"})

    assert assessment.disposition == BriefingLayoutDisposition.REPAIR
    assert assessment.unknown_source_keys == []
    assert assessment.repairable_unknown_source_keys == ["content:998", "content:999"]


def test_compose_window_retries_policy_failure_once() -> None:
    attempts: list[int] = []

    def fake_llm(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        return (MALFORMED_BLOCKS if len(attempts) == 1 else WELL_FORMED_BLOCKS), None

    segment = compose_window(
        [_source()],
        lens_key="articles",
        lens_title="Articles",
        tier="longform",
        window_index=1,
        use_llm=True,
        layout_generator=fake_llm,
    )

    assert len(attempts) == 2
    assert "llm_layout_policy_retry:1" in segment.warnings
    assert segment.final_assessment is not None
    assert segment.final_assessment.layout_valid is True


def test_compose_window_retries_production_scalar_dump() -> None:
    attempts: list[int] = []

    def fake_llm(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        return (SCALAR_DUMP_BLOCKS if len(attempts) == 1 else WELL_FORMED_BLOCKS), None

    segment = compose_window(
        [_source()],
        lens_key="articles",
        lens_title="Articles",
        tier="longform",
        window_index=1,
        use_llm=True,
        layout_generator=fake_llm,
    )

    assert len(attempts) == 2
    assert "llm_layout_policy_retry:1" in segment.warnings
    assert "normal" not in segment.narration_text.casefold()


def test_compose_window_repairs_missing_coverage_without_retry() -> None:
    attempts: list[int] = []

    def fake_llm(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        return WELL_FORMED_BLOCKS, None

    segment = compose_window(
        [_source(), _source(content_id=2)],
        lens_key="articles",
        lens_title="Articles",
        tier="longform",
        window_index=1,
        use_llm=True,
        layout_generator=fake_llm,
    )

    assert len(attempts) == 1
    assert "layout_policy_repair" in segment.warnings
    assert "coverage_repair:1" in segment.warnings
    assert segment.final_assessment is not None
    assert segment.final_assessment.layout_valid is True


def test_news_compose_retries_until_links_share_one_paragraph() -> None:
    attempts: list[int] = []
    sources = [_news_source(1), _news_source(2)]

    def fake_llm(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        separator = "\n\n" if len(attempts) == 1 else " "
        return [
            {
                "type": "passage",
                "weight": "brief",
                "markdown": (
                    "[First](newsly://briefing/news/1) explains the first item."
                    + separator
                    + "[Second](newsly://briefing/news/2) explains the second item."
                ),
            }
        ], None

    segment = compose_window(
        sources,
        lens_key="news-test",
        lens_title="Test News",
        tier="news",
        window_index=1,
        use_llm=True,
        layout_generator=fake_llm,
    )

    assert len(attempts) == 2
    assert segment.model != "deterministic"
    assert "llm_layout_policy_retry:1" in segment.warnings
    assert "coverage_repair:1" not in segment.warnings
    assert len(segment.blocks) == 1
    assert len(segment.blocks[0]["paragraphs"]) == 1


def test_news_compose_falls_back_cleanly_after_contract_failures() -> None:
    attempts: list[int] = []
    sources = [_news_source(1), _news_source(2)]

    def invalid_news_layout(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        return [
            {
                "type": "passage",
                "weight": "brief",
                "markdown": "The first item and second item are both notable.",
            }
        ], None

    segment = compose_window(
        sources,
        lens_key="news-test",
        lens_title="Test News",
        tier="news",
        window_index=1,
        use_llm=True,
        layout_generator=invalid_news_layout,
    )

    assert len(attempts) == MAX_COMPOSE_ATTEMPTS
    assert segment.model == "deterministic"
    assert "news_layout_contract_fallback" in segment.warnings
    assert not any(warning.startswith("coverage_repair:") for warning in segment.warnings)
    assert len(segment.blocks) == 1
    paragraphs = segment.blocks[0]["paragraphs"]
    assert len(paragraphs) == 1
    linked_keys = [
        run["source_key"] for run in paragraphs[0]["runs"] if run["kind"] == "source_link"
    ]
    assert linked_keys == ["news:1", "news:2"]


def test_news_compose_falls_back_cleanly_when_json_stays_invalid() -> None:
    attempts: list[int] = []
    sources = [_news_source(1), _news_source(2)]

    def invalid_json(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        raise json.JSONDecodeError("Unterminated string", '{"blocks":[{"type":"passage"', 21)

    segment = compose_window(
        sources,
        lens_key="news-test",
        lens_title="Test News",
        tier="news",
        window_index=1,
        use_llm=True,
        layout_generator=invalid_json,
    )

    assert len(attempts) == MAX_COMPOSE_ATTEMPTS
    assert segment.model == "deterministic"
    assert "news_invalid_layout_fallback" in segment.warnings
    assert len(segment.blocks) == 1
    assert len(segment.blocks[0]["paragraphs"]) == 1


def test_deterministic_news_layout_remains_one_paragraph_at_max_batch() -> None:
    segment = compose_window(
        [_news_source(index) for index in range(1, 5)],
        lens_key="news-test",
        lens_title="Test News",
        tier="news",
        window_index=1,
        use_llm=False,
    )

    assert len(segment.blocks) == 1
    paragraphs = segment.blocks[0]["paragraphs"]
    assert len(paragraphs) == 1
    linked_keys = [
        run["source_key"] for run in paragraphs[0]["runs"] if run["kind"] == "source_link"
    ]
    assert linked_keys == [f"news:{index}" for index in range(1, 5)]
    assert segment.narration_text.count(";") == 3
    assert "News summary 1; News item 2" in segment.narration_text


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("First. Second.", "First, Second"),
        ("Question?", "Question"),
        ("Bang!", "Bang"),
        ("Plain.", "Plain"),
    ],
)
def test_news_clause_preserves_internal_normalization_without_terminal_punctuation(
    sentence: str,
    expected: str,
) -> None:
    assert _news_clause(sentence) == expected


def test_compose_window_repairs_unknown_optional_figure_without_retry() -> None:
    attempts: list[int] = []
    blocks = [
        *WELL_FORMED_BLOCKS,
        {
            "type": "figure",
            "source_key": "content:999",
            "caption": "A useful contextual caption.",
        },
    ]

    def fake_llm(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        return blocks, None

    segment = compose_window(
        [_source()],
        lens_key="articles",
        lens_title="Articles",
        tier="longform",
        window_index=1,
        use_llm=True,
        layout_generator=fake_llm,
    )

    assert len(attempts) == 1
    assert "figure_unknown_source" in segment.warnings
    assert not any(warning.startswith("llm_layout_policy_retry:") for warning in segment.warnings)


def test_compose_window_raises_when_policy_failure_persists() -> None:
    attempts: list[int] = []

    def malformed_layout(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        return MALFORMED_BLOCKS, None

    with pytest.raises(BriefingCompositionInvalidOutput, match="failed policy"):
        compose_window(
            [_source()],
            lens_key="articles",
            lens_title="Articles",
            tier="longform",
            window_index=1,
            use_llm=True,
            layout_generator=malformed_layout,
        )

    assert len(attempts) == MAX_COMPOSE_ATTEMPTS


def test_compose_window_retries_when_repaired_layout_still_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    def fake_llm(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        return WELL_FORMED_BLOCKS, None

    monkeypatch.setattr(
        composer,
        "normalize_layout",
        lambda *_args, **_kwargs: NormalizedLayout(
            blocks=[], narration_text="", markdown_raw="", warnings=["forced_empty"]
        ),
    )

    with pytest.raises(BriefingCompositionInvalidOutput, match="failed policy"):
        compose_window(
            [_source()],
            lens_key="articles",
            lens_title="Articles",
            tier="longform",
            window_index=1,
            use_llm=True,
            layout_generator=fake_llm,
        )

    assert len(attempts) == MAX_COMPOSE_ATTEMPTS


def test_process_generated_layout_emits_normalization_warning_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_normalize = composer.normalize_layout

    def normalize_with_warning(*args, **kwargs):  # noqa: ANN002, ANN003
        normalized = real_normalize(*args, **kwargs)
        return NormalizedLayout(
            blocks=normalized.blocks,
            narration_text=normalized.narration_text,
            markdown_raw=normalized.markdown_raw,
            warnings=["one_normalization_warning"],
        )

    monkeypatch.setattr(composer, "normalize_layout", normalize_with_warning)

    processed = process_generated_layout(
        WELL_FORMED_BLOCKS,
        sources=[_source()],
        figure_budget=12,
        ensure_source_figures=True,
    )

    assert processed.warnings.count("one_normalization_warning") == 1


def test_compose_window_raises_when_layout_json_stays_invalid() -> None:
    attempts: list[int] = []

    def invalid_json(*_args, **_kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        raise json.JSONDecodeError("Unterminated string", '{"blocks":[{"type":"passage"', 21)

    with pytest.raises(BriefingCompositionInvalidOutput, match="invalid layout JSON"):
        compose_window(
            [_source()],
            lens_key="articles",
            lens_title="Articles",
            tier="longform",
            window_index=1,
            use_llm=True,
            layout_generator=invalid_json,
        )

    assert len(attempts) == MAX_COMPOSE_ATTEMPTS


@pytest.mark.parametrize(
    "content",
    [
        '{"blocks":[{"type":"passage","weight":"feature","markdown":"A useful brief."}]}',
        '{"layout":[{"type":"passage","weight":"feature","markdown":"A useful brief."}]}',
        '[{"type":"passage","weight":"feature","markdown":"A useful brief."}]',
        '```json\n[{"type":"passage","weight":"feature","markdown":"A useful brief."}]\n```',
    ],
)
def test_parse_composer_layout_json_accepts_supported_wrappers(content: str) -> None:
    layout = _parse_composer_layout_json(content)

    assert len(layout.blocks) == 1
    assert layout.blocks[0].type == "passage"
    assert layout.blocks[0].markdown == "A useful brief."


def test_parse_composer_layout_json_coerces_legacy_content_fields() -> None:
    layout = _parse_composer_layout_json(
        """
        {
          "blocks": [
            {"type": "passage", "content": "A useful brief."},
            {"type": "pullquote", "source_key": "content:1", "content": "A quote."},
            {
              "type": "figure",
              "source_key": "content:1",
              "content": "A caption.",
              "placement": "inset"
            }
          ]
        }
        """
    )

    passage, pullquote, figure = layout.blocks
    assert isinstance(passage, PassageBlock)
    assert isinstance(pullquote, PullquoteBlock)
    assert isinstance(figure, FigureBlock)
    assert passage.markdown == "A useful brief."
    assert pullquote.text == "A quote."
    assert figure.caption == "A caption."


def test_parse_composer_layout_json_recovers_weight_dumped_prose() -> None:
    layout = _parse_composer_layout_json(
        """
        {
          "blocks": [
            {
              "type": "passage",
              "weight": "[One source](newsly://briefing/content/1) explains the useful point."
            }
          ]
        }
        """
    )

    passage = layout.blocks[0]
    assert isinstance(passage, PassageBlock)
    assert passage.weight == "brief"
    assert (
        passage.markdown == "[One source](newsly://briefing/content/1) explains the useful point."
    )


@pytest.mark.parametrize(
    "content",
    [
        '{"blocks":[{"type":"passage","weight":"normal"}]}',
        '{"blocks":[{"type":"pullquote","text":"A quote."}]}',
        '{"blocks":[{"type":"figure","source_key":"content:1","caption":"Caption."}]}',
        ('{"blocks":[{"type":"passage","markdown":"A brief.","source_key":"content:1"}]}'),
    ],
)
def test_parse_composer_layout_json_rejects_missing_or_cross_type_fields(content: str) -> None:
    with pytest.raises(ValidationError):
        _parse_composer_layout_json(content)


def test_source_payload_includes_briefing_context_when_available() -> None:
    payload = _source_payload(_source_with_context("Long-form source detail."))

    assert payload["briefing_context"] == "Long-form source detail."


def _source(*, content_id: int = 1) -> BriefingSource:
    return _source_with_context(None, content_id=content_id)


def _source_with_context(
    briefing_context: str | None,
    *,
    content_id: int = 1,
) -> BriefingSource:
    return BriefingSource(
        source_key=f"content:{content_id}",
        kind="content",
        id=content_id,
        tier="longform",
        lens_key="articles",
        title="A useful article" if content_id == 1 else "Another useful article",
        summary="A concise summary.",
        key_points=["A concrete point."],
        url=f"https://example.com/article/{content_id}",
        image_url=None,
        thumbnail_url=None,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_type=ContentType.ARTICLE,
        briefing_context=briefing_context,
    )


def _news_source(news_id: int) -> BriefingSource:
    return BriefingSource(
        source_key=f"news:{news_id}",
        kind="news",
        id=news_id,
        tier="news",
        lens_key="news-test",
        title=f"News item {news_id}",
        summary=f"News summary {news_id}.",
        key_points=[],
        url=f"https://example.com/news/{news_id}",
        image_url=None,
        thumbnail_url=None,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_type=ContentType.NEWS,
    )
