"""Typed failures shared by audio episode orchestration layers."""


class AudioEpisodeInputError(ValueError):
    """Raised when persisted episode input cannot produce a valid episode."""
