"""Shared formatting helpers for admin web views."""


def format_user_label(user_id: int | None, email: str | None, full_name: str | None) -> str:
    """Return a stable admin display label for an optional user."""
    if full_name and email:
        return f"{full_name} ({email})"
    if email:
        return email
    if full_name:
        return full_name
    if user_id is not None:
        return f"User {user_id}"
    return "Unattributed"


def format_user_label_with_id(user_id: int | None, email: str | None, full_name: str | None) -> str:
    """Return a user display label with the database id when available."""
    label = format_user_label(user_id, email, full_name)
    if user_id is None:
        return label
    return f"{label} (#{user_id})"
