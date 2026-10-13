"""Pure model for multi-part recordings (1-2 video files, one entry).

Contains only frozen dataclasses and pure functions: no I/O, no
FFprobe, no side effects of any kind.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from bootleg_catalog.models.export_recording import ExportRecording

# Fields that can be resolved by the divergence dialog.
_RESOLVABLE_FIELDS = frozenset({"resolution", "video_codec", "audio_codec"})


@dataclass(frozen=True)
class PartTech:
    """Technical facts for a single video file part."""

    path: str
    duration: float | None
    file_size: int
    video_width: int | None
    video_height: int | None
    video_codec: str | None
    audio_codec: str | None
    frame_rate: float | None
    audio_channels: int | None
    audio_sample_rate: int | None
    audio_bitrate: int | None


@dataclass(frozen=True)
class MultiPartProbe:
    """Technical facts aggregated across 1-2 probed parts."""

    parts: tuple[PartTech, ...]
    duration: float | None
    file_size: int
    video_width: int | None
    video_height: int | None
    video_codec: str | None
    audio_codec: str | None
    frame_rate: float | None
    audio_channels: int | None
    audio_sample_rate: int | None
    audio_bitrate: int | None
    # Subset of {"resolution", "video_codec", "audio_codec"}: fields
    # whose values differ between the parts.
    divergent: frozenset[str]


def _part_tech(rec: ExportRecording) -> PartTech:
    """Project an :class:`ExportRecording` onto a :class:`PartTech`."""
    return PartTech(
        path=rec.source_path,
        duration=rec.duration,
        file_size=rec.file_size if rec.file_size is not None else 0,
        video_width=rec.video_width,
        video_height=rec.video_height,
        video_codec=rec.video_codec,
        audio_codec=rec.audio_codec,
        frame_rate=rec.frame_rate,
        audio_channels=rec.audio_channels,
        audio_sample_rate=rec.audio_sample_rate,
        audio_bitrate=rec.audio_bitrate,
    )


def aggregate_parts(probeds: list[ExportRecording]) -> MultiPartProbe:
    """Aggregate 1-2 probed recordings into a single probe result.

    Raises ``ValueError`` for an empty list or more than two probeds.
    """
    if len(probeds) == 0:
        raise ValueError("aggregate_parts requires at least one probed recording")
    if len(probeds) > 2:
        raise ValueError("At most two parts are supported")

    divergent: set[str] = set()

    def shared(values: list, tag: str):
        """Same non-None value across all parts, else None + divergence tag."""
        unique = set(values)
        if len(unique) == 1:
            return next(iter(unique))
        divergent.add(tag)
        return None

    # duration: sum of non-None durations; any None makes the whole None
    if any(rec.duration is None for rec in probeds):
        duration: float | None = None
    else:
        duration = sum(rec.duration for rec in probeds if rec.duration is not None)

    # file_size: sum, None treated as 0
    file_size = sum(
        (rec.file_size if rec.file_size is not None else 0) for rec in probeds
    )

    first = probeds[0]

    return MultiPartProbe(
        parts=tuple(_part_tech(rec) for rec in probeds),
        duration=duration,
        file_size=file_size,
        video_width=shared(
            [rec.video_width for rec in probeds], "resolution"
        ),
        video_height=shared(
            [rec.video_height for rec in probeds], "resolution"
        ),
        video_codec=shared([rec.video_codec for rec in probeds], "video_codec"),
        audio_codec=shared([rec.audio_codec for rec in probeds], "audio_codec"),
        # First part's values, no divergence check.
        frame_rate=first.frame_rate,
        audio_channels=first.audio_channels,
        audio_sample_rate=first.audio_sample_rate,
        audio_bitrate=first.audio_bitrate,
        divergent=frozenset(divergent),
    )


def distribute_screenshot_counts(total: int, part_count: int) -> tuple[int, ...]:
    """Split *total* screenshots across *part_count* (1 or 2) parts.

    With two parts and an odd total, the first part gets the extra
    screenshot.  Raises ``ValueError`` for any other part count.
    """
    if part_count == 1:
        return (total,)
    if part_count == 2:
        if total % 2 == 0:
            return (total // 2, total // 2)
        return (total // 2 + 1, total // 2)
    raise ValueError(f"part_count must be 1 or 2, got {part_count}")


def apply_resolutions(
    aggregate: MultiPartProbe,
    resolutions: dict[str, int],
) -> MultiPartProbe:
    """Return a new MultiPartProbe with divergent fields resolved
    from the chosen part index (0 or 1). Non-divergent fields unchanged.
    Empty resolutions dict returns the aggregate unchanged.
    Unknown field keys raise ValueError.

    A resolved field takes the chosen part's value (for ``"resolution"``
    both width and height) and is removed from ``divergent``.  The
    ``parts`` tuple is unchanged.
    """
    if not resolutions:
        return aggregate

    divergent = set(aggregate.divergent)
    updates: dict[str, object] = {}
    for field, index in resolutions.items():
        if field not in _RESOLVABLE_FIELDS:
            raise ValueError(f"Unknown divergent field: {field!r}")
        if index not in (0, 1):
            raise ValueError(f"Part index must be 0 or 1, got {index!r}")
        if index >= len(aggregate.parts):
            raise ValueError(
                f"Part index {index} out of range for "
                f"{len(aggregate.parts)} part(s)"
            )
        part = aggregate.parts[index]
        if field == "resolution":
            updates["video_width"] = part.video_width
            updates["video_height"] = part.video_height
        else:
            updates[field] = getattr(part, field)
        divergent.discard(field)

    updates["divergent"] = frozenset(divergent)
    return replace(aggregate, **updates)
