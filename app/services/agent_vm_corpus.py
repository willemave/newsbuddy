"""Locked delta transfer and root-owned installation for E2B agent corpora."""

from __future__ import annotations

import hashlib
import io
import json
import shlex
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.db import AgentDataFile, User
from app.services.agent_data_sync import get_agent_data_user_root, read_agent_data_manifest
from app.services.agent_vm_io import (
    AGENT_VM_HYDRATION_TIMEOUT_SECONDS,
    remaining_deadline_seconds,
)
from app.services.agent_vm_runtime import AgentVmError


class AgentDataRevisionError(AgentVmError):
    """Raised when a sandbox advertises an impossible corpus revision."""


@dataclass(frozen=True)
class AgentDataTransfer:
    archive_path: Path
    from_revision: int
    to_revision: int
    full: bool
    changed_file_count: int
    deleted_path_count: int


@dataclass(frozen=True)
class AgentDataHydrationResult:
    remote_revision: int
    applied_revision: int
    full: bool
    changed_file_count: int
    deleted_path_count: int
    elapsed_ms: float


@contextmanager
def materialize_agent_data_transfer(
    db: Session,
    *,
    user_id: int,
    remote_revision: int,
    force_full: bool = False,
) -> Iterator[AgentDataTransfer | None]:
    """Create one coherent full or delta archive under the user's row lock."""
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None or not bool(user.is_active):
        raise AgentVmError(f"Cannot hydrate agent data for missing user {user_id}")

    target_revision = int(user.agent_data_revision or 0)
    if remote_revision > target_revision:
        raise AgentDataRevisionError(
            f"Sandbox corpus revision {remote_revision} is ahead of host revision {target_revision}"
        )
    if remote_revision == target_revision and not force_full:
        yield None
        return

    full = force_full or remote_revision == 0
    query = db.query(AgentDataFile).filter(AgentDataFile.user_id == user_id)
    if full:
        rows = query.order_by(AgentDataFile.path).all()
    else:
        rows = (
            query.filter(
                AgentDataFile.revision > remote_revision,
                AgentDataFile.revision <= target_revision,
            )
            .order_by(AgentDataFile.path)
            .all()
        )

    active_rows = [row for row in rows if row.deleted_at is None]
    deleted_paths = _deleted_paths(rows)
    file_count = int(
        db.query(func.count(AgentDataFile.id))
        .filter(
            AgentDataFile.user_id == user_id,
            AgentDataFile.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    host_manifest = read_agent_data_manifest(user_id) or {}
    manifest = {
        "version": 2,
        "user_id": user_id,
        "revision": target_revision,
        "generated_at": datetime.now(UTC).isoformat(),
        "file_count": file_count,
        "complete": host_manifest.get("complete") is True,
    }
    transfer_metadata = {
        "version": 1,
        "user_id": user_id,
        "from_revision": remote_revision,
        "to_revision": target_revision,
        "full": full,
    }

    archive_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f"newsly-agent-data-{user_id}-{remote_revision}-{target_revision}-",
            suffix=".tar.gz",
            delete=False,
        ) as handle:
            archive_path = Path(handle.name)
        root = get_agent_data_user_root(user_id)
        with tarfile.open(archive_path, mode="w:gz") as archive:
            _add_json(archive, "transfer.json", transfer_metadata)
            _add_json(archive, "manifest.json", manifest)
            _add_json(archive, "deletions.json", deleted_paths)
            index_name = "index_full.jsonl" if full else "index_upserts.jsonl"
            _add_bytes(archive, index_name, _index_bytes(active_rows))
            for row in active_rows:
                relative_path = _validated_relative_path(str(row.path))
                source = _safe_host_target(root, relative_path)
                checksum = str(row.checksum_sha256 or "")
                if not source.is_file() or not _checksum_matches(source, checksum):
                    raise AgentVmError(
                        f"Agent data file does not match its ledger entry: {relative_path}"
                    )
                archive.add(
                    source,
                    arcname=f"files/{relative_path}",
                    recursive=False,
                )
        yield AgentDataTransfer(
            archive_path=archive_path,
            from_revision=remote_revision,
            to_revision=target_revision,
            full=full,
            changed_file_count=len(active_rows),
            deleted_path_count=len(deleted_paths),
        )
    finally:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)


