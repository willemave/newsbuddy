"""Pure numerical contracts for the historical pre-Jev embedding experiment."""

import importlib.util
import math
from pathlib import Path
from typing import Any

import pytest

spec = importlib.util.spec_from_file_location(
    "category_embedding_replay", Path(__file__).parents[1] / "scripts/replay_category_embeddings.py"
)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def direction(cosine: float) -> list[float]:
    return [cosine, math.sqrt(1 - cosine * cosine)]


def replay(story_vectors: list[list[float]], **settings: Any) -> dict[str, Any]:
    return runner.replay(
        {
            "categories": [{"id": "a", "vector": [1.0, 0.0], "weight": 1}],
            "stories": [{"id": str(i), "vector": vector} for i, vector in enumerate(story_vectors)],
            **settings,
        }
    )


def test_first_category_wins_ties() -> None:
    result = replay(
        [[1.0, 0.0]],
        categories=[
            {"id": "b", "vector": [1.0, 0.0], "weight": 1},
            {"id": "a", "vector": [1.0, 0.0], "weight": 1},
        ],
    )
    row = result["assignments"][0]
    assert row["category"] == "b" and row["margin"] == 0
    assert row["runner_up"] == {"category": "a", "cosine": 1.0}


@pytest.mark.parametrize(
    "score,cap,expected,stage",
    [
        (0.55, False, "a", "primary"),
        (0.549999, False, "other", "other"),
        (0.45, True, "a", "capped_absorption"),
        (0.449999, True, "other", "other"),
        (0.50, False, "other", "other"),
    ],
)
def test_inclusive_boundaries(score: float, cap: bool, expected: str, stage: str) -> None:
    result = replay([direction(score)], at_category_cap=cap, update_centroids=False)
    assert result["assignments"][0]["category"] == expected
    assert result["assignments"][0]["stage"] == stage


def test_batch_freezes_routing_but_next_batch_uses_updated_centroid() -> None:
    stories = [direction(0.6), direction(0.3)]
    frozen = replay(stories, batch_size=2)
    adaptive = replay(stories, batch_size=1)
    fixed = replay(stories, batch_size=1, update_centroids=False)
    assert [row["category"] for row in frozen["assignments"]] == ["a", "other"]
    assert [row["category"] for row in adaptive["assignments"]] == ["a", "a"]
    assert [row["category"] for row in fixed["assignments"]] == ["a", "other"]
    assert fixed["final_centroids"][0]["vector"] == [1.0, 0.0]
    assert frozen["final_centroids"][0]["vector"] == pytest.approx([0.8, 0.4])


def test_two_pass_order_and_capped_denominator() -> None:
    first, second = direction(0.5), direction(0.8)
    result = replay([first, second], at_category_cap=True, max_weight=1)
    # The later primary story updates first, then the earlier absorption story.
    expected = [
        ((initial + primary) / 2 + absorbed) / 2
        for initial, primary, absorbed in zip([1.0, 0.0], second, first, strict=True)
    ]
    assert result["final_centroids"][0]["vector"] == pytest.approx(expected)
    assert result["final_centroids"][0]["weight"] == 1


@pytest.mark.parametrize("weight", [3, 32, 100])
def test_unequal_weight_and_cap_32(weight: int) -> None:
    vector = direction(0.6)
    result = replay([vector], categories=[{"id": "a", "vector": [1.0, 0.0], "weight": weight}])
    capped = min(weight, 32)
    expected = [(capped + vector[0]) / (capped + 1), vector[1] / (capped + 1)]
    assert result["final_centroids"][0]["vector"] == pytest.approx(expected)
    assert result["final_centroids"][0]["weight"] == min(weight + 1, 32)


def test_profile_weight_zero_is_replaced_and_no_centroid_normalization() -> None:
    vector = direction(0.6)
    cold = replay([vector], categories=[{"id": "a", "vector": [1.0, 0.0], "weight": 0}])
    assert cold["final_centroids"][0] == {"id": "a", "vector": vector, "weight": 1}
    warm = replay([vector])
    assert sum(value * value for value in warm["final_centroids"][0]["vector"]) < 1.0


def test_antipodal_no_winner_matches_strict_initial_minus_one() -> None:
    result = replay([[-1.0, 0.0]], threshold=-1)
    assert result["assignments"][0]["best_category"] is None
    assert result["assignments"][0]["category"] == "other"


def test_empty_categories_and_empty_stories() -> None:
    assert replay([[1.0, 0.0]], categories=[])["assignments"][0]["category"] == "other"
    assert replay([])["assignments"] == []


@pytest.mark.parametrize(
    "vector",
    [[0.0, 0.0], [], [1.0], [float("nan"), 1.0], [float("inf"), 1.0], [True, 1.0], [1e-20, 0.0]],
)
def test_invalid_vectors(vector: list[float]) -> None:
    with pytest.raises(ValueError):
        replay([vector])


@pytest.mark.parametrize(
    "settings",
    [
        {"batch_size": 0},
        {"max_weight": 0},
        {"update_centroids": 1},
        {"threshold": float("nan")},
        {"at_category_cap": "yes"},
        {"categories": [{"id": "a", "vector": [1, 0]}] * 2},
        {"categories": [{"id": str(i), "vector": [1, 0]} for i in range(11)]},
        {"stories": [{"id": "x", "vector": [1, 0]}] * 2},
        {"categories": [{"id": "a", "vector": [1, 0], "weight": -1}]},
    ],
)
def test_invalid_settings_and_ids(settings: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replay([[1.0, 0.0]], **settings)


def test_provider_normalization_and_class_mean() -> None:
    assert runner.normalize_vector([3.0, 4.0]) == pytest.approx([0.6, 0.8])
    assert runner.mean_vector([[1.0, 0.0], [0.0, 1.0]]) == [0.5, 0.5]
    with pytest.raises(ValueError):
        runner.mean_vector([])
    with pytest.raises(ValueError):
        runner.mean_vector([[1.0, 0.0], [1.0]])
