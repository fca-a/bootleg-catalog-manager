"""Show grouper for export sessions.

Groups a collection of :class:`ExportRecording` by their ``show``
field, producing :class:`ShowGroup` instances for downstream
consumption (Preview, PDF, XLSX).

Rules:

- ``None``, empty string, or whitespace-only → ``"Ungrouped"``
- Comparison is case-insensitive
- Whitespace is normalised (leading/trailing stripped, internal collapsed)
- The first normalised form encountered determines the group name
- Groups are sorted alphabetically (case-insensitive); ``Ungrouped``
  always appears last
- Recordings within a group preserve insertion order
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from bootleg_catalog.models.export_recording import ExportRecording


UNGROUPED = "Ungrouped"


@dataclass
class ShowGroup:
    """A group of recordings sharing the same ``[Show]`` value."""
    show_name: str
    recordings: list[ExportRecording]


def _normalise_show(value: str | None) -> str:
    """Normalise a show value for grouping.

    - ``None`` → ``"Ungrouped"``
    - Empty/whitespace-only → ``"Ungrouped"``
    - Otherwise: strip + collapse internal whitespace
    """
    if value is None:
        return UNGROUPED
    normalised = " ".join(value.split())
    if not normalised:
        return UNGROUPED
    return normalised


def group_by_show(recordings: list[ExportRecording]) -> list[ShowGroup]:
    """Group recordings by their ``show`` field.

    Parameters
    ----------
    recordings:
        Recordings to group.  The list is not modified.

    Returns
    -------
    list[ShowGroup]
        Groups sorted alphabetically by ``show_name`` (case-insensitive),
        with ``"Ungrouped"`` always last.  Recordings within each group
        preserve insertion order.
    """
    # Phase 1: bucket by normalised show key (case-insensitive)
    # The key is lowercased for comparison; the display name is the
    # first normalised form encountered (deterministic by insertion order).
    buckets: dict[str, list[ExportRecording]] = defaultdict(list)
    key_to_name: dict[str, str] = {}  # lowercased key → display name

    for rec in recordings:
        normalised = _normalise_show(rec.show)
        key = normalised.lower()
        buckets[key].append(rec)
        if key not in key_to_name:
            key_to_name[key] = normalised

    # Phase 2: build ShowGroup list, sorted
    groups: list[ShowGroup] = []
    for key, recs in buckets.items():
        groups.append(ShowGroup(show_name=key_to_name[key], recordings=list(recs)))

    # Sort: case-insensitive alphabetical, Ungrouped last
    groups.sort(key=lambda g: (g.show_name.lower() == UNGROUPED.lower(), g.show_name.lower()))

    return groups
