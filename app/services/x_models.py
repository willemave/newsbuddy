"""Small value objects for X API responses."""

from dataclasses import dataclass


@dataclass(frozen=True)
class XList:
    """Minimal X list payload used for sync."""

    id: str
    name: str
