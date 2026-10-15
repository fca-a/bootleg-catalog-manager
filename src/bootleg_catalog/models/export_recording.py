"""Export recording model.

An independent data model for recordings in an export session.  This is
NOT linked to the catalog, repository, or SQLite in any way.

Technical fields are populated by FFprobe.  Archival fields are
populated by the user.  Screenshots are discovered via sidecar
patterns.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExportRecording:
    """A recording in an export session.

    This model is intentionally independent of :class:`Recording` in the
    catalog.  It has no database ID, no folder reference, and no
    dependency on SQLite.
    """

    # --- Identity ---
    source_path: str

    # --- Technical (auto-populated by FFprobe) ---
    extension: str | None = None
    # True for recordings added via "+ Add from Folder…": extension
    # then carries a user-chosen format label ("VIDEO_TS", "DVD"…)
    # instead of a media-file extension, and the duration is
    # user-typed (or None) because probing the first VOB yields only a
    # partial chunk.
    is_rip: bool = False
    file_size: int | None = None
    duration: float | None = None
    container_format: str | None = None
    video_codec: str | None = None
    video_width: int | None = None
    video_height: int | None = None
    frame_rate: float | None = None
    pixel_format: str | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    audio_sample_rate: int | None = None
    audio_bitrate: int | None = None

    # --- Audio (is_audio entries only) ---
    # True for entries added via "+ Add Audio Recording…": the source
    # is an audio file or a folder of tracks (an album), never video.
    is_audio: bool = False
    # Audio files backing this entry: (source_path,) for a single
    # file, every audio file inside the folder for an album.  Always
    # empty when is_audio is False (validated in __post_init__).
    tracks: tuple[str, ...] = ()

    # --- Archival (user-populated) ---
    title: str | None = None
    year: int | None = None
    date: str | None = None
    venue: str | None = None
    location: str | None = None
    country: str | None = None
    tour: str | None = None
    show: str | None = None
    source: str | None = None
    type: str | None = None
    generation: str | None = None
    quality: str | None = None
    # Free text; NFT (Not For Trade) / NFTF (Not For Trade Forever) are
    # the default suggestions (see settings_service).
    trade_status: str | None = None
    audio_source: str | None = None
    video_source: str | None = None
    master: str | None = None
    notes: str | None = None
    description: str | None = None

    # --- Screenshots (auto-discovered from sidecars) ---
    screenshot_paths: list[str] = field(default_factory=list)
    # Subset of screenshot_paths the user attached manually.  These are
    # embedded into the .bootleg on save (see session_store); paths that
    # are absent here are auto sidecar screenshots and stay referenced.
    manual_paths: set[str] = field(default_factory=set)

    # --- Probe diagnostics ---
    probe_error: str | None = None

    # --- Multi-part (1-2 video files treated as one entry) ---
    # Empty tuple is normalised to (source_path,) in __post_init__.
    parts: tuple[str, ...] = ()
    # Size of the part-1 block when splitting screenshot_paths across
    # two parts.  None = callers compute the default.  When
    # set on a single-part recording it must equal len(screenshot_paths).
    screenshot_split: int | None = None

    def __post_init__(self) -> None:
        """Normalise ``parts``/``tracks`` and validate their invariants."""
        parts = tuple(self.parts)
        if len(parts) >= 3:
            raise ValueError("At most two parts are supported")
        if len(parts) == 0:
            parts = (self.source_path,)
        elif parts[0] != self.source_path:
            raise ValueError(
                f"parts[0] must equal source_path: "
                f"{parts[0]!r} != {self.source_path!r}"
            )
        self.parts = parts

        # Audio: tracks list the album's files and only make sense for
        # audio entries — a video recording must never carry them.
        self.tracks = tuple(self.tracks)
        if not self.is_audio and self.tracks:
            raise ValueError(
                "tracks can only be populated when is_audio is True"
            )

        if self.screenshot_split is not None:
            count = len(self.screenshot_paths)
            if len(parts) == 1:
                if self.screenshot_split != count:
                    raise ValueError(
                        "screenshot_split must equal len(screenshot_paths) "
                        f"for a single-part recording: "
                        f"{self.screenshot_split} != {count}"
                    )
            elif not 0 <= self.screenshot_split <= count:
                raise ValueError(
                    f"screenshot_split must be between 0 and {count}, "
                    f"got {self.screenshot_split}"
                )

    @property
    def is_multi_part(self) -> bool:
        """True when this recording spans two video files."""
        return len(self.parts) == 2

    def screenshot_part_index(self, split_default: int) -> tuple[int, ...]:
        """Return the part index of every screenshot, parallel to the list.

        Uses ``self.screenshot_split`` when set, otherwise *split_default*
        as the size of the part-1 block.  Single-part recordings are
        always part ``0``.
        """
        if len(self.parts) == 1:
            return (0,) * len(self.screenshot_paths)
        split = (
            self.screenshot_split
            if self.screenshot_split is not None
            else split_default
        )
        return tuple(
            0 if index < split else 1
            for index in range(len(self.screenshot_paths))
        )
