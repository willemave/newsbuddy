"""API models for authenticated user feedback."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.contracts import OperationStatus


class SubmitFeedbackRequest(BaseModel):
    """Feedback submitted from an authenticated client."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=4000)
    source: str = Field(default="ios_settings", min_length=1, max_length=64)
    app_version: str | None = Field(default=None, max_length=64)
    build_number: str | None = Field(default=None, max_length=64)
    platform: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=128)
    device_model: str | None = Field(default=None, max_length=128)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        """Trim feedback and reject whitespace-only submissions."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Feedback message is required")
        return normalized

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Feedback source is required")
        return normalized

    @field_validator(
        "app_version",
        "build_number",
        "platform",
        "os_version",
        "device_model",
    )
    @classmethod
    def normalize_optional_strings(cls, value: str | None) -> str | None:
        """Trim optional client metadata and store empty values as null."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SubmitFeedbackResponse(BaseModel):
    """Response after feedback is stored."""

    status: OperationStatus
    feedback_id: int