def hydrate_e2b_agent_data(
    sandbox: Any,
    db: Session,
    *,
    user_id: int,
    deadline: float | None,
) -> AgentDataHydrationResult:
    """Apply one root-owned, manifest-last corpus transfer to a sandbox."""
    started_at = perf_counter()
    remote_revision, force_full = _remote_revision(sandbox, user_id=user_id, deadline=deadline)
    with materialize_agent_data_transfer(
        db,
        user_id=user_id,
        remote_revision=remote_revision,
        force_full=force_full,
    ) as transfer:
        if transfer is None:
            return AgentDataHydrationResult(
                remote_revision=remote_revision,
                applied_revision=remote_revision,
                full=False,
                changed_file_count=0,
                deleted_path_count=0,
                elapsed_ms=(perf_counter() - started_at) * 1000,
            )

        request_timeout = _hydration_timeout(deadline)
        remote_archive = f"/tmp/newsly-agent-data-{uuid4().hex}.tar.gz"
        with transfer.archive_path.open("rb") as archive:
            sandbox.files.write(
                remote_archive,
                archive,
                request_timeout=request_timeout,
            )
        command = _install_command(remote_archive)
        try:
            result = sandbox.commands.run(
                command,
                user="root",
                timeout=request_timeout,
                request_timeout=request_timeout,
            )
        finally:
            with suppress(Exception):
                sandbox.files.remove(remote_archive, request_timeout=request_timeout)
        exit_code = int(getattr(result, "exit_code", getattr(result, "exitCode", 0)) or 0)
        if exit_code != 0:
            stderr = str(getattr(result, "stderr", "") or "")
            raise AgentVmError(f"Unable to hydrate user agent data: {stderr[:1000]}")
        return AgentDataHydrationResult(
            remote_revision=remote_revision,
            applied_revision=transfer.to_revision,
            full=transfer.full,
            changed_file_count=transfer.changed_file_count,
            deleted_path_count=transfer.deleted_path_count,
            elapsed_ms=(perf_counter() - started_at) * 1000,
        )


def _remote_revision(
    sandbox: Any,
    *,
    user_id: int,
    deadline: float | None,
) -> tuple[int, bool]:
    try:
        raw = sandbox.files.read(
            "/data/manifest.json",
            request_timeout=_hydration_timeout(deadline),
        )
        payload = json.loads(str(raw))
    except Exception:  # noqa: BLE001 - missing/corrupt manifests require a full install
        return 0, True
    if not isinstance(payload, dict):
        return 0, True
    manifest_user_id = payload.get("user_id")
    if manifest_user_id != user_id:
        return 0, True
    value = payload.get("revision")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0, True
    return value, False


def _deleted_paths(rows: list[AgentDataFile]) -> list[str]:
    paths: set[str] = set()
    for row in rows:
        if row.deleted_at is not None:
            paths.add(_validated_relative_path(str(row.path)))
        for path in row.stale_paths or []:
            if isinstance(path, str):
                paths.add(_validated_relative_path(path))
        if row.deleted_at is None:
            paths.discard(str(row.path))
    return sorted(paths)


def _index_bytes(rows: list[AgentDataFile]) -> bytes:
    records: list[tuple[str, dict[str, object]]] = []
    for row in rows:
        if not isinstance(row.index_record, dict):
            raise AgentVmError(f"Agent data index record is missing for {row.path}")
        records.append((str(row.path), dict(row.index_record)))
    return "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for _path, record in sorted(records)
    ).encode("utf-8")


