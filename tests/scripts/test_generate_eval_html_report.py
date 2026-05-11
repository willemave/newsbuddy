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
