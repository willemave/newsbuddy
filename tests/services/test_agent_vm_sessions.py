from __future__ import annotations

from uuid import uuid4

from app.core.settings import get_settings
from app.services.agent_vm_sessions import create_agent_vm_session
from app.services.agent_vm_tool_scripts import install_agent_vm_tool_scripts


def test_local_agent_vm_session_reports_process_namespace_reuse(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_task_sandbox_provider", "local")
    namespace = f"test:{uuid4()}"

    first = create_agent_vm_session(
        user_id=1,
        llm_task_id=1,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/one",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )
    second = create_agent_vm_session(
        user_id=1,
        llm_task_id=2,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/users/1/tasks/two",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )

    assert first.lease.vm_namespace == namespace
    assert first.lease.reuse_scope == "process_namespace"
    assert first.lease.reused is False
    assert second.lease.vm_namespace == namespace
    assert second.lease.reuse_scope == "process_namespace"
    assert second.lease.reused is True


def test_local_agent_vm_session_loads_workspace_tool_env(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_task_sandbox_provider", "local")
    monkeypatch.setenv("EXA_API_KEY", "host-secret-must-not-leak")
    namespace = f"test:{uuid4()}"
    session = create_agent_vm_session(
        user_id=1,
        llm_task_id=3,
        vm_namespace=namespace,
        workspace_path="/workspace/newsly/tasks/tool-env",
        shared_workspace_path="/workspace/newsly/users/1/shared",
        feature="test",
    )

    install_agent_vm_tool_scripts(
        session,
        llm_task_id=3,
        api_base_url="http://127.0.0.1:8000",
        task_token="test-token",
    )
    help_result = session.execute_bash("newsly-web-search --help")
    env_result = session.execute_bash("printf '%s' \"$NEWSLY_LLM_TASK_ID\"")
    secret_result = session.execute_bash("printf '%s' \"${EXA_API_KEY:-}\"")

    assert help_result.exit_code == 0
    assert "usage: newsly-web-search" in help_result.stdout
    assert env_result.stdout == "3"
    assert secret_result.stdout == ""
