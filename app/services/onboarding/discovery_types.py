"""Typed outcomes shared by onboarding discovery runners."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OnboardingDiscoveryExecutionResult:
    """Terminal discovery outcome consumed by the queue handler."""

    success: bool
    error_message: str | None = None
    run_id: int | None = None
