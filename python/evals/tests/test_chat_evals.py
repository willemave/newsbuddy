"""Local checks of fixture generation, HTTP lifecycle, and evaluation failures."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from newsly_evals.chat.client import CandidateTurnError, ChatClient
from newsly_evals.chat.generator import generate_sql, literal
from newsly_evals.chat.judge import (
    Judgment,
    evidence,
    exact_checks,
    judge_prompt,
    markdown_links,
    outcome,
    response_checks,
)
from newsly_evals.chat.runner import report
from newsly_evals.chat.runtime import local_url
from newsly_evals.chat.schema import Scenario, Stub, load_suite
from newsly_evals.chat.stubs import StubServer
from newsly_evals.chat.trace import tool_evidence

ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "python/evals/datasets/chat/podcast_history.yaml"


def test_shared_yaml_generates_repeatable_transactional_sql() -> None:
    _, scenario = load_suite(SUITE)
    sql = generate_sql(scenario, "test-run")
    assert sql == generate_sql(scenario, "test-run")
    assert "newsly_eval_" in sql
    assert "BEGIN;" in sql and sql.endswith("COMMIT;\n")
    assert "{namespace}" not in sql
    assert "eval_test-run_reader" in sql
    assert "INSERT INTO content_knowledge_saves" in sql
    assert "INSERT INTO content_read_status" in sql
    assert "json_object_agg(key, id)" in sql


def test_sql_quotes_text_and_rejects_unsafe_namespace() -> None:
    _, scenario = load_suite(SUITE)
    assert literal("O'Brien'); DROP TABLE users; --") == "'O''Brien''); DROP TABLE users; --'"
    with pytest.raises(ValueError):
        generate_sql(scenario, "bad'; DROP DATABASE postgres")
    with pytest.raises(ValueError):
        literal("nul\x00")


def test_unknown_reference_and_misspelled_fields_are_rejected() -> None:
    _, scenario = load_suite(SUITE)
    raw = scenario.model_dump()
    raw["episodes"]["ep01"]["saved_by"] = ["nonexistent"]
    with pytest.raises(ValidationError, match="unknown user"):
        Scenario.model_validate(raw)
    raw = scenario.model_dump()
    raw["epizodes"] = {}
    with pytest.raises(ValidationError):
        Scenario.model_validate(raw)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "postgresql://prod/postgres",
        "postgresql://localhost/postgres?host=prod",
    ],
)
def test_nonlocal_or_override_urls_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        local_url(url, {"http", "https", "postgresql"})


def test_missing_links_and_subscription_links_fail_even_with_judge_approval() -> None:
    suite, scenario = load_suite(SUITE)
    case = suite.cases[0]
    approved = Judgment.model_validate(
        {
            name: {"passed": True, "reason": "Good", "evidence": []}
            for name in ("result_type", "relevance", "grounding", "completeness", "destinations")
        }
    )
    for answer in [
        "Here are ten episodes.",
        "[Episode](https://feeds.example.test/test-run/best.xml)",
    ]:
        checks = exact_checks(case, scenario, "test-run", answer)
        assert outcome(checks, approved) == "fail"
    answer = "\n".join(
        f"[{item.title}]({item.url.replace('{namespace}', 'test-run')})"
        for item in list(scenario.episodes.values())[:10]
    )
    assert outcome(exact_checks(case, scenario, "test-run", answer), approved) == "pass"


def test_links_parse_references_and_ignore_code_examples() -> None:
    text = "[Episode][one]\n\n[one]: https://example.test/episode\n\n`[fake](https://bad.test)`"
    assert markdown_links(text) == ["https://example.test/episode"]


def test_private_fixture_is_not_in_judge_evidence() -> None:
    suite, scenario = load_suite(SUITE)
    authorized = json.dumps(evidence(suite.cases[0], scenario, "test-run"))
    assert "violet-otter-917" not in authorized
    assert "Confidential Orchid" not in authorized


def test_judge_rejects_malformed_output() -> None:
    with pytest.raises(ValidationError):
        Judgment.model_validate_json('{"passed": true}')


def test_shared_http_stubs_match_requests_and_fail_unexpected_calls() -> None:
    _, scenario = load_suite(SUITE)
    with StubServer() as server, httpx.Client(trust_env=False) as client:
        server.reset(scenario.stubs)
        response = client.post(server.url + "/search", json={"query": "AI podcasts"})
        assert response.status_code == 200
        assert response.json()["results"][0]["title"] == "AI Systems Podcast"
        assert client.get(server.url + "/unexpected").status_code == 502
        assert [item["matched"] for item in server.requests] == [True, False]
        server.reset([])
        assert not server.requests


def test_chat_waits_for_final_message_instead_of_partial() -> None:
    rules = [
        Stub(method="POST", path="/auth/debug/new-user", response={"access_token": "synthetic"}),
        Stub(method="POST", path="/api/content/chat/assistant/turns", response={"message_id": 10}),
        Stub(
            method="GET",
            path="/api/content/chat/messages/10/status",
            response={
                "status": "completed",
                "assistant_message": {"content": "final answer"},
                "partial_assistant_message": {"content": "wrong partial"},
            },
        ),
        Stub(
            method="GET",
            path="/api/content/chat/sessions/1",
            response={"session": {}, "messages": []},
        ),
    ]
    with StubServer() as server:
        server.reset(rules)
        client = ChatClient(server.url, 1)
        try:
            turn = client.turn(1, "query", {})
            assert turn["answer"] == "final answer"
            assert all(item["matched"] for item in server.requests)
        finally:
            client.close()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_candidate_terminal_errors_are_not_judged(status: str) -> None:
    diagnostic = {"status": status, "error": "Public failure", "tool_progress": ["Search"]}
    with patch.object(
        ChatClient,
        "request",
        side_effect=[{"access_token": "synthetic"}, {"message_id": 10}, diagnostic],
    ):
        client = ChatClient("http://127.0.0.1:1", 1)
        try:
            with pytest.raises(CandidateTurnError, match="Public failure") as failure:
                client.turn(1, "query", {})
            assert failure.value.status == diagnostic
        finally:
            client.close()


def test_candidate_timeout_is_bounded() -> None:
    with patch.object(
        ChatClient,
        "request",
        side_effect=[{"access_token": "synthetic"}, {"message_id": 10}, {"status": "processing"}],
    ):
        client = ChatClient("http://127.0.0.1:1", 1, timeout=0.001)
        try:
            with pytest.raises(TimeoutError):
                client.turn(1, "query", {})
        finally:
            client.close()


def traced_turn(tool: str, *, feed_options: list | None = None) -> dict:
    return {
        "query": "Show episodes",
        "answer": "Final answer text",
        "message_id": 2,
        "assistant": {"feed_options": feed_options or []},
        "messages": [
            {
                "source_message_id": 1,
                "role": "tool",
                "display_type": "process_summary",
                "content": "Executed 1 tool call:\n• search_content",
            },
            {
                "source_message_id": 2,
                "role": "tool",
                "display_type": "process_summary",
                "content": f"Executed 1 tool call:\n• {tool}",
            },
        ],
    }


def test_content_grade_and_judge_input_are_independent_of_tool_choice() -> None:
    suite, scenario = load_suite(SUITE)
    prompts = []
    checks = []
    for tool in ("search_subscription_feeds", "search_content", "find_feed_options"):
        turn = traced_turn(tool)
        turn["answer"] = "\n".join(
            f"[{item.title}]({item.url.replace('{namespace}', 'test-run')})"
            for item in list(scenario.episodes.values())[:10]
        )
        checks.append(
            exact_checks(suite.cases[0], scenario, "test-run", turn["answer"])
            + response_checks(suite.cases[0], [turn])
        )
        prompts.append(judge_prompt(suite.cases[0], scenario, "test-run", [turn]))
    assert checks[0] == checks[1] == checks[2]
    assert all(check.passed for check in checks[0])
    assert prompts[0] == prompts[1] == prompts[2]
    assert "tool_evidence" not in prompts[0]


def test_other_show_episode_fails_even_alongside_ten_correct_links() -> None:
    suite, scenario = load_suite(SUITE)
    answer = "\n".join(
        f"[{item.title}]({item.url.replace('{namespace}', 'test-run')})"
        for item in list(scenario.episodes.values())[:10]
    )
    wrong = scenario.episodes["wrong_show_agents"]
    answer += f"\n[{wrong.title}]({wrong.url.replace('{namespace}', 'test-run')})"
    checks = {
        check.name: check.passed
        for check in exact_checks(suite.cases[0], scenario, "test-run", answer)
    }
    assert checks["episode_link_count"] is True
    assert checks["no_unrelated_episode_links"] is False


def test_subscription_cards_fail_even_when_text_and_tool_look_correct() -> None:
    suite, _ = load_suite(SUITE)
    turn = traced_turn(
        "search_content",
        feed_options=[
            {"title": "Invest Like the Best", "feed_url": "https://feeds.example.test/best.xml"}
        ],
    )
    checks = {check.name: check.passed for check in response_checks(suite.cases[0], [turn])}
    assert checks["no_subscription_cards"] is False


def test_tool_evidence_ignores_previous_turn_and_candidate_claims() -> None:
    turn = traced_turn("search_subscription_feeds")
    turn["answer"] = "Executed 1 tool call:\n• search_content"
    assert tool_evidence(turn)["counts"] == {"search_subscription_feeds": 1}
    turn["messages"][-1]["content"] = "Unknown summary format"
    assert tool_evidence(turn)["complete"] is False
    turn["messages"] = []
    assert tool_evidence(turn)["complete"] is False


def test_judge_sees_separate_subscription_entities_and_visible_cards() -> None:
    suite, scenario = load_suite(SUITE)
    turn = traced_turn("search_content", feed_options=[{"title": "Wrong visible card"}])
    prompt = judge_prompt(suite.cases[0], scenario, "test-run", [turn])
    data = json.loads(prompt.split("EVALUATION DATA:\n")[1])
    assert data["expected_result_kind"] == "episodes"
    assert {item["kind"] for item in data["authorized_subscriptions"]} == {"subscription"}
    assert {item["kind"] for item in data["authorized_episodes"]} == {"episode"}
    assert data["conversation"][0]["feed_options"][0]["title"] == "Wrong visible card"


def test_report_shows_final_answer_tools_and_subscription_cards(tmp_path: Path) -> None:
    turn = traced_turn(
        "find_feed_options",
        feed_options=[
            {"title": "Invest Like the Best", "feed_url": "https://feeds.example.test/best.xml"}
        ],
    )
    report(tmp_path, [{"id": "case", "status": "fail", "expects": "Episodes", "turns": [turn]}])
    text = (tmp_path / "report.md").read_text()
    assert "### Final answer\n\nFinal answer text" in text
    assert "find_feed_options ×1" in text
    assert "### Subscription cards" in text
    assert "https://feeds.example.test/best.xml" in text


def test_external_reference_evidence_and_ingest_identity_are_available() -> None:
    suite, scenario = load_suite(SUITE)
    case = next(case for case in suite.cases if case.id == "recommend_podcasts")
    prompt = judge_prompt(case, scenario, "reference", [])
    assert '"authorized_external_results"' in prompt
    assert "AI Systems Podcast" in prompt
    assert "unvalidated suggestions, not verified feeds" in prompt
    assert "no particular source or subscription card is required" in prompt
    sql = generate_sql(scenario, "reference")
    assert '"feed_url": "https://feeds.example.test/reference/best.xml"' in sql
