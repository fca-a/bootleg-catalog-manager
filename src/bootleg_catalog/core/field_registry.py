"""Typed field registry for export layouts.

Separates fields into three categories:

- **TechnicalField**: resolved automatically from ExportRecording data
  (populated by FFprobe).
- **ArchivalField**: resolved from user-entered ExportRecording data.
- **SuggestionArchivalField**: archival field with a configurable
  suggestion list (e.g. Type, Source).

Usage::

    from bootleg_catalog.core.field_registry import (
        REGISTRY,
        get_field_spec,
        FieldCategory,
    )

    spec = get_field_spec("Duration")
    assert spec.category == FieldCategory.TECHNICAL

    spec = get_field_spec("Show")
    assert spec.category == FieldCategory.ARCHIVAL

The registry does NOT depend on Qt, SQLite, or the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from bootleg_catalog.models.export_recording import ExportRecording


# ---------------------------------------------------------------------------
# Field categories
# ---------------------------------------------------------------------------

class FieldCategory(Enum):
    """Semantic category of a layout field."""
    TECHNICAL = "technical"
    ARCHIVAL = "archival"
    SUGGESTION = "suggestion"


# ---------------------------------------------------------------------------
# Field spec base
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldSpec:
    """Base specification for a registered field."""
    name: str
    display_name: str
    category: FieldCategory
    resolver: Callable[[ExportRecording], str | None]


@dataclass(frozen=True)
class TechnicalFieldSpec(FieldSpec):
    """A field resolved automatically from technical metadata."""
    category: FieldCategory = field(default=FieldCategory.TECHNICAL, init=False)


@dataclass(frozen=True)
class ArchivalFieldSpec(FieldSpec):
    """A field resolved from user-entered metadata."""
    category: FieldCategory = field(default=FieldCategory.ARCHIVAL, init=False)


@dataclass(frozen=True)
class SuggestionFieldSpec(FieldSpec):
    """An archival field backed by a configurable suggestion list."""
    category: FieldCategory = field(default=FieldCategory.SUGGESTION, init=False)
    suggestions_key: str = ""


# ---------------------------------------------------------------------------
# Formatting helpers (centralised)
# ---------------------------------------------------------------------------

def _fmt_size(rec: ExportRecording) -> str | None:
    """Format file size as human-readable string."""
    if rec.file_size is None:
        return None
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(rec.file_size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{rec.file_size} B"


def _fmt_duration(rec: ExportRecording) -> str | None:
    """Format duration as H:MM:SS or M:SS."""
    if rec.duration is None:
        return None
    total = max(0, int(round(rec.duration)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _fmt_resolution(rec: ExportRecording) -> str | None:
    """Format resolution as WxH."""
    if rec.video_width and rec.video_height:
        return f"{rec.video_width}\u00d7{rec.video_height}"
    return None


def _fmt_frame_rate(rec: ExportRecording) -> str | None:
    """Format frame rate with two decimal places."""
    if rec.frame_rate is None:
        return None
    return f"{rec.frame_rate:.2f} fps"


def _fmt_sample_rate(rec: ExportRecording) -> str | None:
    """Format audio sample rate."""
    if rec.audio_sample_rate is None:
        return None
    return f"{rec.audio_sample_rate} Hz"


def _fmt_bitrate(rec: ExportRecording) -> str | None:
    """Format audio bitrate."""
    if rec.audio_bitrate is None:
        return None
    if rec.audio_bitrate >= 1_000_000:
        return f"{rec.audio_bitrate / 1_000_000:.1f} Mbps"
    if rec.audio_bitrate >= 1_000:
        return f"{rec.audio_bitrate / 1_000:.0f} kbps"
    return f"{rec.audio_bitrate} bps"


# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------

# Technical fields — resolved from ExportRecording automatically.
TECHNICAL_FIELDS: dict[str, TechnicalFieldSpec] = {
    "Extension": TechnicalFieldSpec(
        name="Extension", display_name="Extension",
        resolver=lambda r: r.extension,
    ),
    "Size": TechnicalFieldSpec(
        name="Size", display_name="Size",
        resolver=_fmt_size,
    ),
    "Duration": TechnicalFieldSpec(
        name="Duration", display_name="Duration",
        resolver=_fmt_duration,
    ),
    "Container": TechnicalFieldSpec(
        name="Container", display_name="Container",
        resolver=lambda r: r.container_format,
    ),
    "Video Codec": TechnicalFieldSpec(
        name="Video Codec", display_name="Video Codec",
        resolver=lambda r: r.video_codec,
    ),
    "Resolution": TechnicalFieldSpec(
        name="Resolution", display_name="Resolution",
        resolver=_fmt_resolution,
    ),
    "Frame Rate": TechnicalFieldSpec(
        name="Frame Rate", display_name="Frame Rate",
        resolver=_fmt_frame_rate,
    ),
    "Audio Codec": TechnicalFieldSpec(
        name="Audio Codec", display_name="Audio Codec",
        resolver=lambda r: r.audio_codec,
    ),
    "Audio Channels": TechnicalFieldSpec(
        name="Audio Channels", display_name="Audio Channels",
        resolver=lambda r: str(r.audio_channels) if r.audio_channels is not None else None,
    ),
    "Sample Rate": TechnicalFieldSpec(
        name="Sample Rate", display_name="Sample Rate",
        resolver=_fmt_sample_rate,
    ),
    "Audio Bitrate": TechnicalFieldSpec(
        name="Audio Bitrate", display_name="Audio Bitrate",
        resolver=_fmt_bitrate,
    ),
}

# Archival fields — free-text, resolved from ExportRecording.
ARCHIVAL_FIELDS: dict[str, ArchivalFieldSpec] = {
    "Title": ArchivalFieldSpec(
        name="Title", display_name="Title",
        resolver=lambda r: r.title,
    ),
    "Year": ArchivalFieldSpec(
        name="Year", display_name="Year",
        resolver=lambda r: str(r.year) if r.year is not None else None,
    ),
    "Date": ArchivalFieldSpec(
        name="Date", display_name="Date",
        resolver=lambda r: r.date,
    ),
    "Venue": ArchivalFieldSpec(
        name="Venue", display_name="Venue",
        resolver=lambda r: r.venue,
    ),
    "Location": ArchivalFieldSpec(
        name="Location", display_name="Location",
        resolver=lambda r: r.location,
    ),
    "Country": ArchivalFieldSpec(
        name="Country", display_name="Country",
        resolver=lambda r: r.country,
    ),
    "Tour": ArchivalFieldSpec(
        name="Tour", display_name="Tour",
        resolver=lambda r: r.tour,
    ),
    "Show": ArchivalFieldSpec(
        name="Show", display_name="Show",
        resolver=lambda r: r.show,
    ),
    "Generation": ArchivalFieldSpec(
        name="Generation", display_name="Generation",
        resolver=lambda r: r.generation,
    ),
    "Audio Source": ArchivalFieldSpec(
        name="Audio Source", display_name="Audio Source",
        resolver=lambda r: r.audio_source,
    ),
    "Video Source": ArchivalFieldSpec(
        name="Video Source", display_name="Video Source",
        resolver=lambda r: r.video_source,
    ),
    "Master": ArchivalFieldSpec(
        name="Master", display_name="Master",
        resolver=lambda r: r.master,
    ),
    "Notes": ArchivalFieldSpec(
        name="Notes", display_name="Notes",
        resolver=lambda r: r.notes,
    ),
    "Description": ArchivalFieldSpec(
        name="Description", display_name="Description",
        resolver=lambda r: r.description,
    ),
}

# Suggestion fields — archival + configurable suggestion list.
SUGGESTION_FIELDS: dict[str, SuggestionFieldSpec] = {
    "Type": SuggestionFieldSpec(
        name="Type", display_name="Type",
        resolver=lambda r: r.type,
        suggestions_key="type",
    ),
    "Source": SuggestionFieldSpec(
        name="Source", display_name="Source",
        resolver=lambda r: r.source,
        suggestions_key="source",
    ),
    "Quality": SuggestionFieldSpec(
        name="Quality", display_name="Quality",
        resolver=lambda r: r.quality,
        suggestions_key="quality",
    ),
}


# ---------------------------------------------------------------------------
# Unified lookup
# ---------------------------------------------------------------------------

# Merge all fields into one dict for fast lookup.
ALL_FIELDS: dict[str, FieldSpec] = {}
ALL_FIELDS.update(TECHNICAL_FIELDS)
ALL_FIELDS.update(ARCHIVAL_FIELDS)
ALL_FIELDS.update(SUGGESTION_FIELDS)


def get_field_spec(name: str) -> FieldSpec | None:
    """Return the FieldSpec for *name*, or ``None`` if not registered."""
    return ALL_FIELDS.get(name)


def is_technical(name: str) -> bool:
    """Return True if *name* is registered as a technical field."""
    spec = ALL_FIELDS.get(name)
    return spec is not None and spec.category == FieldCategory.TECHNICAL


def is_archival(name: str) -> bool:
    """Return True if *name* is registered as an archival or suggestion field."""
    spec = ALL_FIELDS.get(name)
    return spec is not None and spec.category in (
        FieldCategory.ARCHIVAL, FieldCategory.SUGGESTION,
    )


def is_suggestion(name: str) -> bool:
    """Return True if *name* is registered as a suggestion field."""
    spec = ALL_FIELDS.get(name)
    return spec is not None and spec.category == FieldCategory.SUGGESTION


def all_field_names() -> list[str]:
    """Return all registered field names in definition order."""
    return list(ALL_FIELDS.keys())
