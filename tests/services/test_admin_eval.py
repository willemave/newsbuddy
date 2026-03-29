"""Tests for admin eval service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.models.schema import Content
from app.services.admin_eval import (
    AdminEvalRunRequest,
    ModelPricing,
    run_admin_eval,
    select_eval_samples,
)


class _FakeResult:
    def __init__(self, output: dict, *, input_tokens: int = 120, output_tokens: int = 40):
        self.output = output
        self._usage = SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )

    def usage(self):
        return self._usage


class _FakeAgent:
    def __init__(self, output: dict):
        self._output = output

    def run_sync(self, _prompt: str, **_kwargs):
        return _FakeResult(self._output)


def _create_content(
    db_session,
    *,
    content_id: int,
    content_type: str,
    created_at: datetime,
    text_field: str,
    text_value: str,
) -> Content:
    metadata = {text_field: text_value, "summary": {"title": f"Existing {content_id}"}}
    content = Content(
        id=content_id,
        content_type=content_type,
        url=f"https://example.com/{content_type}/{content_id}",
        title=f"{content_type.title()} {content_id}",
        status="completed",
        content_metadata=metadata,
        created_at=created_at,
    )
    db_session.add(content)
    return content


def test_select_eval_samples_is_deterministic(db_session):
    base = datetime.now(UTC).replace(tzinfo=None)
    for idx in range(30):
        _create_content(
            db_session,
            content_id=idx + 1,
            content_type="article",
            created_at=base - timedelta(minutes=idx),
            text_field="content",
            text_value=f"Article body {idx}",
        )
    db_session.commit()

    first = select_eval_samples(
        db_session,
        content_types=["article"],
        recent_pool_size=20,
        sample_size=10,
        seed=7,
    )
    second = select_eval_samples(
        db_session,
        content_types=["article"],
        recent_pool_size=20,
        sample_size=10,
        seed=7,
    )

    first_ids = [entry.content_id for entry in first["article"]]
    second_ids = [entry.content_id for entry in second["article"]]

    assert first_ids == second_ids
    assert len(first_ids) == 10
    assert all(content_id <= 20 for content_id in first_ids)


def test_select_eval_samples_uses_total_budget_across_types(db_session):
    now = datetime.now(UTC).replace(tzinfo=None)
    for idx in range(5):
        _create_content(
            db_session,
            content_id=2000 + idx,
            content_type="article",
            created_at=now - timedelta(minutes=idx),
            text_field="content",
            text_value=f"Article {idx}",
        )
        _create_content(
            db_session,
            content_id=3000 + idx,
            content_type="podcast",
            created_at=now - timedelta(minutes=idx),
            text_field="transcript",
            text_value=f"Podcast {idx}",
        )
    db_session.commit()

    selected = select_eval_samples(
        db_session,
        content_types=["article", "podcast"],
        recent_pool_size=10,
        sample_size=3,
        seed=99,
    )

    total_selected = len(selected["article"]) + len(selected["podcast"])
    assert total_selected == 3
    assert len(selected["article"]) >= 1
    assert len(selected["podcast"]) >= 1


def test_run_admin_eval_uses_news_title_focus_and_cost(db_session, monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    _create_content(
        db_session,
        content_id=501,
        content_type="article",
        created_at=now,
        text_field="content",
        text_value="Article content with enough detail for testing.",
    )
    _create_content(
        db_session,
        content_id=502,
        content_type="news",
        created_at=now - timedelta(minutes=1),
        text_field="content",
        text_value="News content body for digest testing.",
    )
    db_session.commit()

    def fake_get_settings():
        return SimpleNamespace(
            openai_api_key="test-openai",
            anthropic_api_key="test-anthropic",
            google_api_key="test-google",
            cerebras_api_key="test-cerebras",
        )

    def fake_get_basic_agent(model_spec: str, output_type, _system_prompt: str):  # noqa: ANN001
        if output_type.__name__ == "NewsSummary":
            return _FakeAgent({"title": f"News from {model_spec}", "summary": "digest"})
        return _FakeAgent(
            {
                "title": f"Longform from {model_spec}",
                "points": [{"text": "Point", "detail": "Detail", "quotes": []}],
            }
        )

    monkeypatch.setattr("app.services.admin_eval.get_settings", fake_get_settings)
    monkeypatch.setattr(
        "app.services.admin_eval.get_basic_agent",
        fake_get_basic_agent,
    )

    request = AdminEvalRunRequest(
        content_types=["article", "news"],
        models=["flash_lite"],
        sample_size=2,
        recent_pool_size=10,
        seed=1,
        pricing={
            "flash_lite": ModelPricing(input_per_million_usd=1.0, output_per_million_usd=2.0)
        },
    )

    result = run_admin_eval(db_session, request)
    assert result["aggregate"]["cells_total"] == 2
    assert result["aggregate"]["cells_successful"] == 2

    news_row = next(row for row in result["results"] if row["content_type"] == "news")
    news_cell = news_row["model_results"][0]
    assert news_cell["display_output"] == {"title": news_cell["generated_title"]}
    assert news_cell["raw_output"]["summary"] == "digest"
    assert news_cell["estimated_cost_usd"] is not None
    assert news_cell["request_chars"] > 0
    assert news_cell["request_tokens_estimate"] > 0
    assert news_cell["request_tokens_actual"] == news_cell["usage"]["input_tokens"]
    assert result["aggregate"]["avg_request_chars"] is not None
    assert result["aggregate"]["avg_request_tokens_estimate"] is not None


def test_run_admin_eval_skips_unavailable_models(db_session, monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    _create_content(
        db_session,
        content_id=900,
        content_type="article",
        created_at=now,
        text_field="content",
        text_value="Sample article.",
    )
    db_session.commit()

    def fake_get_settings():
        return SimpleNamespace(
            openai_api_key=None,
            anthropic_api_key=None,
            google_api_key=None,
            cerebras_api_key=None,
        )

    monkeypatch.setattr("app.services.admin_eval.get_settings", fake_get_settings)

    request = AdminEvalRunRequest(
        content_types=["article"],
        models=["gpt_5_4", "flash_lite", "gemini_3_pro", "cerebras_glm_4_7"],
        sample_size=1,
        recent_pool_size=10,
    )

    result = run_admin_eval(db_session, request)
    assert result["available_models"] == []
    assert len(result["skipped_models"]) == 4


def test_run_admin_eval_disables_model_after_first_hard_error(db_session, monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    _create_content(
        db_session,
        content_id=1001,
        content_type="article",
        created_at=now,
        text_field="content",
        text_value="Article content one.",
    )
    _create_content(
        db_session,
        content_id=1002,
        content_type="article",
        created_at=now - timedelta(minutes=1),
        text_field="content",
        text_value="Article content two.",
    )
    db_session.commit()

    call_count = {"bad": 0, "good": 0}

    class _BadAgent:
        def run_sync(self, _prompt: str, **_kwargs):
            call_count["bad"] += 1
            raise RuntimeError("status_code: 404 model_not_found")

    class _GoodAgent:
        def run_sync(self, _prompt: str, **_kwargs):
            call_count["good"] += 1
            return _FakeResult({"title": "ok", "points": []})

    def fake_get_settings():
        return SimpleNamespace(
            openai_api_key="test-openai",
            anthropic_api_key="test-anthropic",
            google_api_key="test-google",
            cerebras_api_key="test-cerebras",
        )

    def fake_get_basic_agent(model_spec: str, _output_type, _system_prompt: str):  # noqa: ANN001
        if "cerebras" in model_spec:
            return _BadAgent()
        return _GoodAgent()

    monkeypatch.setattr("app.services.admin_eval.get_settings", fake_get_settings)
    monkeypatch.setattr(
        "app.services.admin_eval.get_basic_agent",
        fake_get_basic_agent,
    )

    request = AdminEvalRunRequest(
        content_types=["article"],
        models=["cerebras_glm_4_7", "flash_lite"],
        sample_size=2,
        recent_pool_size=10,
        seed=1,
    )

    result = run_admin_eval(db_session, request)

    # Bad model should only be attempted once, then disabled for remaining rows.
    assert call_count["bad"] == 1
    assert call_count["good"] == 2
    assert any(
        item["alias"] == "cerebras_glm_4_7" and "disabled_after_error" in item["reason"]
        for item in result["skipped_models"]
    )
