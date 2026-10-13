"""Recent sessions — persisted list of recently opened export sessions.

Pure module: no Qt, no catalog layer, no repository, no SQLite.

Persists a capped, deduplicated list to
``app_data_dir() / "recent_sessions.json"``.  Entries whose session file
no longer exists are kept — the UI can show them greyed out; pruning
missing paths is a UI concern, not a storage concern.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bootleg_catalog.utils.logging import get_logger
from bootleg_catalog.utils.paths import app_data_dir

logger = get_logger()

RECENT_SESSIONS_FILE = "recent_sessions.json"
FORMAT_VERSION = 1


class RecentSessionsError(Exception):
    """Raised when the recents file cannot be read or written."""


@dataclass(frozen=True)
class RecentEntry:
    display_name: str
    path: str            # absolute path to a .bootleg file
    last_opened: str     # ISO-8601 UTC


class RecentSessions:
    """A capped, deduplicated list of recently opened sessions.

    Persists to ``app_data_dir() / "recent_sessions.json"``.

    Read rules:

    - Missing file -> empty list; reading never creates the file.
    - Corrupt or structurally invalid JSON -> empty list, the file is
      kept on disk for manual recovery (and logged).
    - Each entry must be an object with string ``display_name``,
      ``path`` and ``last_opened`` fields whose path is absolute —
      anything else is skipped while valid entries are retained.
      Unknown fields are dropped: the schema is fixed on write.
    """

    MAX_ENTRIES = 10

    def __init__(self, path: Path | None = None) -> None:
        """path overrides the default file location (for tests)."""
        self._path = Path(path) if path is not None else None

    def list(self) -> list[RecentEntry]:
        """Return entries sorted by last_opened descending (newest first)."""
        return sorted(self._load(), key=lambda entry: entry.last_opened, reverse=True)

    def add(self, display_name: str, session_path: str | Path) -> None:
        """Record that this session was opened or saved.

        - Normalises session_path to an absolute path.
        - If an entry with the same path already exists, replaces its
          display_name and updates its last_opened timestamp — no duplicate.
        - Trims to MAX_ENTRIES (oldest evicted first).
        - Persists immediately.
        """
        absolute_path = os.path.abspath(str(session_path))
        entries = [e for e in self._load() if e.path != absolute_path]
        entries.append(
            RecentEntry(
                display_name=display_name,
                path=absolute_path,
                last_opened=datetime.now(timezone.utc).isoformat(
                    timespec="microseconds"
                ),
            )
        )
        entries.sort(key=lambda entry: entry.last_opened, reverse=True)
        entries = entries[: self.MAX_ENTRIES]
        self._save(entries)

    def remove(self, session_path: str | Path) -> None:
        """Remove the entry for this path. No-op if not present."""
        absolute_path = os.path.abspath(str(session_path))
        entries = self._load()
        remaining = [e for e in entries if e.path != absolute_path]
        if len(remaining) == len(entries):
            return
        self._save(remaining)

    def clear(self) -> None:
        """Remove all entries."""
        self._save([])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _file(self) -> Path:
        if self._path is not None:
            return self._path
        # app_data_dir() is called lazily here (never at import) so
        # tests can patch bootleg_catalog.core.recent_sessions.app_data_dir.
        return app_data_dir() / RECENT_SESSIONS_FILE

    def _load(self) -> list[RecentEntry]:
        path = self._file()
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise RecentSessionsError(f"Could not read recent sessions:\n{path}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Keeping unreadable recents file: %s", path)
            return []
        if not isinstance(data, dict):
            logger.warning("Keeping recents file with unexpected top-level value: %s", path)
            return []
        records = data.get("entries")
        if not isinstance(records, list):
            logger.warning("Keeping recents file without an entries list: %s", path)
            return []

        entries: list[RecentEntry] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            display_name = record.get("display_name")
            session_path = record.get("path")
            last_opened = record.get("last_opened")
            if not (
                isinstance(display_name, str)
                and isinstance(session_path, str)
                and isinstance(last_opened, str)
            ):
                continue
            if not os.path.isabs(session_path):
                # We always write absolute paths; skip anything else.
                continue
            entries.append(RecentEntry(display_name, session_path, last_opened))
        return entries

    def _save(self, entries: list[RecentEntry]) -> None:
        path = self._file()
        payload = {
            "format_version": FORMAT_VERSION,
            "entries": [
                {
                    "display_name": entry.display_name,
                    "path": entry.path,
                    "last_opened": entry.last_opened,
                }
                for entry in entries
            ],
        }
        temp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            file_descriptor, temp_name = tempfile.mkstemp(
                prefix=".recent-", suffix=".tmp", dir=str(path.parent)
            )
            os.close(file_descriptor)
            temp_path = Path(temp_name)
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(path)
        except OSError as exc:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise RecentSessionsError(f"Could not save recent sessions:\n{path}") from exc
