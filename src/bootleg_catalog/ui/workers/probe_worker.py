"""Background probe worker — metadata probing off the UI thread.

Worker layer only: no widget creation and no thread ownership.  The
caller creates the :class:`~PySide6.QtCore.QThread`, moves the worker
onto it, and wires ``started`` / ``finished`` / ``failed`` / ``quit``
itself — the same pattern as :mod:`bootleg_catalog.ui.workers.rip_scan_worker`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from bootleg_catalog.core.export_session_service import (
    ExportSessionService,
    ExportSessionServiceError,
)


class ProbeWorker(QObject):
    """Probe a single media file's metadata on a worker thread.

    ``run`` never raises — every outcome is reported through
    ``finished`` (carrying the probed :class:`ExportRecording`) or
    ``failed`` (carrying a human-readable message).
    """

    finished = Signal(object)  # ExportRecording
    failed = Signal(str)       # human-readable message

    def __init__(self, service: ExportSessionService, path: str | Path) -> None:
        super().__init__()
        self._service = service
        self._path = path

    @Slot()
    def run(self) -> None:
        """Call ``service.probe_recording_from_path(path)``; emit finished/failed."""
        try:
            recording = self._service.probe_recording_from_path(self._path)
        except ExportSessionServiceError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Probe failed: {exc}")
        else:
            self.finished.emit(recording)
