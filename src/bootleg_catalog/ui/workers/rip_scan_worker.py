"""Rip scan and batch probe workers — background "+ Add from Folder…".

Worker-layer conventions from :mod:`bootleg_catalog.ui.workers.probe_worker`:
no widget creation, no thread ownership, ``run`` never raises.  The caller
owns the :class:`~PySide6.QtCore.QThread` and wires ``started`` /
``finished`` / ``failed`` / ``quit`` itself.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from bootleg_catalog.core.export_session_service import (
    ExportSessionService,
    ExportSessionServiceError,
)
from bootleg_catalog.core.rip_scanner import RipScanError, scan_rip_folder
from bootleg_catalog.models.export_recording import ExportRecording


class RipScanWorker(QObject):
    """Scan a folder as a physical-media rip on a worker thread.

    ``run`` never raises.  :class:`RipScanError` — including the
    ``format_file_size``-formatted 100 GiB guard message produced by
    :func:`scan_rip_folder` — is reported verbatim through ``failed``;
    ``OSError`` and anything unexpected is wrapped as
    ``Rip scan failed: …``.  The size guard itself lives only in
    :func:`scan_rip_folder`; this worker never duplicates it.
    """

    finished = Signal(object)  # RipScanResult
    failed = Signal(str)       # human-readable message

    def __init__(self, folder: str | Path) -> None:
        super().__init__()
        self._folder = folder

    @Slot()
    def run(self) -> None:
        """Call scan_rip_folder(folder); emit finished/failed."""
        try:
            result = scan_rip_folder(self._folder)
        except RipScanError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Rip scan failed: {exc}")
        else:
            self.finished.emit(result)


class BatchProbeWorker(QObject):
    """Probe many files on a worker thread; returns recordings, adds none.

    ``probe_recording_from_path`` is pure I/O + Python (no widget
    creation), so the service reference is safe to use here.
    :meth:`ExportSessionService.add_recording` is never called from this
    worker — the main thread commits the returned recordings when it
    handles ``finished``.
    """

    progress = Signal(int, int)     # (current, total)
    finished = Signal(list, list)   # (added_recordings, failed_paths)
    failed = Signal(str)            # catastrophic failures only

    def __init__(self, service: ExportSessionService, paths: list[str]) -> None:
        super().__init__()
        self._service = service
        self._paths = paths

    @Slot()
    def run(self) -> None:
        """Probe every path; progress per file, finished with both lists."""
        added: list[ExportRecording] = []
        failed: list[str] = []
        total = len(self._paths)
        try:
            for index, path in enumerate(self._paths):
                try:
                    recording = self._service.probe_recording_from_path(path)
                except ExportSessionServiceError:
                    failed.append(path)
                except Exception:
                    # Unexpected per-file errors are recorded like probe
                    # failures so one bad file never aborts the batch.
                    failed.append(path)
                else:
                    added.append(recording)
                self.progress.emit(index + 1, total)
            self.finished.emit(added, failed)
        except Exception as exc:
            # Catastrophic escape (should not happen): tell the caller.
            self.failed.emit(f"Batch probe failed: {exc}")
