"""Shared-fixture validation and opt-in real API bootstrap, without paid calls."""

import json
import os
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from newsly_evals.chat.client import ChatClient
from newsly_evals.chat.runtime import LocalRuntime
from newsly_evals.chat.schema import Stub, read_yaml
from newsly_evals.chat.stubs import StubServer
from newsly_evals.pipeline.generator import generate_sql
from newsly_evals.pipeline.schema import Suite

ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "contracts/testing/pipeline/summarization.yaml"
PRODUCTION_SUITE = ROOT / "contracts/testing/pipeline/tech_news_production.yaml"


def test_suite_and_sql_guards() -> None:
    suite = Suite.model_validate(read_yaml(SUITE))
    assert len(suite.cases) == 14
    for case in suite.cases:
        sql = generate_sql(case, "test-fixture")
        assert "current_database() !~ '^newsly_eval_" in sql
        assert "COMMIT;" in sql
        if case.stage == "summary":
            assert '"summary":' not in sql
    with pytest.raises(ValueError):
        generate_sql(suite.cases[0], "x'; DROP DATABASE postgres;")
    raw = suite.model_dump()
    raw["cases"].append(raw["cases"][0])
    with pytest.raises(ValidationError):
        Suite.model_validate(raw)


def test_news_word_target_requires_ordered_news_briefing_range() -> None:
    production_case = Suite.model_validate(read_yaml(PRODUCTION_SUITE)).cases[0].model_dump()
    production_case["news_word_target"] = {
        "target_min": 65,
        "target_max": 45,
        "hard_max": 75,
    }
    with pytest.raises(ValidationError, match="target_min <= target_max <= hard_max"):
        Suite.model_validate({"version": 1, "cases": [production_case]})

    summary_case = Suite.model_validate(read_yaml(SUITE)).cases[0].model_dump()
    summary_case["news_word_target"] = {
        "target_min": 25,
        "target_max": 40,
        "hard_max": 40,
    }
    with pytest.raises(ValidationError, match="requires a briefing case with news sources"):
        Suite.model_validate({"version": 1, "cases": [summary_case]})


def test_embedding_stub_matches_request_batch() -> None:
    with StubServer() as stubs:
        stubs.reset([Stub(method="POST", path="/embeddings", embedding_vector=[1, 0])])
        with httpx.Client(trust_env=False) as client:
            response = client.post(
                stubs.url + "/embeddings", json={"input": ["one", "two"], "model": "fixture"}
            )
        assert response.json()["data"] == [
            {"index": 0, "embedding": [1, 0], "object": "embedding"},
            {"index": 1, "embedding": [1, 0], "object": "embedding"},
        ]


@pytest.mark.skipif(
    not os.environ.get("NEWSLY_EVAL_BIN_DIR"), reason="local Rust binaries required"
)
@pytest.mark.parametrize("suite_path", [SUITE, PRODUCTION_SUITE])
def test_pipeline_fixtures_through_rust_api(tmp_path: Path, suite_path: Path) -> None:
    suite = Suite.model_validate(read_yaml(suite_path))
    with (
        StubServer() as stubs,
        LocalRuntime(
            postgres_url="postgresql://localhost/postgres",
            binary_dir=Path(os.environ["NEWSLY_EVAL_BIN_DIR"]),
            output=tmp_path / "runtime",
            environment={},
            stubs=stubs,
            workers=(),
        ) as runtime,
    ):
        for i, case in enumerate(suite.cases):
            refs = runtime.seed(generate_sql(case, f"integration-{i}"))
            client = ChatClient(runtime.api_url, refs["user:reader"])
            try:
                for source in case.sources:
                    if source.kind != "news":
                        response = client.client.get(f"/api/content/{refs['source:' + source.id]}")
                        if case.stage == "summary":
                            assert response.status_code == 404  # unpublished source is hidden
                        else:
                            assert response.status_code == 200
                            assert response.json()["title"] == source.title
                    else:
                        news = client.request(
                            "GET", f"/api/news/items/{refs['source:' + source.id]}"
                        )
                        assert news["title"] == source.title
                assert client.request("GET", "/api/briefing")["generated_at"] is None
            finally:
                client.close()


def test_collect_waits_for_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    from newsly_evals.pipeline.runner import collect

    case = Suite.model_validate(read_yaml(SUITE)).cases[0]
    client = Mock()
    published = {
        "status": "completed",
        "summary_kind": "longform_artifact",
        "longform_artifact": {"artifact": {"type": "findings", "payload": {}}},
        "structured_summary": {"legacy": "duplicate"},
    }
    client.client.get.side_effect = [
        httpx.Response(404, request=httpx.Request("GET", "http://localhost/api/content/1")),
        httpx.Response(
            200, json=published, request=httpx.Request("GET", "http://localhost/api/content/1")
        ),
    ]
    monkeypatch.setattr("newsly_evals.pipeline.runner.time.sleep", lambda _: None)
    result = collect(client, case, {"source:trial": 1}, Mock(), 5, tmp_path)
    assert result == {"title": None, "longform_artifact": published["longform_artifact"]}
    assert json.loads((tmp_path / "last-response.json").read_text()) == published
    assert client.client.get.call_count == 2


def test_production_source_fields_reach_fixture_sql() -> None:
    suite = Suite.model_validate(read_yaml(PRODUCTION_SUITE))
    assert len(suite.cases) == 4
    for case in suite.cases:
        sql = generate_sql(case, "production-snapshot")
        for source in case.sources:
            assert source.url and "?" not in source.url
            assert source.key_points
            assert source.url in sql
        assert "summary_key_points" in sql
        assert case.expects not in sql


