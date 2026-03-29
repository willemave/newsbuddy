"""Tests for X digest filtering prompts and scoring."""

from types import SimpleNamespace

from app.models.user import User
from app.services.news_digest_preferences import (
    DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT,
    normalize_news_digest_preference_prompt,
    resolve_user_news_digest_preference_prompt,
)
from app.services.x_api import XTweet
from app.services.x_digest_filter import (
    X_DIGEST_FILTER_THRESHOLD,
    XDigestFilterDecision,
    XDigestFilterEvalCase,
    evaluate_x_digest_filter_cases,
    score_x_digest_candidate,
)


def _tweet() -> XTweet:
    return XTweet(
        id="123",
        text="NVIDIA says next-quarter AI inference demand is still supply constrained.",
        author_username="willem",
        author_name="Willem",
        created_at="2026-03-26T10:00:00Z",
        like_count=25,
        retweet_count=4,
        reply_count=2,
    )


def test_resolve_user_news_digest_preference_prompt_defaults_when_empty() -> None:
    """Missing or blank stored prompts should use the default instructions."""
    user = User(apple_id="a", email="test@example.com", news_digest_preference_prompt="   ")

    assert normalize_news_digest_preference_prompt("   ") is None
    assert (
        resolve_user_news_digest_preference_prompt(user)
        == DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT
    )


def test_score_x_digest_candidate_accepts_at_threshold(monkeypatch) -> None:
    """Scores at the fixed threshold should be accepted."""

    def fake_get_basic_agent(_model_spec, output_cls, _system_prompt):
        class _Agent:
            def run_sync(self, _prompt, model_settings=None):  # noqa: ANN001
                return SimpleNamespace(
                    output=output_cls(
                        score=X_DIGEST_FILTER_THRESHOLD,
                        reason="Concrete earnings and supply signal.",
                    )
                )

        return _Agent()

    monkeypatch.setattr("app.services.x_digest_filter.get_basic_agent", fake_get_basic_agent)

    decision = score_x_digest_candidate(
        tweet=_tweet(),
        user_prompt=DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT,
        source_type="x_timeline",
        source_label="X Following",
    )

    assert decision == XDigestFilterDecision(
        score=X_DIGEST_FILTER_THRESHOLD,
        reason="Concrete earnings and supply signal.",
        accepted=True,
        errored=False,
    )


def test_score_x_digest_candidate_handles_invalid_agent_output(monkeypatch) -> None:
    """Malformed agent output should safely fall back to a rejected decision."""

    def fake_get_basic_agent(_model_spec, _output_cls, _system_prompt):
        class _Agent:
            def run_sync(self, _prompt, model_settings=None):  # noqa: ANN001
                return SimpleNamespace()

        return _Agent()

    monkeypatch.setattr("app.services.x_digest_filter.get_basic_agent", fake_get_basic_agent)

    decision = score_x_digest_candidate(
        tweet=_tweet(),
        user_prompt=DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT,
        source_type="x_list",
        source_label="Semis",
    )

    assert decision.accepted is False
    assert decision.errored is True
    assert decision.score == 0.0


def test_evaluate_x_digest_filter_cases_reports_pass_and_fail(monkeypatch) -> None:
    """Eval helper should mark each case with a pass/fail outcome."""
    cases = [
        XDigestFilterEvalCase(
            name="include_concrete_update",
            tweet=XTweet(id="accept", text="Semiconductor capex is rising.", author_username="a"),
            user_prompt=DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT,
            source_type="x_timeline",
            source_label="X Following",
            expected_accept=True,
        ),
        XDigestFilterEvalCase(
            name="exclude_low_signal_hype",
            tweet=XTweet(id="reject", text="gm this is so bullish lol", author_username="b"),
            user_prompt=DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT,
            source_type="x_timeline",
            source_label="X Following",
            expected_accept=False,
        ),
    ]

    def fake_score_x_digest_candidate(*, tweet, user_prompt, source_type, source_label):  # noqa: ANN001
        assert user_prompt == DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT
        assert source_type == "x_timeline"
        assert source_label == "X Following"
        if tweet.id == "accept":
            return XDigestFilterDecision(
                score=0.9,
                reason="Concrete operating update.",
                accepted=True,
            )
        return XDigestFilterDecision(
            score=0.2,
            reason="Low-context hype.",
            accepted=True,
        )

    monkeypatch.setattr(
        "app.services.x_digest_filter.score_x_digest_candidate",
        fake_score_x_digest_candidate,
    )

    results = evaluate_x_digest_filter_cases(cases=cases)

    assert [result.case_name for result in results] == [
        "include_concrete_update",
        "exclude_low_signal_hype",
    ]
    assert results[0].passed is True
    assert results[1].passed is False
    assert results[1].decision.reason == "Low-context hype."


