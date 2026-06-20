import json

from scripts import generate_eval_html_report as report


def test_parse_news_prompt_variants_all_expands_in_order() -> None:
    assert report.parse_news_prompt_variants("all") == list(report.NEWS_PROMPT_VARIANT_ORDER)


def test_parse_news_statuses_all_expands_in_order() -> None:
    assert report.parse_news_statuses("all") == list(report.NEWS_STATUS_ORDER)


def test_parse_news_statuses_rejects_unknown() -> None:
    try:
        report.parse_news_statuses("ready,unknown")
    except ValueError as exc:
        assert "Unknown news statuses" in str(exc)
    else:
        raise AssertionError("Expected unknown news status to raise")


def test_build_prompt_definitions_expands_news_variants() -> None:
    definitions = report.build_prompt_definitions(
        content_types=["news"],
        longform_template="source_aware_editorial_v2",
        custom_longform_system_prompt=None,
        custom_longform_user_template=None,
        custom_longform_output_type="editorial_narrative",
        custom_news_system_prompt=None,
        custom_news_user_template=None,
        custom_news_output_type="news",
        news_prompt_variants=["current", "reader_impact"],
    )

    assert [definition["prompt_variant"] for definition in definitions] == [
        "current",
        "reader_impact",
    ]
    assert {definition["prompt_type"] for definition in definitions} == {"news"}
    assert definitions[0]["system_prompt"] != definitions[1]["system_prompt"]


def test_build_prompt_definitions_includes_key_point_variants() -> None:
    definitions = report.build_prompt_definitions(
        content_types=["news"],
        longform_template="source_aware_editorial_v2",
        custom_longform_system_prompt=None,
        custom_longform_user_template=None,
        custom_longform_output_type="editorial_narrative",
        custom_news_system_prompt=None,
        custom_news_user_template=None,
        custom_news_output_type="news",
        news_prompt_variants=["key_point_depth", "source_backed_four"],
    )

    assert [definition["prompt_variant"] for definition in definitions] == [
        "key_point_depth",
        "source_backed_four",
    ]
    assert "3-4" in definitions[0]["system_prompt"]
    assert "prefer 4" in definitions[1]["system_prompt"]


def test_resolve_prompt_for_source_uses_news_variant() -> None:
    system_prompt, user_template, prompt_type = report.resolve_prompt_for_source(
        content_type="news",
        source_url=None,
        longform_template="source_aware_editorial_v2",
        custom_longform_system_prompt=None,
        custom_longform_user_template=None,
        custom_longform_output_type="editorial_narrative",
        custom_news_system_prompt=None,
        custom_news_user_template=None,
        custom_news_output_type="news",
        news_prompt_variant="evidence_first",
    )

    assert prompt_type == "news"
    assert "careful news summarization editor" in system_prompt
    assert "{content}" in user_template


def test_resolve_prompt_for_source_can_use_longform_artifact_template() -> None:
    system_prompt, user_template, prompt_type = report.resolve_prompt_for_source(
        content_type="podcast",
        source_url="https://example.com/show",
        longform_template="longform_artifact_v1",
        custom_longform_system_prompt=None,
        custom_longform_user_template=None,
        custom_longform_output_type="editorial_narrative",
        custom_news_system_prompt=None,
        custom_news_user_template=None,
        custom_news_output_type="news",
    )

    assert prompt_type == "longform_artifact"
    assert "selection_trace" in system_prompt
    assert "Source hint: podcast:conversation" in user_template
    assert "{content}" in user_template


