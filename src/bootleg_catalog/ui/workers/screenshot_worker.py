"""Background screenshot worker — ffmpeg extraction off the UI thread.

Worker layer only: no widget creation and no thread ownership.  The
caller creates the :class:`~PySide6.QtCore.QThread`, moves the worker
onto it, and wires ``started`` / ``finished`` / ``failed`` / ``quit``
itself — the same pattern as :mod:`bootleg_catalog.ui.workers.probe_worker`.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from bootleg_catalog.core.screenshot_generator import (
    ScreenshotPlan,
    generate_screenshots,
    generate_screenshots_for_recording,
)
from bootleg_catalog.models.export_recording import ExportRecording


class ScreenshotWorker(QObject):
    """Extract a plan's frames on a worker thread.

    Two shapes: plan mode takes a pre-built plan for one file (manual
    timestamps, single-part recordings); recording mode takes a whole
    1-2 part recording plus the requested total, and ``run`` probes
    each part's duration here, on the worker thread, before planning
    its timestamps.

    ``run`` never raises — every outcome is reported through
    ``finished`` (the generated paths, in timestamp order) or
    ``failed`` (a human-readable message).  ``progress`` fires once
    per completed timestamp.
    """

    progress = Signal(int, int)  # current, total
    finished = Signal(list)      # list[str] of output paths
    failed = Signal(str)         # human-readable message

    def __init__(
        self,
        video_path: str,
        plan: ScreenshotPlan | None,
        ffmpeg_path: str,
        overwrite: bool = False,
        *,
        recording: ExportRecording | None = None,
        total: int = 0,
        ffprobe_path: str | None = None,
    ) -> None:
        super().__init__()
        self._video_path = video_path
        self._plan = plan
        self._ffmpeg_path = ffmpeg_path
        self._overwrite = overwrite
        self._recording = recording
        self._total = total
        self._ffprobe_path = ffprobe_path

    @Slot()
    def run(self) -> None:
        """Generate every timestamp; emit finished(paths) or failed(msg)."""
        try:
            if self._recording is not None:
                paths = generate_screenshots_for_recording(
                    self._recording,
                    self._total,
                    self._ffmpeg_path,
                    ffprobe_path=self._ffprobe_path,
                    overwrite=self._overwrite,
                    progress_cb=self._emit_progress,
                )
            else:
                paths = generate_screenshots(
                    self._video_path,
                    self._plan,
                    self._ffmpeg_path,
                    overwrite=self._overwrite,
                    progress_cb=self._emit_progress,
                )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(paths)

    def _emit_progress(self, current: int, total: int) -> None:
        self.progress.emit(current, total)
