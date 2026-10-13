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
)


class ScreenshotWorker(QObject):
    """Extract a plan's frames on a worker thread.

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
        plan: ScreenshotPlan,
        ffmpeg_path: str,
        overwrite: bool = False,
    ) -> None:
        super().__init__()
        self._video_path = video_path
        self._plan = plan
        self._ffmpeg_path = ffmpeg_path
        self._overwrite = overwrite

    @Slot()
    def run(self) -> None:
        """Generate every timestamp; emit finished(paths) or failed(msg)."""
        try:
            paths = generate_screenshots(
                self._video_path,
                self._plan,
                self._ffmpeg_path,
                overwrite=self._overwrite,
                progress_cb=lambda current, total: self.progress.emit(
                    current, total,
                ),
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(paths)
