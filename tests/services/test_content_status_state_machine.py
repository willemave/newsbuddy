"""Tests for the long-form content status state machine."""

from __future__ import annotations

import pytest

from app.models.contracts import ContentStatus, ContentType
from app.services.content_status_state_machine import (
    ContentStatusStateMachine,
    InvalidContentStatusTransition,
)


def test_long_form_summary_without_artwork_enters_awaiting_image() -> None:
    assert (
        ContentStatusStateMachine.status_after_summary(
            content_type=ContentType.ARTICLE,
            artwork_ready=False,
        )
        == ContentStatus.AWAITING_IMAGE
    )


def test_long_form_summary_with_artwork_completes() -> None:
    assert (
        ContentStatusStateMachine.status_after_summary(
            content_type=ContentType.PODCAST,
            artwork_ready=True,
        )
        == ContentStatus.COMPLETED
    )


def test_news_summary_completes_without_artwork_gate() -> None:
    assert (
        ContentStatusStateMachine.status_after_summary(
            content_type=ContentType.NEWS,
            artwork_ready=False,
        )
        == ContentStatus.COMPLETED
    )


def test_generated_artwork_completes_awaiting_image_content() -> None:
    assert (
        ContentStatusStateMachine.status_after_generated_artwork(
            content_type=ContentType.ARTICLE,
            current_status=ContentStatus.AWAITING_IMAGE,
        )
        == ContentStatus.COMPLETED
    )


def test_generated_artwork_rejects_invalid_long_form_transition() -> None:
    with pytest.raises(InvalidContentStatusTransition):
        ContentStatusStateMachine.status_after_generated_artwork(
            content_type=ContentType.ARTICLE,
            current_status=ContentStatus.PROCESSING,
        )
