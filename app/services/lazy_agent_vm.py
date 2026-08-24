"""Lazy per-turn access to a persistent per-user agent VM."""

from __future__ import annotations

import shutil
import threading

from app.services.agent_data_sync import get_agent_data_user_root
from app.services.agent_vm_local import LocalAgentVmSession
from app.services.agent_vm_runtime import AgentVmSession
from app.services.agent_vm_sessions import create_agent_vm_session


class LazyAgentVmRuntime:
    """Acquire a VM only when a registered VM tool is actually invoked."""

    def __init__(
        self,
        *,
        user_id: int,
        session_id: int,
        llm_task_id: int | None,
        feature: str,
        deadline: float | None = None,
    ) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self.llm_task_id = llm_task_id or session_id
        self.feature = feature
        self.deadline = deadline
        self._session: AgentVmSession | None = None
        self._lock = threading.Lock()

    @property
    def acquired(self) -> bool:
        return self._session is not None

    def get_session(self) -> AgentVmSession:
        existing = self._session
        if existing is not None:
            return existing
        with self._lock:
            if self._session is not None:
                return self._session
            session = create_agent_vm_session(
                user_id=self.user_id,
                llm_task_id=self.llm_task_id,
                vm_namespace=f"user:{self.user_id}",
                workspace_path=f"/data/workspace/{self.feature}/{self.session_id}",
                shared_workspace_path="/data/workspace/shared",
                feature=self.feature,
                deadline=self.deadline,
            )
            if isinstance(session, LocalAgentVmSession):
                _hydrate_local_agent_data(session, user_id=self.user_id)
            self._session = session
            return session

    def close(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            session.close()


def _hydrate_local_agent_data(session: LocalAgentVmSession, *, user_id: int) -> None:
    source_root = get_agent_data_user_root(user_id)
    if not source_root.exists():
        return
    data_root = (session.namespace_root / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    for source in source_root.iterdir():
        destination = data_root / source.name
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
