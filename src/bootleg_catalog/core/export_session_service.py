"""Export session service — high-level API for the interactive export workflow.

Manages an in-memory :class:`ExportSession` and exposes operations that
do not require the caller to know about FFprobe, path resolution,
screenshot discovery, or show grouping.

Completely isolated from the catalog, repository, SQLite, and Recording.
"""

from __future__ import annotations

from pathlib import Path

from bootleg_catalog.core.export_session import ExportSession, ExportSessionError
from bootleg_catalog.core.layout_resolver import LayoutResolver
from bootleg_catalog.core.layout_service import screenshots_for_recording
from bootleg_catalog.core.session_store import SessionData, load_session, save_session
from bootleg_catalog.core.show_grouper import group_by_show
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import LayoutModel
from bootleg_catalog.models.resolved_export import ResolvedExportDocument
from bootleg_catalog.utils.ffmpeg import FFprobeError, probe


class ExportSessionServiceError(Exception):
    """Raised when an export session service operation fails."""


class ExportSessionService:
    """High-level service for the interactive export workflow.

    Wraps an :class:`ExportSession` and provides convenience methods that
    handle FFprobe probing, screenshot discovery, and document resolution.

    Parameters
    ----------
    layout:
        The initial layout for this export session.
    ffprobe_path:
        Optional explicit path to the ``ffprobe`` executable.  When
        ``None``, the system PATH is used.
    probe_timeout:
        Timeout in seconds for FFprobe subprocess calls.
    """

    def __init__(
        self,
        layout: LayoutModel,
        *,
        ffprobe_path: str | None = None,
        probe_timeout: int = 30,
    ) -> None:
        self._session = ExportSession(layout)
        self._ffprobe_path = ffprobe_path
        self._probe_timeout = probe_timeout
        self._dirty = False
        self._display_name = ""

    # -- Properties ----------------------------------------------------------

    @property
    def session(self) -> ExportSession:
        """The underlying export session."""
        return self._session

    @property
    def layout(self) -> LayoutModel:
        """The current layout."""
        return self._session.layout

    def set_layout(self, layout: LayoutModel) -> None:
        """Replace the layout without touching recordings."""
        self._session.layout = layout
        self._dirty = True

    # -- Recording management ------------------------------------------------

    def probe_recording_from_path(self, path: str | Path) -> ExportRecording:
        """Probe a media file and return an ExportRecording without adding it.

        This allows callers to inspect the recording (e.g. in a dialog)
        before deciding whether to commit it to the session.

        Parameters
        ----------
        path:
            Absolute path to the media file.

        Returns
        -------
        ExportRecording
            The newly created recording, with technical fields populated
            from FFprobe (or ``None`` on probe failure) and
            ``screenshot_paths`` discovered from sidecar files.

        Raises
        ------
        ExportSessionServiceError
            If the file does not exist on disk.
        """
        source = Path(path)
        if not source.is_file():
            raise ExportSessionServiceError(
                f"The file does not exist:\n{source}"
            )

        abs_path = str(source.resolve())
        ext = source.suffix

        # Gather filesystem data
        file_size: int | None = None
        try:
            file_size = source.stat().st_size
        except OSError:
            pass

        probe_error: str | None = None
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

        # Probe
        try:
            result = probe(
                source,
                ffprobe_path=self._ffprobe_path,
                timeout=self._probe_timeout,
            )
            duration = result.duration
            container_format = result.container_format
            video_codec = result.video_codec
            video_width = result.video_width
            video_height = result.video_height
            frame_rate = result.frame_rate
            pixel_format = result.pixel_format
            audio_codec = result.audio_codec
            audio_channels = result.audio_channels
            audio_sample_rate = result.audio_sample_rate
            audio_bitrate = result.audio_bitrate
        except FFprobeError as exc:
            probe_error = str(exc)

        # Discover screenshots (uses path, does not require probe success)
        screenshot_paths: list[str] = []
        try:
            screenshot_paths = screenshots_for_recording(abs_path)
        except Exception:  # noqa: BLE001 — best-effort
            pass

        return ExportRecording(
            source_path=abs_path,
            extension=ext,
            file_size=file_size,
            duration=duration,
            container_format=container_format,
            video_codec=video_codec,
            video_width=video_width,
            video_height=video_height,
            frame_rate=frame_rate,
            pixel_format=pixel_format,
            audio_codec=audio_codec,
            audio_channels=audio_channels,
            audio_sample_rate=audio_sample_rate,
            audio_bitrate=audio_bitrate,
            screenshot_paths=screenshot_paths,
            probe_error=probe_error,
        )

    def add_recording(self, recording: ExportRecording) -> None:
        """Add a pre-built ExportRecording and mark the session dirty.

        Callers that already hold an :class:`ExportRecording` (for
        example the result of an add dialog) must use this instead of
        reaching into :attr:`session`, so the dirty flag is set.

        Raises
        ------
        ExportSessionError
            If a recording with the same ``source_path`` already exists.
        """
        self._session.add_recording(recording)
        self._dirty = True

    def add_recording_from_path(self, path: str | Path) -> ExportRecording:
        """Probe a media file and add it to the session.

        Convenience wrapper around :meth:`probe_recording_from_path`
        that also commits the recording to the session.

        Parameters
        ----------
        path:
            Absolute path to the media file.

        Returns
        -------
        ExportRecording
            The newly created recording.

        Raises
        ------
        ExportSessionServiceError
            If the file does not exist on disk.
        ExportSessionError
            If a recording with the same ``source_path`` already exists.
        """
        recording = self.probe_recording_from_path(path)
        self._session.add_recording(recording)
        self._dirty = True
        return recording

    def remove_recording(self, source_path: str) -> ExportRecording:
        """Remove a recording by its source path.

        Returns the removed :class:`ExportRecording`.

        Raises
        ------
        ExportSessionError
            If no recording with the given path exists.
        """
        removed = self._session.remove_recording(source_path)
        self._dirty = True
        return removed

    def update_recording(
        self,
        source_path: str,
        updated: ExportRecording,
    ) -> None:
        """Replace a recording identified by *source_path*.

        This is how the future Edit dialog will save archival edits.
        No filesystem or database changes are made.

        Raises
        ------
        ExportSessionError
            If no recording with the given path exists, or if the new
            recording's ``source_path`` does not match.
        """
        self._session.update_recording(source_path, updated)
        self._dirty = True

    def clear(self) -> None:
        """Clear all recordings.  The layout is not touched."""
        self._session.clear_recordings()
        self._dirty = True

    # -- Document resolution -------------------------------------------------

    def build_document(self) -> ResolvedExportDocument:
        """Resolve the current session into a :class:`ResolvedExportDocument`.

        Recomputes every call so that layout and archival changes are
        reflected.  Uses :func:`group_by_show` for grouping and
        :class:`LayoutResolver` for resolution.
        """
        groups = group_by_show(self._session.recordings)
        return LayoutResolver().resolve(self._session.layout, groups)

    # -- Screenshot access ---------------------------------------------------

    def get_screenshots(self, source_path: str) -> list[str]:
        """Return the screenshot paths for a recording in the session.

        Returns ``[]`` if the recording is not in the session.
        """
        try:
            rec = self._session.get_recording(source_path)
        except ExportSessionError:
            return []
        return list(rec.screenshot_paths)

    # -- Dirty state ---------------------------------------------------------

    def is_dirty(self) -> bool:
        """Return ``True`` when there are unsaved changes.

        The flag is set by every mutating operation
        (:meth:`add_recording_from_path`, :meth:`remove_recording`,
        :meth:`update_recording`, :meth:`clear`, :meth:`set_layout`,
        :meth:`set_display_name`) and cleared by :meth:`save` and
        :meth:`load`.
        """
        return self._dirty

    def mark_clean(self) -> None:
        """Clear the dirty flag without saving or loading."""
        self._dirty = False

    # -- Display name --------------------------------------------------------

    @property
    def display_name(self) -> str:
        """The session's display name (empty until set or loaded)."""
        return self._display_name

    def set_display_name(self, name: str) -> None:
        """Set the session display name and mark the session dirty."""
        self._display_name = name
        self._dirty = True

    # -- Persistence ---------------------------------------------------------

    def save(self, path: str | Path, *, embed_paths: set[str] | None = None) -> Path:
        """Write the current session to disk, clearing the dirty flag.

        Parameters
        ----------
        path:
            Destination ``.bootleg`` file path.
        embed_paths:
            Screenshot source paths to embed into the archive instead of
            referencing by path (see :func:`save_session`).

        Returns
        -------
        Path
            The final path written.
        """
        data = SessionData(
            display_name=self._display_name,
            layout=self._session.layout,
            recordings=self._session.recordings,
        )
        result = save_session(data, path, embed_paths=embed_paths)
        self._dirty = False
        return result

    def load(self, path: str | Path) -> None:
        """Replace the current session state from disk, clearing the dirty flag.

        Replaces the layout, the recordings, and the display name with
        the contents of the session file.

        Raises
        ------
        SessionStoreError
            If the file cannot be read.
        ExportSessionServiceError
            If the file's recordings are inconsistent (duplicates).
        """
        data = load_session(path)
        new_session = ExportSession(data.layout)
        for recording in data.recordings:
            try:
                new_session.add_recording(recording)
            except ExportSessionError as exc:
                raise ExportSessionServiceError(
                    f"Could not load the recordings from the session file:\n{exc}"
                ) from exc
        self._session = new_session
        self._display_name = data.display_name
        self._dirty = False