def _add_json(archive: tarfile.TarFile, name: str, value: object) -> None:
    _add_bytes(
        archive,
        name,
        (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
    )


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mode = 0o444
    archive.addfile(info, io.BytesIO(data))


def _validated_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or path.parts[0] == "workspace":
        raise AgentVmError(f"Invalid agent data path: {value}")
    return path.as_posix()


def _safe_host_target(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    if candidate == root or root not in candidate.parents:
        raise AgentVmError(f"Agent data path escaped user root: {relative_path}")
    return candidate


def _checksum_matches(path: Path, expected: str) -> bool:
    if len(expected) != 64:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


def _hydration_timeout(deadline: float | None) -> float:
    remaining = remaining_deadline_seconds(deadline)
    if remaining is None:
        return float(AGENT_VM_HYDRATION_TIMEOUT_SECONDS)
    return min(float(AGENT_VM_HYDRATION_TIMEOUT_SECONDS), remaining)


def _install_command(remote_archive: str) -> str:
    archive_arg = shlex.quote(remote_archive)
    return f"""python3 - {archive_arg} <<'PY'
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

archive_path = Path(sys.argv[1])
data_root = Path('/data')
workspace = data_root / 'workspace'
corpus_roots = ('knowledge', 'content', 'news', 'briefings', 'chats')

def safe_relative(value):
    path = PurePosixPath(str(value))
    if path.is_absolute() or not path.parts or '..' in path.parts or path.parts[0] == 'workspace':
        raise RuntimeError(f'unsafe corpus path: {{value}}')
    return Path(*path.parts)

def remove_path(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)

stage = Path(tempfile.mkdtemp(prefix='newsly-agent-data-stage-'))
try:
    with tarfile.open(archive_path, 'r:gz') as bundle:
        for member in bundle.getmembers():
            safe_relative(member.name)
            if member.issym() or member.islnk():
                raise RuntimeError('corpus bundle cannot contain links')
        bundle.extractall(stage)

    metadata = json.loads((stage / 'transfer.json').read_text())
    expected_revision = int(metadata['from_revision'])
    target_revision = int(metadata['to_revision'])
    full = bool(metadata['full'])
    manifest_path = data_root / 'manifest.json'
    current_revision = 0
    if manifest_path.is_file():
        current = json.loads(manifest_path.read_text())
        current_revision = int(current.get('revision', 0))
    if not full and current_revision != expected_revision:
        raise RuntimeError(
            'corpus revision changed during delta install: '
            f'{{current_revision}} != {{expected_revision}}'
        )

    data_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    if full:
        for name in (*corpus_roots, 'index.jsonl', 'manifest.json'):
            remove_path(data_root / name)

    deletions = json.loads((stage / 'deletions.json').read_text())
    for value in deletions:
        remove_path(data_root / safe_relative(value))

    files_root = stage / 'files'
    if files_root.is_dir():
        for source in sorted(files_root.rglob('*')):
            if not source.is_file():
                continue
            relative = source.relative_to(files_root)
            safe_relative(relative.as_posix())
            destination = data_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f'.{{destination.name}}.next')
            shutil.copyfile(source, temporary)
            os.chmod(temporary, 0o444)
            temporary.replace(destination)

    index_target = data_root / 'index.jsonl'
    if full:
        index_source = stage / 'index_full.jsonl'
        records = {{}}
    else:
        if not index_target.is_file():
            raise RuntimeError('delta install requires an existing index')
        records = {{}}
        for line in index_target.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            path = str(record['path'])
            records[path] = record
        for value in deletions:
            records.pop(str(value), None)
        index_source = stage / 'index_upserts.jsonl'

    for line in index_source.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        path = str(record['path'])
        safe_relative(path)
        records[path] = record
    index_bytes = ''.join(
        json.dumps(records[path], sort_keys=True, separators=(',', ':')) + '\\n'
        for path in sorted(records)
    )
    index_temporary = data_root / '.index.jsonl.next'
    index_temporary.write_text(index_bytes)
    os.chmod(index_temporary, 0o444)
    index_temporary.replace(index_target)

    manifest = json.loads((stage / 'manifest.json').read_text())
    if int(manifest['revision']) != target_revision:
        raise RuntimeError('manifest revision does not match transfer')
    manifest_temporary = data_root / '.manifest.json.next'
    manifest_temporary.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\\n')
    os.chmod(manifest_temporary, 0o444)
    manifest_temporary.replace(manifest_path)
    os.chmod(data_root, 0o755)
    os.chown(workspace, 1000, 1000)
    os.chmod(workspace, 0o770)
finally:
    shutil.rmtree(stage, ignore_errors=True)
    archive_path.unlink(missing_ok=True)
PY"""
