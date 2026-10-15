"""Application-wide settings persistence.

Stores global settings as a JSON file in the platform-appropriate
application data directory.

Empty string values for paths are treated as "not configured" — the
existing fallback behaviour (env var → system PATH) applies.

Suggestion lists for Type, Source, Quality, and Trade Status are
user-configurable and stored in settings.json.  They provide
autocomplete values for the UI and are NOT closed enums — the archival
fields always store free-form strings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from bootleg_catalog.utils.paths import app_data_dir

SETTINGS_FILE = "settings.json"

# Settings key for the last-used export layout name (prepared for the
# layout chooser wiring; no AppSettings field yet).
LAST_LAYOUT_SETTING_KEY = "last_layout_name"

# ---------------------------------------------------------------------------
# Canonical suggestion tuples
#
# Relocated from the removed models/recording.py; settings is their
# surviving home and the default suggestion lists below derive from them.
# ---------------------------------------------------------------------------

# Master stays a free-text field; the combo offers exactly one
# suggestion (the user can type anything else).
MASTER_VALUES = ("Unknown",)
QUALITY_VALUES = (
    "Unknown",
    "Excellent",
    "Very Good",
    "Good",
    "Fair",
    "Poor",
    "A+", "A", "A-",
    "B+", "B", "B-",
    "C+", "C", "C-",
)
SOURCE_SUGGESTIONS = (
    "Housecam",
    "Audience",
    "Pro-shot",
    "Soundboard",
    "FM Broadcast",
    "IEM",
    "Matrix",
    "TV Broadcast",
    "Radio Broadcast",
    "Other",
)


# ---------------------------------------------------------------------------
# Default suggestion lists
# ---------------------------------------------------------------------------

DEFAULT_TYPE_SUGGESTIONS: list[str] = [
    "Audience",
    "Broadcast",
    "Fancam",
    "Housecam",
    "Multicam",
    "Rehearsal",
    "Soundboard",
    "Pro-shot",
]

DEFAULT_SOURCE_SUGGESTIONS: list[str] = [
    "Audience",
    "Blu-ray",
    "CD",
    "DAT",
    "DVD",
    "FM Broadcast",
    "TV Broadcast",
    "VHS",
    "Vinyl",
    "Web",
]

DEFAULT_QUALITY_SUGGESTIONS: list[str] = list(QUALITY_VALUES)

# Trade Status — free text; NFT = Not For Trade, NFTF = Not For Trade
# Forever.  The list is a starting point, not an enum.
DEFAULT_TRADE_STATUS_SUGGESTIONS: list[str] = ["NFT", "NFTF"]

# Not part of AppSettings: master is a fixed one-item suggestion list;
# the dialogs read it via MASTER_VALUES.
DEFAULT_MASTER_SUGGESTIONS: list[str] = list(MASTER_VALUES)


# ---------------------------------------------------------------------------
# AppSettings
# ---------------------------------------------------------------------------

def _filter_string_list(value: object) -> list[str] | None:
    """Return only the string items from *value*.

    If *value* is not a list, returns ``None`` ( signalling "use default").
    Non-string entries inside a list are silently dropped.
    """
    if not isinstance(value, list):
        return None
    return [v for v in value if isinstance(v, str)]


@dataclass
class AppSettings:
    """Global application settings."""

    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    type_suggestions: list[str] = field(default_factory=lambda: list(DEFAULT_TYPE_SUGGESTIONS))
    source_suggestions: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCE_SUGGESTIONS))
    quality_suggestions: list[str] = field(default_factory=lambda: list(DEFAULT_QUALITY_SUGGESTIONS))
    trade_status_suggestions: list[str] = field(default_factory=lambda: list(DEFAULT_TRADE_STATUS_SUGGESTIONS))

    # Explicit __eq__ so mutable default_factory lists are compared by value.
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AppSettings):
            return NotImplemented
        return (
            self.ffmpeg_path == other.ffmpeg_path
            and self.ffprobe_path == other.ffprobe_path
            and self.type_suggestions == other.type_suggestions
            and self.source_suggestions == other.source_suggestions
            and self.quality_suggestions == other.quality_suggestions
            and self.trade_status_suggestions == other.trade_status_suggestions
        )

    def to_dict(self) -> dict:
        return {
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "suggestion_lists": {
                "type": list(self.type_suggestions),
                "source": list(self.source_suggestions),
                "quality": list(self.quality_suggestions),
                "trade_status": list(self.trade_status_suggestions),
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AppSettings:
        suggestion_lists = data.get("suggestion_lists")
        if not isinstance(suggestion_lists, dict):
            suggestion_lists = {}

        raw_type = suggestion_lists.get("type")
        raw_source = suggestion_lists.get("source")
        raw_quality = suggestion_lists.get("quality")
        raw_trade = suggestion_lists.get("trade_status")

        type_suggestions = _filter_string_list(raw_type)
        source_suggestions = _filter_string_list(raw_source)
        quality_suggestions = _filter_string_list(raw_quality)
        trade_status_suggestions = _filter_string_list(raw_trade)

        return cls(
            ffmpeg_path=data.get("ffmpeg_path", ""),
            ffprobe_path=data.get("ffprobe_path", ""),
            type_suggestions=type_suggestions if type_suggestions is not None else list(DEFAULT_TYPE_SUGGESTIONS),
            source_suggestions=source_suggestions if source_suggestions is not None else list(DEFAULT_SOURCE_SUGGESTIONS),
            quality_suggestions=quality_suggestions if quality_suggestions is not None else list(DEFAULT_QUALITY_SUGGESTIONS),
            trade_status_suggestions=trade_status_suggestions if trade_status_suggestions is not None else list(DEFAULT_TRADE_STATUS_SUGGESTIONS),
        )


def _settings_path() -> Path:
    return app_data_dir() / SETTINGS_FILE


def load_settings() -> AppSettings:
    """Load settings from disk.  Returns defaults when the file is missing or corrupt."""
    path = _settings_path()
    if not path.is_file():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return AppSettings()
        return AppSettings.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    """Persist settings to disk."""
    path = _settings_path()
    path.write_text(
        json.dumps(settings.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