def test_default_prompt_eval_examples_cover_include_and_exclude_cases(monkeypatch) -> None:
    """Default prompt eval cases should exercise expected include/exclude behavior."""
    cases = [
        XDigestFilterEvalCase(
            name="include_semiconductor_supply_update",
            tweet=XTweet(
                id="101",
                text="TSMC says CoWoS capacity will double next year to meet AI demand.",
                author_username="supplychain",
                author_name="Supply Chain Weekly",
                like_count=120,
                retweet_count=18,
            ),
            user_prompt=DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT,
            source_type="x_timeline",
            source_label="X Following",
            expected_accept=True,
        ),
        XDigestFilterEvalCase(
            name="include_original_product_note",
            tweet=XTweet(
                id="102",
                text=(
                    "We shipped native background sync today and cut median refresh "
                    "latency by 42%."
                ),
                author_username="builder",
                author_name="Builder",
            ),
            user_prompt=DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT,
            source_type="x_list",
            source_label="Product",
            expected_accept=True,
        ),
        XDigestFilterEvalCase(
            name="exclude_engagement_bait",
            tweet=XTweet(
                id="201",
                text="gm if you're bullish on AI smash like and follow",
                author_username="hypeposter",
            ),
            user_prompt=DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT,
            source_type="x_timeline",
            source_label="X Following",
            expected_accept=False,
        ),
        XDigestFilterEvalCase(
            name="exclude_pure_self_promo",
            tweet=XTweet(
                id="202",
                text="My premium newsletter is 20% off today. Subscribe now for alpha.",
                author_username="marketer",
            ),
            user_prompt=DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT,
            source_type="x_list",
            source_label="Markets",
            expected_accept=False,
        ),
    ]

    def fake_get_basic_agent(_model_spec, output_cls, _system_prompt):
        class _Agent:
            def run_sync(self, prompt, model_settings=None):  # noqa: ANN001
                assert DEFAULT_NEWS_DIGEST_PREFERENCE_PROMPT in prompt
                if "TSMC says CoWoS capacity will double" in prompt:
                    return SimpleNamespace(
                        output=output_cls(
                            score=0.94,
                            reason="Concrete supply and capacity update.",
                        )
                    )
                if "cut median refresh latency by 42%" in prompt:
                    return SimpleNamespace(
                        output=output_cls(
                            score=0.88,
                            reason="Specific firsthand product metric.",
                        )
                    )
                if "smash like and follow" in prompt:
                    return SimpleNamespace(
                        output=output_cls(
                            score=0.08,
                            reason="Engagement bait without substance.",
                        )
                    )
                return SimpleNamespace(
                    output=output_cls(
                        score=0.12,
                        reason="Promotional post without new information.",
                    )
                )

        return _Agent()

    monkeypatch.setattr("app.services.x_digest_filter.get_basic_agent", fake_get_basic_agent)

    results = evaluate_x_digest_filter_cases(cases=cases)

    assert all(result.passed for result in results)
    assert [result.expected_accept for result in results] == [True, True, False, False]
    assert [result.decision.accepted for result in results] == [True, True, False, False]