def test_render_output_payload_includes_longform_artifact_sections() -> None:
    html = report._render_output_payload(
        {
            "title": "How teams learn from outages",
            "one_line": "A practical look at post-incident learning.",
            "ask": "copy",
            "artifact": {
                "type": "playbook",
                "payload": {
                    "quotes": [
                        {"text": "The review found the alert did not fire.", "attribution": "host"}
                    ],
                    "extras": {
                        "situation": "A platform team needs to make incident reviews useful.",
                        "outcome": "Reviews produce concrete changes rather than ritual notes.",
                        "evidence": ["Alert coverage missed the failing component."],
                    },
                    "key_points": [
                        {
                            "heading": "Start from the timeline",
                            "content": (
                                "Anchor the review in observed events before discussing fixes."
                            ),
                        }
                    ],
                    "takeaway": "Use the outage record to change the next operating decision.",
                },
            },
            "selection_trace": {
                "source_hint": "podcast:conversation",
                "candidates": ["playbook", "portrait", "mental_model"],
                "selected": "playbook",
                "reason": "The source explains a repeatable incident-review workflow.",
                "confidence": 0.82,
            },
            "feed_preview": {
                "title": "Incident reviews that change behavior",
                "one_line": "A guide to making reviews operational.",
                "preview_bullets": ["Build the timeline first."],
                "reason_to_read": "Useful when your reviews do not change follow-up work.",
                "artifact_type": "playbook",
            },
        }
    )

    assert "Selection Trace" in html
    assert "Extras" in html
    assert "Start from the timeline" in html
    assert "podcast:conversation" in html


