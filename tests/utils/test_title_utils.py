"""Tests for title normalization helpers."""

from app.utils.title_utils import clean_title, derive_chat_session_title


def test_clean_title_drops_url_only_title() -> None:
    assert clean_title("https://t.co/1HuCDkyzQG https://t.co/B8fxN1yI8e") is None


def test_clean_title_keeps_textual_title_with_link_suffix() -> None:
    assert (
        clean_title("Pichai on search, portfolio of long term initiatives: https://t.co/t39iO9B7Ld")
        == "Pichai on search, portfolio of long term initiatives: https://t.co/t39iO9B7Ld"
    )


def test_derive_chat_session_title_uses_plain_message() -> None:
    assert derive_chat_session_title("What did I save about AI?") == "What did I save about AI?"


def test_derive_chat_session_title_rejects_bare_label_message() -> None:
    assert derive_chat_session_title("context:") is None


def test_derive_chat_session_title_skips_label_block_before_request() -> None:
    message = "context:\nScreen Type: knowledge_hub\nScreen Title: Knowledge\n\nWhat's new today?"
    assert derive_chat_session_title(message) == "What's new today?"


def test_derive_chat_session_title_truncates_on_word_boundary() -> None:
    message = (
        "Recommend a few feeds, newsletters, or podcasts I should add based on "
        "what I've been reading lately"
    )
    title = derive_chat_session_title(message)
    assert title is not None
    assert title.endswith("…")
    assert len(title) <= 81
    assert " based" in title or title.rstrip("…").endswith(("add", "on"))


def test_derive_chat_session_title_handles_blank_and_non_string_input() -> None:
    assert derive_chat_session_title("   \n  ") is None
    assert derive_chat_session_title(None) is None
