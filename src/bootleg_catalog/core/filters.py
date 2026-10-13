"""Filter DTO for recording search.

Lives in ``core`` (not ``database``) so that UI widgets can import it
without depending on the repository layer.
"""

from __future__ import annotations

from dataclasses import dataclass


# Columns used for free-text search
SEARCH_COLUMNS = (
    "title",
    "venue",
    "location",
    "country",
    "tour",
    "show",
    "source",
    "quality",
    "master",
    "notes",
    "description",
)


@dataclass(slots=True)
class RecordingFilter:
    """Filter criteria for searching recordings."""

    query: str = ""
    year: int | None = None
    source: str | None = None
    quality: str | None = None
    master: str | None = None
    missing: bool | None = None
    folder_id: int | None = None
