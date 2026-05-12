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


def test_load_news_snapshot_admin_envelope(tmp_path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "ok": True,
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
