"""Incremental, typed host mirror for per-user agent data."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.db import AgentDataFile, User
from app.services.agent_data_documents import collect_agent_data_documents


@dataclass(frozen=True)
class AgentDataSyncSelection:
    """The canonical identity of documents affected by one domain event."""

    content_ids: frozenset[int] = frozenset()
    news_item_ids: frozenset[int] = frozenset()
    chat_session_ids: frozenset[int] = frozenset()
    briefing_dates: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AgentDataSyncResult:
    user_id: int
    revision: int
    written_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]


@dataclass(frozen=True)
class AgentDataReconcilePage:
    """One bounded ledger slice and its next descending cursor."""

    selection: AgentDataSyncSelection
    next_before_id: int


def get_agent_data_user_root(user_id: int) -> Path:
    return (get_settings().agent_data_mirror_root / str(user_id)).resolve()


def sync_agent_data_for_user(
    db: Session,
    *,
    user_id: int,
    selection: AgentDataSyncSelection,
) -> AgentDataSyncResult:
    """Render and reconcile one typed, bounded corpus selection."""
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None or not bool(user.is_active):
        raise ValueError(f"Active user {user_id} does not exist")

    documents = collect_agent_data_documents(
        db,
        user_id=user_id,
        content_ids=set(selection.content_ids),
        news_item_ids=set(selection.news_item_ids),
        chat_session_ids=set(selection.chat_session_ids),
        briefing_dates=set(selection.briefing_dates),
    )
    current_revision = int(user.agent_data_revision or 0)
    next_revision = current_revision + 1
    root = get_agent_data_user_root(user_id)
    root.mkdir(parents=True, exist_ok=True)

    identities = _selection_identities(selection)
    ledger_rows = _selected_ledger_rows(db, user_id=user_id, identities=identities)
    ledger_by_identity = {
        (str(row.document_kind), str(row.document_key)): row for row in ledger_rows
    }
    desired_by_identity = {
        (document.document_kind, document.document_key): document for document in documents
    }

    written_paths: list[str] = []
    deleted_paths: list[str] = []
    for document in documents:
        document_bytes = document.content_bytes
        checksum_sha256 = document.checksum_sha256
        byte_size = document.byte_size
        identity = (document.document_kind, document.document_key)
        row = ledger_by_identity.get(identity)
        changed = row is None
        if row is None:
            row = AgentDataFile(
                user_id=user_id,
                document_kind=document.document_kind,
                document_key=document.document_key,
                path=document.path,
                stale_paths=[],
                revision=next_revision,
            )
            db.add(row)
            ledger_by_identity[identity] = row
        elif str(row.path) != document.path:
            old_path = str(row.path)
            _safe_target(root, old_path).unlink(missing_ok=True)
            stale_paths = {str(path) for path in (row.stale_paths or []) if isinstance(path, str)}
            stale_paths.add(old_path)
            stale_paths.discard(document.path)
            row.path = document.path
            row.stale_paths = sorted(stale_paths)
            deleted_paths.append(old_path)
            changed = True

        target = _safe_target(root, document.path)
        if row.checksum_sha256 != checksum_sha256 or not _file_checksum_matches(
            target, checksum_sha256
        ):
            _atomic_write(target, document_bytes)
            written_paths.append(document.path)
            changed = True
        if row.deleted_at is not None:
            changed = True
        row.checksum_sha256 = checksum_sha256
        row.index_record = document.index_record()
        row.byte_size = byte_size
        row.deleted_at = None
        if changed:
            row.revision = next_revision

    now = datetime.now(UTC)
    for identity, row in ledger_by_identity.items():
        if identity in desired_by_identity or row.deleted_at is not None:
            continue
        path = str(row.path)
        target = _safe_target(root, path)
        target.unlink(missing_ok=True)
        row.revision = next_revision
        row.deleted_at = now
        deleted_paths.append(path)

    changed = bool(written_paths or deleted_paths) or any(
        int(row.revision or 0) == next_revision for row in ledger_by_identity.values()
    )
    revision = next_revision if changed else current_revision
    if changed:
        user.agent_data_revision = revision
    db.flush()
    return AgentDataSyncResult(
        user_id=user_id,
        revision=revision,
        written_paths=tuple(written_paths),
        deleted_paths=tuple(sorted(deleted_paths)),
    )


def next_agent_data_reconcile_page(
    db: Session,
    *,
    user_id: int,
    before_id: int | None,
    limit: int,
) -> AgentDataReconcilePage | None:
    """Map one active checksum-ledger page back to canonical document identities."""
    query = db.query(
        AgentDataFile.id,
        AgentDataFile.document_kind,
        AgentDataFile.document_key,
    ).filter(
        AgentDataFile.user_id == user_id,
        AgentDataFile.deleted_at.is_(None),
    )
    if before_id is not None:
        query = query.filter(AgentDataFile.id < before_id)
    rows = query.order_by(AgentDataFile.id.desc()).limit(limit).all()
    if not rows:
        return None

    content_ids: set[int] = set()
    news_item_ids: set[int] = set()
    chat_session_ids: set[int] = set()
    briefing_dates: set[str] = set()
    for _row_id, raw_kind, raw_key in rows:
        kind = str(raw_kind)
        key = str(raw_key)
        if kind == "content":
            content_ids.add(_positive_document_id(key, kind=kind))
        elif kind == "news":
            news_item_ids.add(_positive_document_id(key, kind=kind))
        elif kind == "chat":
            chat_session_ids.add(_positive_document_id(key, kind=kind))
        elif kind == "briefing":
            briefing_dates.add(key)
        else:
            raise ValueError(f"Unsupported agent-data document kind: {kind}")

    return AgentDataReconcilePage(
        selection=AgentDataSyncSelection(
            content_ids=frozenset(content_ids),
            news_item_ids=frozenset(news_item_ids),
            chat_session_ids=frozenset(chat_session_ids),
            briefing_dates=frozenset(briefing_dates),
        ),
        next_before_id=min(int(row_id) for row_id, _kind, _key in rows),
    )


def publish_agent_data_index(
    db: Session,
    *,
    user_id: int,
    mark_complete: bool = False,
) -> AgentDataSyncResult:
    """Rewrite index and manifest once, publishing the manifest last."""
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None or not bool(user.is_active):
        raise ValueError(f"Active user {user_id} does not exist")
    root = get_agent_data_user_root(user_id)
    root.mkdir(parents=True, exist_ok=True)
    rows = (
        db.query(AgentDataFile.path, AgentDataFile.index_record)
        .filter(
            AgentDataFile.user_id == user_id,
            AgentDataFile.deleted_at.is_(None),
        )
        .all()
    )
    records: dict[str, dict[str, object]] = {}
    for path, index_record in rows:
        if not isinstance(index_record, dict):
            raise ValueError(f"Invalid agent-data index record for {path}")
        records[str(path)] = dict(index_record)
    _write_index(root, records)

    now = datetime.now(UTC)
    revision = int(user.agent_data_revision or 0)
    manifest = {
        "version": 1,
        "user_id": user_id,
        "revision": revision,
        "generated_at": now.isoformat(),
        "file_count": len(records),
        "complete": bool(mark_complete) or _manifest_was_complete(root),
    }
    _atomic_write(
        root / "manifest.json",
        (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    db.flush()
    return AgentDataSyncResult(
        user_id=user_id,
        revision=revision,
        written_paths=("index.jsonl", "manifest.json"),
        deleted_paths=(),
    )


def read_agent_data_manifest(user_id: int) -> dict[str, object] | None:
    path = get_agent_data_user_root(user_id) / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _selection_identities(selection: AgentDataSyncSelection) -> set[tuple[str, str]]:
    return {
        *(("content", str(value)) for value in selection.content_ids),
        *(("news", str(value)) for value in selection.news_item_ids),
        *(("chat", str(value)) for value in selection.chat_session_ids),
        *(("briefing", value) for value in selection.briefing_dates),
    }


def _selected_ledger_rows(
    db: Session,
    *,
    user_id: int,
    identities: set[tuple[str, str]],
) -> list[AgentDataFile]:
    if not identities:
        return []
    return (
        db.query(AgentDataFile)
        .filter(
            AgentDataFile.user_id == user_id,
            tuple_(AgentDataFile.document_kind, AgentDataFile.document_key).in_(sorted(identities)),
        )
        .all()
    )


def _positive_document_id(value: str, *, kind: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {kind} document key: {value}") from exc
    if parsed <= 0:
        raise ValueError(f"Invalid {kind} document key: {value}")
    return parsed


def _write_index(root: Path, records: dict[str, dict[str, object]]) -> None:
    data = "".join(
        json.dumps(records[path], sort_keys=True, separators=(",", ":")) + "\n"
        for path in sorted(records)
    )
    _atomic_write(root / "index.jsonl", data.encode("utf-8"))


def _manifest_was_complete(root: Path) -> bool:
    try:
        payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("complete") is True


def _safe_target(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Agent data path escaped user root: {relative_path}")
    return candidate


def _file_checksum_matches(path: Path, expected_checksum: str) -> bool:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected_checksum
    except OSError:
        return False


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
