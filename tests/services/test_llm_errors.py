import httpx
from openai import APIStatusError
from pydantic_ai.exceptions import ModelHTTPError

from app.services.llm_errors import is_llm_error_retryable, is_llm_unavailable_error


def test_timeout_error_is_llm_unavailable() -> None:
    assert is_llm_unavailable_error(TimeoutError("model timed out")) is True


def test_connection_error_is_llm_unavailable() -> None:
    assert is_llm_unavailable_error(httpx.ConnectError("provider unreachable")) is True


def test_provider_server_error_is_llm_unavailable() -> None:
    response = httpx.Response(503, request=httpx.Request("POST", "https://api.example.test"))
    error = APIStatusError("provider overloaded", response=response, body=None)

    assert is_llm_unavailable_error(error) is True


def test_model_server_error_is_llm_unavailable() -> None:
    error = ModelHTTPError(status_code=502, model_name="test-model", body=None)

    assert is_llm_unavailable_error(error) is True


def test_rate_limit_is_not_llm_unavailable() -> None:
    error = ModelHTTPError(status_code=429, model_name="test-model", body=None)

    assert is_llm_unavailable_error(error) is False


def test_non_availability_error_is_not_llm_unavailable() -> None:
    assert is_llm_unavailable_error(ValueError("bad prompt")) is False


def test_typed_client_error_is_not_retryable() -> None:
    error = ModelHTTPError(status_code=404, model_name="test-model", body=None)

    assert is_llm_error_retryable(error) is False


def test_typed_transient_cause_is_retryable_through_wrapper() -> None:
    provider_error = ModelHTTPError(status_code=503, model_name="test-model", body=None)
    try:
        raise ValueError("provider output validation failed") from provider_error
    except ValueError as error:
        assert is_llm_error_retryable(error) is True


def test_error_message_status_text_is_not_parsed() -> None:
    assert is_llm_error_retryable(RuntimeError("status_code: 404")) is True
