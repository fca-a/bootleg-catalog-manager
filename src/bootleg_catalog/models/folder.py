from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Folder:
    id: int
    path: str
    added_at: str
    last_scanned_at: str | None = None
