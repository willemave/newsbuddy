from __future__ import annotations

from app.models.api import chat, content_actions
from app.models.contracts import ChatMessageDisplayType, ChatMessageRole, TweetLength


def test_cross_client_enums_live_in_contracts_module() -> None:
    """Cross-client enum definitions should have one canonical source."""
    assert ChatMessageRole.__module__ == "app.models.contracts"
    assert ChatMessageDisplayType.__module__ == "app.models.contracts"
    assert TweetLength.__module__ == "app.models.contracts"

    assert chat.ChatMessageRole is ChatMessageRole
    assert chat.ChatMessageDisplayType is ChatMessageDisplayType
    assert content_actions.TweetLength is TweetLength
