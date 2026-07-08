import httpx
from openai import APIStatusError
from pydantic_ai.exceptions import ModelHTTPError

from app.services.llm_errors import is_llm_unavailable_error


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
