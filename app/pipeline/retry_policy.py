"""Neutral retry policy shared by task results, processors, and queue persistence."""


def retry_will_be_scheduled(
    *,
    success: bool,
    retryable: bool,
    retry_count: int,
    max_retries: int,
) -> bool:
    """Return whether one more queue attempt is allowed."""

    return not success and retryable and retry_count < max(int(max_retries), 0)
