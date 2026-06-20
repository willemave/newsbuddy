from __future__ import annotations

from typing import Any, cast

from app.services.agent_toolset import (
    AgentToolPolicy,
    AgentToolsetConfig,
    register_agent_vm_tools,
)


class _FakeAgent:
    def __init__(self) -> None:
        self.tool_names: list[str] = []

    def tool(self, func):
        self.tool_names.append(func.__name__)
        return func


def test_register_agent_vm_tools_respects_tool_policy() -> None:
    agent = _FakeAgent()

    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, None),
        log_event=lambda _deps, _event, _payload: None,
        user_id_getter=lambda _deps: None,
        metadata_getter=lambda _deps: {},
        config=AgentToolsetConfig(
            feature="test",
            operation_prefix="test",
            source="unit",
            tool_policy=AgentToolPolicy.from_mapping(
                {
                    "execute_bash": False,
                    "web_search": False,
                    "files": "read_only",
                }
            ),
        ),
        include_legacy_bash_alias=True,
    )

    assert agent.tool_names == ["read_file", "list_files"]


def test_register_agent_vm_tools_can_hide_direct_web_search_tool() -> None:
    agent = _FakeAgent()

    register_agent_vm_tools(
        cast(Any, agent),
        session_getter=lambda _deps: cast(Any, None),
        log_event=lambda _deps, _event, _payload: None,
        user_id_getter=lambda _deps: None,
        metadata_getter=lambda _deps: {},
        config=AgentToolsetConfig(
            feature="test",
            operation_prefix="test",
            source="unit",
            tool_policy=AgentToolPolicy.from_mapping(
                {
                    "execute_bash": True,
                    "web_search": True,
                    "files": "none",
                }
            ),
            register_direct_web_search_tool=False,
        ),
    )

    assert agent.tool_names == ["execute_bash"]
