"""Tests for structured logging payload/filter/console formatting."""

import logging

from app.core.logging import (
    _build_structured_json_payload,
    _ConsoleStructuredFormatter,
    _StructuredLogFilter,
)


class TestStructuredLogging:
    """Tests for structured logging payloads and filters."""

    def test_structured_payload_merges_extra_fields(self):
        """Test structured payload merges extra fields into context_data."""
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test_file.py",
            lineno=10,
            msg="Structured log",
            args=(),
            exc_info=None,
        )
        record.user_id = 555
        record.context_data = {"content_id": 42}

        payload = _build_structured_json_payload(record)

        assert payload["context_data"]["content_id"] == 42
        assert payload["user_id"] == 555

    def test_structured_payload_promotes_provider_and_model(self):
        """Provider/model should be first-class fields for vendor and LLM logs."""
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test_file.py",
            lineno=10,
            msg="Vendor usage",
            args=(),
            exc_info=None,
        )
        record.provider = "openai"
        record.model = "gpt-5.4-mini"
        record.context_data = {"feature": "summarization"}

        payload = _build_structured_json_payload(record)

        assert payload["provider"] == "openai"
        assert payload["model"] == "gpt-5.4-mini"
        assert payload["context_data"] == {"feature": "summarization"}

    def test_structured_log_filter(self):
        """Test structured log filter only allows records with structured data."""
        filter_instance = _StructuredLogFilter()

        record_without_extra = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test_file.py",
            lineno=10,
            msg="Plain log",
            args=(),
            exc_info=None,
        )
        assert filter_instance.filter(record_without_extra) is False

        record_with_extra = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test_file.py",
            lineno=10,
            msg="Structured log",
            args=(),
            exc_info=None,
        )
        record_with_extra.context_data = {"content_id": 1}
        assert filter_instance.filter(record_with_extra) is True


class TestConsoleStructuredFormatter:
    """Tests for console formatter structured metadata output."""

    def test_plain_record_has_no_structured_suffix(self):
        """Plain logs should not include structured suffixes."""
        formatter = _ConsoleStructuredFormatter("%(message)s")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)

        assert result == "hello"

    def test_structured_record_includes_operation_and_context(self):
        """Structured logs include operation/item/context in console output."""
        formatter = _ConsoleStructuredFormatter("%(message)s")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="voice trace",
            args=(),
            exc_info=None,
        )
        record.component = "voice_ws"
        record.operation = "audio_commit_received"
        record.item_id = 123
        record.context_data = {"event_type": "audio.commit"}

        result = formatter.format(record)

        assert "voice trace" in result
        assert "component=voice_ws" in result
        assert "operation=audio_commit_received" in result
        assert "item_id=123" in result
        assert "audio.commit" in result

    def test_structured_record_includes_provider_and_model(self):
        """Console logs surface provider/model without burying them in context."""
        formatter = _ConsoleStructuredFormatter("%(message)s")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="vendor trace",
            args=(),
            exc_info=None,
        )
        record.provider = "google"
        record.model = "gemini-3.1-flash-lite-preview"

        result = formatter.format(record)

        assert "provider=google" in result
        assert "model=gemini-3.1-flash-lite-preview" in result

    def test_structured_record_redacts_sensitive_context(self):
        """Sensitive fields are redacted in console structured suffix."""
        formatter = _ConsoleStructuredFormatter("%(message)s")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="auth trace",
            args=(),
            exc_info=None,
        )
        record.operation = "auth_debug"
        record.context_data = {"password": "secret123", "username": "demo"}

        result = formatter.format(record)

        assert "<redacted>" in result
        assert "secret123" not in result