def test_load_news_snapshot_export_envelope(tmp_path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ok": True,
                "command": "export.news-items-raw-snapshot",
                "data": {
                    "rows": [
                        {
                            "id": 123,
                            "platform": "hackernews",
                            "source_label": "Hacker News",
                            "article_url": "https://example.com/story",
                            "article_domain": "example.com",
                            "raw_metadata": {
                                "article": {"title": "Example launches richer summaries"},
                                "summary": {"title": "Example launches richer summaries"},
                            },
                            "summary_key_points": [
                                "First live point.",
                                {"text": "Second live point."},
                            ],
                            "summary_text": "The current live summary text.",
                            "article_body_text": "Line one.\n\n## Heading\n\nLine two.",
                            "status": "ready",
                            "ingested_at": "2026-05-12T19:00:00",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    sources, missing_ids = report.select_news_snapshot_eval_sources(
        snapshot_file=str(snapshot_path),
        sample_size=10,
    )

    assert missing_ids == []
    assert len(sources) == 1
    source = sources[0]
    assert source.content_id == 123
    assert source.source_title == "Example launches richer summaries"
    assert source.existing_summary_key_points == ["First live point.", "Second live point."]
    assert source.existing_summary_text == "The current live summary text."
    assert "Hacker News" in source.input_text
    assert "Line one.\n\n## Heading\n\nLine two." in source.input_text


def test_strip_urls_from_processed_raw_markdown_preserves_link_text() -> None:
    stripped = report.strip_urls_from_processed_raw_markdown(
        "Article URL: https://example.com/story\n"
        "Read [the article](https://example.com/story?x=1) and "
        "![chart](https://example.com/chart.png).\n"
        "[![nested](https://example.com/nested.png)](https://example.com/nested-story)\n"
        "[ref]: https://example.com/ref\n"
        "[email](mailto:reader@example.com?subject=Story)\n"
        '<a href="https://example.com/path">label</a>\n'
        "Bare: www.example.com/path\n"
        "Fragment: https://\n"
        "Encoded title fragment %20https:// - Save\n"
        "[[image: headshot]Reporter]("
    )

    assert "http" not in stripped
    assert "www." not in stripped
    assert "the article" in stripped
    assert "[image: chart]" in stripped
    assert "[image: nested]" in stripped
    assert "[image: headshot]Reporter" in stripped
    assert "[[image:" not in stripped
    assert "[ref]:" not in stripped
    assert "mailto:" not in stripped
    assert "email" in stripped
    assert "<a>label</a>" in stripped


def test_analyze_source_input_quality_classifies_metadata_only() -> None:
    quality = report.analyze_source_input_quality(
        "Create a compact short-form news summary grounded only in this evidence.\n"
        "Article title: Example\n\n"
        "Excerpt:\nOnly an aggregator excerpt was available."
    )

    assert quality["status"] == "metadata_only"
    assert quality["body_chars"] == 0
    assert quality["excerpt_chars"] > 0


def test_analyze_source_input_quality_flags_extractor_noise() -> None:
    quality = report.analyze_source_input_quality(
        "Create a compact short-form news summary grounded only in this evidence.\n"
        "Article title: Example\n\n"
        "Article body:\n"
        "Skip to main content\n\n"
        "The company announced the real update in one paragraph.\n\n"
        "## Read Next\n"
        "[Related story](https://example.com/related)\n"
        "Our Standards: The Thomson Reuters Trust Principles."
    )

    assert quality["status"] == "extractor_noise"
    assert "read next" in " ".join(quality["noise_markers"]).lower()


def test_analyze_source_input_quality_allows_link_rich_github_readmes() -> None:
    quality = report.analyze_source_input_quality(
        "Article domain: github.com\n"
        "Article URL: https://github.com/example/project\n\n"
        "Article body:\n"
        "# Example Project\n\n"
        + " ".join(f"[Reference {index}](https://example.com/{index})" for index in range(80))
        + "\n\n"
        + ("This README explains the project architecture and usage. " * 60)
    )

    assert quality["status"] == "full_body"


def test_analyze_source_input_quality_allows_link_rich_espn_articles() -> None:
    quality = report.analyze_source_input_quality(
        "Article domain: espn.com\n"
        "Article URL: https://www.espn.com/mlb/story/_/id/1/example\n\n"
        "Article body:\n"
        "ST. LOUIS -- "
        + " ".join(f"[Player {index}](https://espn.com/player/{index})" for index in range(35))
        + "\n\n"
        + ("The game story includes scoring details and quotes from players. " * 45)
    )

    assert quality["status"] == "full_body"


def test_clean_processed_source_input_article_body_applies_publisher_cleanup() -> None:
    processed_input = (
        "Create a compact short-form news summary grounded only in this evidence.\n"
        "Article domain: reuters.com\n"
        "Article URL: https://www.reuters.com/example/story\n\n"
        "Article body:\n"
        "[Skip to main content](https://www.reuters.com/example/story#main-content) "
        "LOS ANGELES, May 12 (Reuters) - The real article body starts here. "
        "Advertisement · Scroll to continue "
        "It includes another useful sentence. "
        "Reporting by Reporter; Editing by Editor "
        "Our Standards: The Thomson Reuters Trust Principles.\n\n"
        "Excerpt:\n"
        "Aggregator excerpt."
    )

    cleaned = report.clean_processed_source_input_article_body(processed_input)

    assert "Article body:\nLOS ANGELES, May 12 (Reuters) -" in cleaned
    assert "Skip to main content" not in cleaned
    assert "Advertisement" not in cleaned
    assert "Reporting by" not in cleaned
    assert "another useful sentence.\n\nExcerpt:" in cleaned
    assert "Excerpt:\nAggregator excerpt." in cleaned


def test_build_source_input_quality_domain_counts_groups_weak_rows() -> None:
    rows = [
        {
            "url": "https://example.com/one",
            "input_text": "Article body:\nShort.",
            "model_results": [],
        },
        {
            "url": "https://example.com/two",
            "input_text": "Excerpt:\nOnly metadata.",
            "model_results": [],
        },
        {
            "url": "https://full.example/three",
            "input_text": "Article body:\n" + ("Complete article sentence. " * 80),
            "model_results": [],
        },
    ]

    counts = report.build_source_input_quality_domain_counts(rows)

    assert counts == [
        {
            "domain": "example.com",
            "total": 2,
            "statuses": {"short_body": 1, "metadata_only": 1},
        }
    ]


def test_render_html_includes_collapsed_source_input() -> None:
    html = report.render_html(
        {
            "run_completed_at": "2026-05-12T00:00:00+00:00",
            "config": {
                "content_types": ["news"],
                "sample_size": 1,
                "recent_pool_size": 1,
                "longform_template": "source_aware_editorial_v2",
                "news_prompt_variants": ["key_point_depth"],
                "news_statuses": ["ready"],
                "news_require_article_body": False,
                "seed": 1,
            },
            "aggregate": {
                "items_total": 1,
                "cells_total": 0,
                "cells_successful": 0,
                "cells_failed": 0,
            },
            "available_models": [],
            "skipped_models": [],
            "prompt_definitions": [],
            "results": [
                {
                    "content_id": 123,
                    "content_type": "news",
                    "created_at": "2026-05-12T00:00:00+00:00",
                    "url": "https://example.com/story",
                    "source_title": "Example story",
                    "existing_summary_title": None,
                    "existing_summary_key_points": [],
                    "existing_summary_text": None,
                    "input_text": "Article title: Example <story>\n\nArticle body:\nRaw markdown",
                    "input_chars": 60,
                    "model_results": [],
                }
            ],
        }
    )

    assert "Source Body Quality" in html
    assert 'data-quality-search placeholder="Filter title, domain, or ID"' in html
    assert 'data-filter-quality="weak"' in html
    assert 'data-source-quality="short_body"' in html
    assert "Domains Needing Attention" in html
    assert "example.com" in html
    assert "Short body" in html
    assert "<summary>Processed raw markdown</summary>" in html
    assert "Article title: Example &lt;story&gt;" in html


def test_render_html_includes_cleaned_source_preview_when_body_changes() -> None:
    html = report.render_html(
        {
            "run_completed_at": "2026-05-12T00:00:00+00:00",
            "config": {
                "content_types": ["news"],
                "sample_size": 1,
                "recent_pool_size": 1,
                "longform_template": "source_aware_editorial_v2",
                "news_prompt_variants": ["key_point_depth"],
                "news_statuses": ["ready"],
                "news_require_article_body": False,
                "seed": 1,
            },
            "aggregate": {
                "items_total": 1,
                "cells_total": 0,
                "cells_successful": 0,
                "cells_failed": 0,
            },
            "available_models": [],
            "skipped_models": [],
            "prompt_definitions": [],
            "results": [
                {
                    "content_id": 123,
                    "content_type": "news",
                    "created_at": "2026-05-12T00:00:00+00:00",
                    "url": "https://www.reuters.com/example/story",
                    "source_title": "Reuters story",
                    "existing_summary_title": None,
                    "existing_summary_key_points": [],
                    "existing_summary_text": None,
                    "input_text": (
                        "Article domain: reuters.com\n"
                        "Article URL: https://www.reuters.com/example/story\n\n"
                        "Article body:\n"
                        "[Skip to main content](https://www.reuters.com/example/story) "
                        "LOS ANGELES, May 12 (Reuters) - The article body. "
                        "Advertisement · Scroll to continue "
                        "Reporting by Reporter; Editing by Editor"
                    ),
                    "input_chars": 240,
                    "model_results": [],
                }
            ],
        }
    )

    assert "Cleaned source markdown preview (not used for this run)" in html
    assert "Article body:\nLOS ANGELES, May 12 (Reuters) -" in html


def test_render_html_can_strip_source_input_urls() -> None:
    html = report.render_html(
        {
            "run_completed_at": "2026-05-12T00:00:00+00:00",
            "config": {
                "content_types": ["news"],
                "sample_size": 1,
                "recent_pool_size": 1,
                "longform_template": "source_aware_editorial_v2",
                "news_prompt_variants": ["key_point_depth"],
                "news_statuses": ["ready"],
                "news_require_article_body": False,
                "strip_source_input_urls": True,
                "seed": 1,
            },
            "aggregate": {
                "items_total": 1,
                "cells_total": 0,
                "cells_successful": 0,
                "cells_failed": 0,
            },
            "available_models": [],
            "skipped_models": [],
            "prompt_definitions": [],
            "results": [
                {
                    "content_id": 123,
                    "content_type": "news",
                    "created_at": "2026-05-12T00:00:00+00:00",
                    "url": "https://example.com/story",
                    "source_title": "Example story",
                    "existing_summary_title": None,
                    "existing_summary_key_points": [],
                    "existing_summary_text": None,
                    "input_text": (
                        "Article URL: https://example.com/story\n\n"
                        "Article body:\nRead [the article](https://example.com/story)."
                    ),
                    "input_chars": 88,
                    "model_results": [],
                }
            ],
        }
    )

    assert "<summary>Processed raw markdown (URLs stripped)</summary>" in html
    assert "Read the article." in html
    assert "Article URL: https://example.com/story" not in html
