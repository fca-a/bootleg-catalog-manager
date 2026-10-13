"""Export session model.

A temporary, in-memory workspace for building an export.  The session
holds a layout and a list of recordings.  It is intentionally isolated
from the catalog, repository, SQLite, and FFprobe.

LayoutModel → ExportSession → ShowGrouper → ResolvedExportDocument → Export
"""

from __future__ import annotations

from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import LayoutModel


class ExportSessionError(Exception):
    """Raised when an export session operation fails."""


class ExportSession:
    """A temporary workspace for building an export.

    The session is purely in-memory.  It does not touch the filesystem,
    database, or catalog in any way.

    Parameters
    ----------
    layout:
        The layout to use for this export session.
    """

    def __init__(self, layout: LayoutModel) -> None:
        self._layout = layout
        self._recordings: list[ExportRecording] = []

    # -- Properties ------------------------------------------------------

    @property
    def layout(self) -> LayoutModel:
        """The layout used by this session."""
        return self._layout

    @layout.setter
    def layout(self, value: LayoutModel) -> None:
        self._layout = value

    @property
    def recordings(self) -> list[ExportRecording]:
        """A shallow copy of the current recordings list.

        The list is a copy so callers cannot mutate internal state
        directly, but the ``ExportRecording`` objects themselves are
        shared.
        """
        return list(self._recordings)

    @property
    def recording_count(self) -> int:
        """Number of recordings in the session."""
        return len(self._recordings)

    # -- Mutation --------------------------------------------------------

    def add_recording(self, recording: ExportRecording) -> None:
        """Append a recording to the session.

        Parameters
        ----------
        recording:
            The recording to add.

        Raises
        ------
        ExportSessionError
            If a recording with the same ``source_path`` already exists.
        """
        if self._find_index(recording.source_path) is not None:
            raise ExportSessionError(
                f"A recording with path '{recording.source_path}' "
                f"already exists in the session."
            )
        self._recordings.append(recording)

    def remove_recording(self, source_path: str) -> ExportRecording:
        """Remove a recording by its source path.

        Parameters
        ----------
        source_path:
            The ``source_path`` of the recording to remove.

        Returns
        -------
        ExportRecording
            The removed recording.

        Raises
        ------
        ExportSessionError
            If no recording with the given path exists.
        """
        idx = self._find_index(source_path)
        if idx is None:
            raise ExportSessionError(
                f"No recording with path '{source_path}' in the session."
            )
        return self._recordings.pop(idx)

    def update_recording(
        self,
        source_path: str,
        recording: ExportRecording,
    ) -> None:
        """Replace a recording identified by *source_path*.

        The new recording's ``source_path`` must match the existing
        recording's path (identity is not transferable).

        Parameters
        ----------
        source_path:
            The path of the recording to replace.
        recording:
            The replacement recording.

        Raises
        ------
        ExportSessionError
            If no recording with the given path exists, or if the new
            recording's ``source_path`` does not match.
        """
        idx = self._find_index(source_path)
        if idx is None:
            raise ExportSessionError(
                f"No recording with path '{source_path}' in the session."
            )
        if recording.source_path != source_path:
            raise ExportSessionError(
                f"Replacement recording path '{recording.source_path}' "
                f"does not match target path '{source_path}'."
            )
        self._recordings[idx] = recording

    def clear_recordings(self) -> None:
        """Remove all recordings from the session."""
        self._recordings.clear()

    def get_recording(self, source_path: str) -> ExportRecording:
        """Return the recording with the given *source_path*.

        Raises
        ------
        ExportSessionError
            If no recording with the given path exists.
        """
        idx = self._find_index(source_path)
        if idx is None:
            raise ExportSessionError(
                f"No recording with path '{source_path}' in the session."
            )
        return self._recordings[idx]

    # -- Internal --------------------------------------------------------

    def _find_index(self, source_path: str) -> int | None:
        """Return the index of the recording with *source_path*, or None."""
        for i, rec in enumerate(self._recordings):
            if rec.source_path == source_path:
                return i
        return None
