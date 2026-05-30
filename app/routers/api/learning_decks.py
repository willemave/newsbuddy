"""Learning Deck API and hosted artifact routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db_session, get_readonly_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.learning_decks import (
    LearningDeckCreateRequest,
    LearningDeckListResponse,
    LearningDeckResponse,
    LearningDeckShareResponse,
    LearningDeckUrlResponse,
)
from app.models.db import User
from app.services.learning_decks import (
    LearningDeckError,
    LearningDeckHostedObject,
    build_private_learning_deck_token,
    create_or_rerun_learning_deck,
    delete_learning_deck,
    disable_learning_deck_share,
    enable_learning_deck_share,
    get_deck_by_private_token,
    get_deck_by_valid_share_token,
    get_learning_deck,
    list_learning_decks,
    present_learning_deck,
    read_learning_deck_asset_object,
    read_learning_deck_source_notes_object,
    read_learning_deck_viewer_object,
)

router = APIRouter(prefix="/learning", tags=["learning"])
public_router = APIRouter(tags=["learning"])


@router.get(
    "/decks",
    response_model=LearningDeckListResponse,
    summary="List current user's Learning Decks",
)
def list_decks(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningDeckListResponse:
    """Return current Learning Decks for the authenticated user."""
    user_id = require_user_id(current_user)
    return LearningDeckListResponse(
        decks=[present_learning_deck(db, deck) for deck in list_learning_decks(db, user_id=user_id)]
    )


@router.post(
    "/decks",
    response_model=LearningDeckResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create or rerun a Learning Deck",
)
def create_deck(
    payload: LearningDeckCreateRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningDeckResponse:
    """Create or rerun a Learning Deck from one supported source."""
    try:
        deck = create_or_rerun_learning_deck(
            db,
            current_user=current_user,
            content_id=payload.content_id,
            news_item_id=payload.news_item_id,
            url=payload.url,
            interests_prompt=payload.interests_prompt,
        )
    except LearningDeckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return present_learning_deck(db, deck)


@router.get(
    "/decks/{deck_id}",
    response_model=LearningDeckResponse,
    summary="Get one Learning Deck",
)
def get_deck(
    deck_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningDeckResponse:
    """Return one Learning Deck for the authenticated user."""
    deck = get_learning_deck(db, user_id=require_user_id(current_user), deck_id=deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Learning Deck not found")
    return present_learning_deck(db, deck)


@router.delete(
    "/decks/{deck_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a Learning Deck",
)
def delete_deck(
    deck_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Soft-delete a Learning Deck and remove known artifact objects."""
    try:
        delete_learning_deck(db, user_id=require_user_id(current_user), deck_id=deck_id)
    except LearningDeckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/decks/{deck_id}/viewer-url",
    response_model=LearningDeckUrlResponse,
    summary="Create a short-lived private viewer URL",
)
def create_viewer_url(
    deck_id: Annotated[int, Path(..., gt=0)],
    request: Request,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningDeckUrlResponse:
    """Return a short-lived URL for opening the raw Reveal.js deck."""
    user_id = require_user_id(current_user)
    deck = get_learning_deck(db, user_id=user_id, deck_id=deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Learning Deck not found")
    if not deck.latest_successful_run_id:
        raise HTTPException(status_code=409, detail="Learning Deck is not ready")
    token, expires_at = build_private_learning_deck_token(deck=deck, user_id=user_id)
    return LearningDeckUrlResponse(
        url=str(request.url_for("serve_private_learning_deck", token=token)),
        expires_at=expires_at,
    )


@router.post(
    "/decks/{deck_id}/source-notes-url",
    response_model=LearningDeckUrlResponse,
    summary="Create a short-lived private source-notes URL",
)
def create_source_notes_url(
    deck_id: Annotated[int, Path(..., gt=0)],
    request: Request,
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningDeckUrlResponse:
    """Return a short-lived URL for rendered source notes."""
    user_id = require_user_id(current_user)
    deck = get_learning_deck(db, user_id=user_id, deck_id=deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Learning Deck not found")
    if not deck.latest_successful_run_id:
        raise HTTPException(status_code=409, detail="Learning Deck is not ready")
    token, expires_at = build_private_learning_deck_token(deck=deck, user_id=user_id)
    return LearningDeckUrlResponse(
        url=str(request.url_for("serve_private_learning_deck_source_notes", token=token)),
        expires_at=expires_at,
    )


@router.post(
    "/decks/{deck_id}/share",
    response_model=LearningDeckShareResponse,
    summary="Enable public sharing for a Learning Deck",
)
def enable_share(
    deck_id: Annotated[int, Path(..., gt=0)],
    request: Request,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningDeckShareResponse:
    """Enable sharing and return the stable public URL."""
    try:
        token = enable_learning_deck_share(
            db,
            user_id=require_user_id(current_user),
            deck_id=deck_id,
        )
    except LearningDeckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return LearningDeckShareResponse(
        share_enabled=True,
        share_url=str(request.url_for("serve_shared_learning_deck", token=token)),
    )


@router.delete(
    "/decks/{deck_id}/share",
    response_model=LearningDeckShareResponse,
    summary="Disable public sharing for a Learning Deck",
)
def disable_share(
    deck_id: Annotated[int, Path(..., gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningDeckShareResponse:
    """Disable public sharing for a Learning Deck."""
    try:
        disable_learning_deck_share(
            db,
            user_id=require_user_id(current_user),
            deck_id=deck_id,
        )
    except LearningDeckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return LearningDeckShareResponse(share_enabled=False)


@public_router.get(
    "/learning/share/{token}/",
    name="serve_shared_learning_deck",
    include_in_schema=False,
)
def serve_shared_learning_deck(
    token: str,
    db: Annotated[Session, Depends(get_readonly_db_session)],
) -> Response:
    """Serve a public shared Learning Deck."""
    try:
        deck = get_deck_by_valid_share_token(db, token=token)
        return _hosted_object_response(read_learning_deck_viewer_object(deck))
    except LearningDeckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Learning Deck artifact not found") from exc


@public_router.get(
    "/learning/share/{token}/source-notes",
    name="serve_shared_learning_deck_source_notes",
    include_in_schema=False,
)
def serve_shared_learning_deck_source_notes(
    token: str,
    db: Annotated[Session, Depends(get_readonly_db_session)],
) -> Response:
    """Serve public rendered source notes for a shared Learning Deck."""
    try:
        deck = get_deck_by_valid_share_token(db, token=token)
        return _hosted_object_response(read_learning_deck_source_notes_object(deck))
    except LearningDeckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Learning Deck artifact not found") from exc


@public_router.get(
    "/learning/share/{token}/assets/{asset_path:path}",
    name="serve_shared_learning_deck_asset",
    include_in_schema=False,
)
def serve_shared_learning_deck_asset(
    token: str,
    asset_path: str,
    db: Annotated[Session, Depends(get_readonly_db_session)],
) -> Response:
    """Serve a public shared Learning Deck local asset."""
    try:
        deck = get_deck_by_valid_share_token(db, token=token)
        return _hosted_object_response(read_learning_deck_asset_object(deck, asset_path=asset_path))
    except LearningDeckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Learning Deck artifact not found") from exc


@public_router.get(
    "/learning/signed/{token}/",
    name="serve_private_learning_deck",
    include_in_schema=False,
)
def serve_private_learning_deck(
    token: str,
    db: Annotated[Session, Depends(get_readonly_db_session)],
) -> Response:
    """Serve a short-lived private Learning Deck URL."""
    try:
        deck = get_deck_by_private_token(db, token=token)
        return _hosted_object_response(read_learning_deck_viewer_object(deck))
    except LearningDeckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Learning Deck artifact not found") from exc


@public_router.get(
    "/learning/signed/{token}/source-notes",
    name="serve_private_learning_deck_source_notes",
    include_in_schema=False,
)
def serve_private_learning_deck_source_notes(
    token: str,
    db: Annotated[Session, Depends(get_readonly_db_session)],
) -> Response:
    """Serve rendered source notes for a short-lived private URL."""
    try:
        deck = get_deck_by_private_token(db, token=token)
        return _hosted_object_response(read_learning_deck_source_notes_object(deck))
    except LearningDeckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Learning Deck artifact not found") from exc


@public_router.get(
    "/learning/signed/{token}/assets/{asset_path:path}",
    name="serve_private_learning_deck_asset",
    include_in_schema=False,
)
def serve_private_learning_deck_asset(
    token: str,
    asset_path: str,
    db: Annotated[Session, Depends(get_readonly_db_session)],
) -> Response:
    """Serve a local asset for a short-lived private Learning Deck URL."""
    try:
        deck = get_deck_by_private_token(db, token=token)
        return _hosted_object_response(read_learning_deck_asset_object(deck, asset_path=asset_path))
    except LearningDeckError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Learning Deck artifact not found") from exc


def _hosted_object_response(hosted_object: LearningDeckHostedObject) -> Response:
    return Response(
        content=hosted_object.data,
        media_type=hosted_object.media_type,
        headers={"Cache-Control": "private, no-store"},
    )
