"""Tests for logging redaction and filename sanitization helpers."""

from app.core.logging import _sanitize_filename
from app.core.redaction import redact_value


class TestRedactValue:
    """Tests for sensitive data redaction."""

    def test_redacts_authorization_header(self):
        """Test that authorization headers are redacted."""
        headers = {"authorization": "Bearer secret-token-123"}
        result = redact_value(headers)
        assert result["authorization"] == "<redacted>"

    def test_redacts_cookie_header(self):
        """Test that cookie headers are redacted."""
        headers = {"cookie": "session=abc123; user=test"}
        result = redact_value(headers)
        assert result["cookie"] == "<redacted>"

    def test_redacts_api_key_variations(self):
        """Test that various API key field names are redacted."""
        data = {
            "api-key": "key123",
            "apikey": "key456",
            "x-api-key": "key789",
            "APIKEY": "key000",
        }
        result = redact_value(data)
        assert result["api-key"] == "<redacted>"
        assert result["apikey"] == "<redacted>"
        assert result["x-api-key"] == "<redacted>"
        assert result["APIKEY"] == "<redacted>"

    def test_redacts_token_fields(self):
        """Test that token fields are redacted."""
        data = {
            "token": "tok123",
            "access_token": "access123",
            "refresh_token": "refresh123",
        }
        result = redact_value(data)
        assert result["token"] == "<redacted>"
        assert result["access_token"] == "<redacted>"
        assert result["refresh_token"] == "<redacted>"

    def test_redacts_password_and_secret(self):
        """Test that password and secret fields are redacted."""
        data = {"password": "secret123", "secret": "mysecret", "jwt_secret_key": "jwtsec"}
        result = redact_value(data)
        assert result["password"] == "<redacted>"
        assert result["secret"] == "<redacted>"
        assert result["jwt_secret_key"] == "<redacted>"

    def test_redacts_bearer_token_in_string(self):
        """Test that Bearer tokens in strings are redacted."""
        value = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = redact_value(value)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "Bearer <redacted>" in result

    def test_redacts_nested_dicts(self):
        """Test that nested dictionaries are redacted recursively."""
        data = {
            "outer": {"inner": {"password": "secret123", "safe_field": "visible"}},
            "normal_field": "also_visible",
        }
        result = redact_value(data)
        assert result["outer"]["inner"]["password"] == "<redacted>"
        assert result["outer"]["inner"]["safe_field"] == "visible"
        assert result["normal_field"] == "also_visible"

    def test_redacts_values_in_lists(self):
        """Test that values in lists are redacted recursively."""
        data = [{"password": "secret"}, {"username": "visible"}]
        result = redact_value(data)
        assert result[0]["password"] == "<redacted>"
        assert result[1]["username"] == "visible"

    def test_redacts_values_in_tuples(self):
        """Test that values in tuples are redacted recursively."""
        data = ({"token": "secret"}, {"data": "visible"})
        result = redact_value(data)
        assert result[0]["token"] == "<redacted>"
        assert result[1]["data"] == "visible"
        assert isinstance(result, tuple)

    def test_preserves_non_sensitive_data(self):
        """Test that non-sensitive data is preserved."""
        data = {"username": "testuser", "email": "test@example.com", "count": 42}
        result = redact_value(data)
        assert result == data

    def test_handles_none_values(self):
        """Test that None values pass through unchanged."""
        assert redact_value(None) is None

    def test_handles_numeric_values(self):
        """Test that numeric values pass through unchanged."""
        assert redact_value(42) == 42
        assert redact_value(3.14) == 3.14


class TestSanitizeFilename:
    """Tests for filename sanitization."""

    def test_sanitizes_special_characters(self):
        """Test that special characters are replaced with underscores."""
        assert _sanitize_filename("my/log:file") == "my_log_file"
        assert _sanitize_filename("test@component") == "test_component"

    def test_converts_to_lowercase(self):
        """Test that filenames are converted to lowercase."""
        assert _sanitize_filename("MyLogFile") == "mylogfile"

    def test_strips_leading_trailing_chars(self):
        """Test that leading/trailing special chars are stripped."""
        assert _sanitize_filename("_test_") == "test"
        assert _sanitize_filename("...test...") == "test"

    def test_returns_app_for_empty(self):
        """Test that empty strings return 'app'."""
        assert _sanitize_filename("") == "app"
        assert _sanitize_filename("   ") == "app"
