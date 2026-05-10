"""Tests for dig-deeper prompt construction."""

from __future__ import annotations

import json

from app.models.contracts import ContentStatus, ContentType
from app.models.db import ChatMessage, Content, ContentDiscussion, ProcessingTask
from app.services.dig_deeper import (
    build_dig_deeper_prompt,
    create_dig_deeper_message,
    enqueue_dig_deeper_task,
)
from app.services.queue import TaskType


def _create_news_content(db_session) -> Content:
    content = Content(
        content_type=ContentType.NEWS.value,
        url="https://example.com/story",
        title="Example Story",
        source="example.com",
        platform="hackernews",
        status=ContentStatus.NEW.value,
        content_metadata={
            "platform": "hackernews",
            "discussion_url": "https://news.ycombinator.com/item?id=123",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    return content


def test_build_dig_deeper_prompt_includes_discussion_comment_context(db_session) -> None:
    content = _create_news_content(db_session)
    discussion = ContentDiscussion(
        content_id=content.id,
        platform="hackernews",
        status="completed",
        discussion_data={
            "mode": "comments",
            "compact_comments": [
                "Thread argues the rollout is too aggressive for reliability.",
                "Several users support phased deployment with feature flags.",
            ],
            "discussion_groups": [],
            "links": [],
            "stats": {"fetched_count": 2},
        },
    )
    db_session.add(discussion)
    db_session.commit()

    prompt = build_dig_deeper_prompt(db_session, content)

    assert "Discussion context:" in prompt
    assert "Comment highlights:" in prompt
    assert "too aggressive for reliability" in prompt
    assert "phased deployment with feature flags" in prompt
    assert "pull out key ideas from the discussion context" in prompt


def test_build_dig_deeper_prompt_without_discussion_context(db_session) -> None:
    content = _create_news_content(db_session)

    prompt = build_dig_deeper_prompt(db_session, content)

    assert "Dig deeper into the key points of" in prompt
    assert "Example Story" in prompt
    assert "Discussion context:" not in prompt


def test_build_dig_deeper_prompt_includes_discussion_group_topics(db_session) -> None:
    content = _create_news_content(db_session)
    discussion = ContentDiscussion(
        content_id=content.id,
        platform="techmeme",
        status="completed",
        discussion_data={
            "mode": "discussion_list",
            "discussion_groups": [
                {
                    "label": "Main stories",
                    "items": [
                        {"title": "OpenAI announces new roadmap", "url": "https://example.com/a"},
                        {"title": "Analysts debate timing", "url": "https://example.com/b"},
                    ],
                }
            ],
            "links": [],
            "stats": {"fetched_count": 2},
        },
    )
    db_session.add(discussion)
    db_session.commit()

    prompt = build_dig_deeper_prompt(db_session, content)

    assert "Discussion context:" in prompt
    assert "Discussion thread topics:" in prompt
    assert "Main stories: OpenAI announces new roadmap, Analysts debate timing" in prompt


def test_create_dig_deeper_message_uses_share_initial_message(db_session) -> None:
    content = _create_news_content(db_session)

    session_id, message_id, prompt = create_dig_deeper_message(
        db_session,
        content,
        user_id=1,
        initial_message="What should I ask the author?",
    )

    assert prompt == "What should I ask the author?"
    message = db_session.query(ChatMessage).filter(ChatMessage.id == message_id).first()
    assert message is not None
    assert message.session_id == session_id
    assert message.status == "processing"
    assert "What should I ask the author?" in json.dumps(json.loads(message.message_list))


def test_enqueue_dig_deeper_task_stores_initial_message(db_session) -> None:
    content = _create_news_content(db_session)
    assert content.id is not None

    task_id = enqueue_dig_deeper_task(
        db_session,
        content_id=content.id,
        user_id=1,
        initial_message="Help me brief this.",
    )

    task = db_session.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
    assert task is not None
    assert task.task_type == TaskType.DIG_DEEPER.value
    assert task.payload == {"user_id": 1, "initial_message": "Help me brief this."}