@pytest.mark.parametrize("artifact", [None, {}, {"artifact": "broken"}])
def test_completed_summary_requires_canonical_artifact(tmp_path: Path, artifact: object) -> None:
    from unittest.mock import Mock

    from newsly_evals.pipeline.runner import collect

    case = Suite.model_validate(read_yaml(SUITE)).cases[0]
    response = {
        "status": "completed",
        "summary_kind": "longform_artifact",
        "longform_artifact": artifact,
        "structured_summary": {"overview": "Alias exists"},
    }
    client = Mock()
    client.client.get.return_value = httpx.Response(
        200, json=response, request=httpx.Request("GET", "http://localhost/api/content/1")
    )
    with pytest.raises(RuntimeError, match="canonical longform_artifact"):
        collect(client, case, {"source:trial": 1}, Mock(), 5, tmp_path)
    assert json.loads((tmp_path / "last-response.json").read_text()) == response


def test_report_contains_canonical_output_once(tmp_path: Path) -> None:
    from newsly_evals.pipeline.runner import report

    report(
        tmp_path,
        [
            {
                "id": "article",
                "status": "pass",
                "expects": "Faithful summary",
                "output": {
                    "title": "Article",
                    "longform_artifact": {
                        "artifact": {
                            "type": "findings",
                            "payload": {"takeaway": "Unique takeaway."},
                        },
                    },
                },
            }
        ],
    )
    assert (tmp_path / "report.md").read_text().count("Unique takeaway.") == 1
    assert json.loads((tmp_path / "results.json").read_text())["version"] == 2


def test_news_density_metrics_count_rendered_run_text(tmp_path: Path) -> None:
    from newsly_evals.pipeline.runner import measure_news_passages, report
    from newsly_evals.pipeline.schema import NewsWordTarget

    output = {
        "lenses": [
            {
                "lens": {"tier": "news"},
                "segments": [
                    {
                        "id": 42,
                        "source_keys": ["news:1", "news:2"],
                        "blocks": [
                            {
                                "type": "passage",
                                "paragraphs": [
                                    {
                                        "runs": [
                                            {"kind": "source_link", "text": "Linked title"},
                                            {"kind": "text", "text": " adds useful context."},
                                        ]
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
            {"lens": {"tier": "longform"}, "segments": [{"id": 99, "blocks": []}]},
        ]
    }

    metrics = measure_news_passages(
        output, NewsWordTarget(target_min=25, target_max=45, hard_max=75)
    )
    assert metrics == [
        {
            "segment_id": 42,
            "source_count": 2,
            "rendered_word_count": 5,
            "density_band": "under_target",
            "word_target": {"target_min": 25, "target_max": 45, "hard_max": 75},
        }
    ]
    report(
        tmp_path,
        [
            {
                "id": "news",
                "status": "pass",
                "expects": "Useful density",
                "output": output,
                "metrics": {"news_passages": metrics},
            }
        ],
    )
    assert (
        "5 rendered words across 2 sources (under_target)" in (tmp_path / "report.md").read_text()
    )


@pytest.mark.skipif(
    not os.environ.get("NEWSLY_EVAL_BIN_DIR"), reason="local Rust binaries required"
)
@pytest.mark.parametrize("stored", [False, True], ids=["metadata-fallback", "file-backed"])
def test_body_api_preserves_long_transcripts(tmp_path: Path, stored: bool) -> None:
    import hashlib

    from newsly_evals.chat.generator import literal
    from newsly_evals.pipeline.schema import Case, Source

    transcript = (
        "Opening qualification.\n\n"
        + ("Speaker: Evidence remains uncertain — café.\n" * 1400)
        + "FINAL SOURCE SENTENCE."
    )
    case = Case(
        id="long_transcript",
        stage="briefing",
        expects="Complete source body",
        sources=[Source(id="episode", kind="podcast", title="Long transcript", text=transcript)],
    )
    with (
        StubServer() as stubs,
        LocalRuntime(
            postgres_url="postgresql://localhost/postgres",
            binary_dir=Path(os.environ["NEWSLY_EVAL_BIN_DIR"]),
            output=tmp_path / "runtime",
            environment={},
            stubs=stubs,
            workers=(),
        ) as runtime,
    ):
        sql = generate_sql(case, "long-transcript")
        if stored:
            body = transcript.encode()
            root = tmp_path / "runtime" / "bodies"
            root.mkdir(parents=True, exist_ok=True)
            (root / "episode.txt").write_bytes(body)
            seed_body = (
                "INSERT INTO content_bodies (content_id, variant, storage_provider, storage_key, "
                "content_format, sha256, byte_size, char_count, created_at) VALUES "
                "((SELECT id FROM eval_refs WHERE key='source:episode'), 'source', 'local', "
                f"'episode.txt', 'text', {literal(hashlib.sha256(body).hexdigest())}, "
                f"{len(body)}, {len(transcript)}, CURRENT_TIMESTAMP);\n"
                # A different fallback proves that the stored body is the one returned.
                "UPDATE contents SET content_metadata = "
                '(content_metadata::jsonb || \'{"transcript":"fallback sentinel"}\'::jsonb)::json '
                "WHERE id=(SELECT id FROM eval_refs WHERE key='source:episode');\n"
            )
            sql = sql.replace(
                "SELECT json_object_agg(key, id) FROM eval_refs;",
                seed_body + "SELECT json_object_agg(key, id) FROM eval_refs;",
            )
        refs = runtime.seed(sql)
        client = ChatClient(runtime.api_url, refs["user:reader"])
        try:
            body = client.request(
                "GET", f"/api/content/{refs['source:episode']}/body?variant=source"
            )
            assert body["text"] == transcript
            assert body["kind"] == "transcript"
        finally:
            client.close()
