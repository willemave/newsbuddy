"""Opt-in local integration check using exactly the harness's YAML and HTTP stubs.

NEWSLY_EVAL_BIN_DIR=/path/to/debug pytest python/evals/tests/test_chat_local_integration.py
No model calls: the proxy has no allowed external hosts.
"""

import os
from pathlib import Path

import pytest

from newsly_evals.chat.client import ChatClient
from newsly_evals.chat.generator import generate_sql
from newsly_evals.chat.runtime import LocalRuntime
from newsly_evals.chat.schema import load_suite
from newsly_evals.chat.stubs import StubServer

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    not os.environ.get("NEWSLY_EVAL_BIN_DIR"), reason="local Rust binaries required"
)
def test_generated_state_through_real_rust_api(tmp_path: Path) -> None:
    _, scenario = load_suite(ROOT / "python/evals/datasets/chat/podcast_history.yaml")
    with (
        StubServer() as stubs,
        LocalRuntime(
            postgres_url=os.environ.get(
                "NEWSLY_EVAL_POSTGRES_URL", "postgresql://localhost/postgres"
            ),
            binary_dir=Path(os.environ["NEWSLY_EVAL_BIN_DIR"]),
            output=tmp_path / "runtime",
            environment={},
            stubs=stubs,
        ) as runtime,
    ):
        stubs.reset(scenario.stubs)
        refs = runtime.seed(generate_sql(scenario, "integration"))
        client = ChatClient(runtime.api_url, refs["user:reader"])
        try:
            states = client.content_state(
                {
                    key: refs["episode:" + key]
                    for key, item in scenario.episodes.items()
                    if "reader" in item.visible_to
                }
            )
            assert len(states) == 15
            assert sum(state["is_saved_to_knowledge"] for state in states.values()) == 2
            assert states["ep03"]["is_read"] is False
            assert states["ep01"]["is_read"] is True
            assert len(client.subscription_state()) == 3
            client.set_model(refs["session:reader"], "openai:gpt-5.6-terra")
            private = client.client.get(f"/api/content/{refs['episode:private']}")
            assert private.status_code == 404
            second = runtime.seed(generate_sql(scenario, "integration-two"))
            assert refs["user:reader"] != second["user:reader"]
            assert refs["episode:ep01"] != second["episode:ep01"]
            other_run = client.client.get(f"/api/content/{second['episode:ep01']}")
            assert other_run.status_code == 404
        finally:
            client.close()
