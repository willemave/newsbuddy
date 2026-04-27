"""Machine-oriented additive API surface for the remote agent CLI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.commands import (
    complete_agent_onboarding,
    generate_agent_digest,
    start_agent_onboarding,
)
from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.common import (
    AgentDigestRequest,
    AgentDigestResponse,
    AgentLibraryDocumentResponse,
    AgentLibraryFileResponse,
    AgentLibraryManifestResponse,
    AgentOnboardingCompleteRequest,
    AgentOnboardingStartRequest,
    AgentOnboardingStartResponse,
    AgentSearchRequest,
    AgentSearchResponse,
    CliLinkApproveRequest,
    CliLinkApproveResponse,
    CliLinkPollResponse,
    CliLinkStartRequest,
    CliLinkStartResponse,
    JobStatusResponse,
    OnboardingDiscoveryStatusResponse,
)
from app.models.user import User
from app.queries import (
    get_agent_onboarding_status,
    get_job_status,
    search_external_results,
)
from app.services.cli_link import (
    approve_cli_link_session,
    poll_cli_link_session,
    start_cli_link_session,
)
from app.services.personal_markdown_library import collect_personal_markdown_documents_for_user

router = APIRouter(tags=["agent"])


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(
    job_id: int,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> JobStatusResponse:
    """Return async job status."""
    del current_user
    return get_job_status.execute(db, job_id=job_id)


@router.post("/agent/search", response_model=AgentSearchResponse)
def search_agent(
    payload: AgentSearchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentSearchResponse:
    """Search external/provider-backed sources for the agent CLI."""
    del current_user
    return search_external_results.execute(
        query=payload.query,
        limit=payload.limit,
        include_podcasts=payload.include_podcasts,
    )


@router.post("/agent/onboarding", response_model=AgentOnboardingStartResponse)
async def start_onboarding(
    payload: AgentOnboardingStartRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentOnboardingStartResponse:
    """Start simplified async onboarding."""
    return await start_agent_onboarding.execute(
        db,
        user_id=require_user_id(current_user),
        payload=payload,
    )


@router.get(
    "/agent/onboarding/{run_id}",
    response_model=OnboardingDiscoveryStatusResponse,
)
def get_onboarding(
    run_id: int,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> OnboardingDiscoveryStatusResponse:
    """Return onboarding run status."""
    return get_agent_onboarding_status.execute(
        db,
        user_id=require_user_id(current_user),
        run_id=run_id,
    )


@router.post(
    "/agent/onboarding/{run_id}/complete",
    response_model=dict,
)
def complete_onboarding(
    run_id: int,
    payload: AgentOnboardingCompleteRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, object]:
    """Complete onboarding from simplified selections."""
    response = complete_agent_onboarding.execute(
        db,
        user_id=require_user_id(current_user),
        run_id=run_id,
        payload=payload,
    )
    return response.model_dump(mode="json")


@router.post("/agent/digests", response_model=AgentDigestResponse)
def generate_digest(
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    payload: AgentDigestRequest | None = Body(default=None),
) -> AgentDigestResponse:
    """Queue arbitrary-window digest generation for agent clients."""
    if payload is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return generate_agent_digest.execute(
        db,
        user_id=require_user_id(current_user),
        payload=payload,
    )


@router.post("/agent/cli/link/start", response_model=CliLinkStartResponse)
def start_cli_link(
    db: Annotated[Session, Depends(get_db_session)],
    payload: CliLinkStartRequest | None = Body(default=None),
) -> CliLinkStartResponse:
    """Create an unauthenticated QR approval session for the CLI."""
    started = start_cli_link_session(
        db,
        device_name=payload.device_name if payload else None,
    )
    return CliLinkStartResponse(
        session_id=started.session_id,
        status="pending",
        poll_token=started.poll_token,
        approve_url=started.approve_url,
        expires_at=started.expires_at,
        poll_interval_seconds=2,
    )


@router.post("/agent/cli/link/{session_id}/approve", response_model=CliLinkApproveResponse)
def approve_cli_link(
    session_id: Annotated[str, Path(min_length=8, max_length=64)],
    payload: CliLinkApproveRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CliLinkApproveResponse:
    """Approve one pending CLI link session from the authenticated app."""
    try:
        approved = approve_cli_link_session(
            db,
            session_id=session_id,
            approve_token=payload.approve_token,
            user=current_user,
            device_name=payload.device_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CliLinkApproveResponse(
        session_id=approved.session_id,
        status="approved",
        key_prefix=approved.key_prefix,
        expires_at=approved.expires_at,
    )


@router.get("/agent/cli/link/{session_id}", response_model=CliLinkPollResponse)
def poll_cli_link(
    session_id: Annotated[str, Path(min_length=8, max_length=64)],
    poll_token: Annotated[str, Query(min_length=8, max_length=255)],
    db: Annotated[Session, Depends(get_db_session)],
) -> CliLinkPollResponse:
    """Poll a pending CLI link session without requiring existing auth."""
    try:
        polled = poll_cli_link_session(
            db,
            session_id=session_id,
            poll_token=poll_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CliLinkPollResponse(
        session_id=polled.session_id,
        status=polled.status,
        expires_at=polled.expires_at,
        api_key=polled.api_key,
        key_prefix=polled.key_prefix,
    )


@router.get("/agent/library/manifest", response_model=AgentLibraryManifestResponse)
def get_agent_library_manifest(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    include_source: Annotated[bool, Query()] = True,
) -> AgentLibraryManifestResponse:
    """Return manifest metadata for exportable per-user markdown files."""
    documents = collect_personal_markdown_documents_for_user(
        db,
        user_id=require_user_id(current_user),
        include_source=include_source,
    )
    return AgentLibraryManifestResponse(
        generated_at=datetime.now(UTC),
        include_source=include_source,
        documents=[
            AgentLibraryDocumentResponse(
                relative_path=document.relative_path.as_posix(),
                content_id=document.content_id,
                variant=document.variant,
                updated_at=document.updated_at,
                size_bytes=document.size_bytes,
                checksum_sha256=document.checksum_sha256,
            )
            for document in documents
        ],
    )


@router.get("/agent/library/file", response_model=AgentLibraryFileResponse)
def get_agent_library_file(
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentLibraryFileResponse:
    """Return one rendered markdown document by relative manifest path."""
    documents = collect_personal_markdown_documents_for_user(
        db,
        user_id=require_user_id(current_user),
        include_source=True,
    )
    document = next((item for item in documents if item.relative_path.as_posix() == path), None)
    if document is None:
        raise HTTPException(status_code=404, detail="Library document not found")
    return AgentLibraryFileResponse(
        relative_path=document.relative_path.as_posix(),
        content_id=document.content_id,
        variant=document.variant,
        updated_at=document.updated_at,
        checksum_sha256=document.checksum_sha256,
        text=document.text,
    )
