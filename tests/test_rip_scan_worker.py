"""Tests for the rip scan and batch probe workers (L15.3d.3)."""

from __future__ import annotations

import gc
import os
from unittest.mock import MagicMock, patch

import pytest

from bootleg_catalog.core.export_session_service import ExportSessionServiceError
from bootleg_catalog.core.rip_scanner import RipScanError, RipScanResult
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.ui.workers.rip_scan_worker import BatchProbeWorker, RipScanWorker

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _drain_deferred_deletes(qt_app):
    """Flush queued Qt deferred deletes while live top-level widgets are held.

    Mirrors the lifecycle hardening fixture in test_probe_worker.py:
    deferred deletes are flushed while widgets are still alive, then this
    test's cycle garbage is collected now, while Qt is fully alive, so
    shutdown never finalizes a large garbage batch in a dangerous order.
    """
    yield
    live_widgets = list(qt_app.topLevelWidgets())
    if live_widgets:
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        live_widgets.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_recording(
    source_path: str = "C:/media/concert.mkv",
    **overrides,
) -> ExportRecording:
    defaults = dict(
        source_path=source_path,
        extension=".mkv",
        file_size=1_000_000_000,
        duration=7200.0,
        video_codec="h264",
        audio_codec="aac",
        screenshot_paths=[],
        show=None,
        title=None,
    )
    defaults.update(overrides)
    return ExportRecording(**defaults)


def _make_result(video_files: list[str], total: int = 7_000_000_000) -> RipScanResult:
    return RipScanResult(
        folder_path="C:/rip",
        rip_kind="dvd",
        video_files=video_files,
        total_size_bytes=total,
    )


# ---------------------------------------------------------------------------
# RipScanWorker.run behaviour
# ---------------------------------------------------------------------------

class TestRipScanWorker:
    def test_run_emits_finished_with_rip_scan_result(self, qt_app) -> None:
        """A successful scan emits finished with exactly scan_rip_folder's result."""
        result = _make_result(["C:/rip/VIDEO_TS/VTS_01_1.VOB"])
        worker = RipScanWorker("C:/rip")
        finished: list[object] = []
        failed: list[str] = []
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        with patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            return_value=result,
        ) as mock_scan:
            worker.run()

        assert finished == [result]
        assert failed == []
        mock_scan.assert_called_once_with("C:/rip")

    def test_run_emits_rip_scan_error_message_verbatim(self, qt_app) -> None:
        """RipScanError (e.g. the size guard) reaches failed unchanged."""
        message = (
            "This rip is 120.0 GB, which exceeds the 100.0 GB limit for "
            "folder scans.\n"
            "Add individual recordings instead of the whole folder."
        )
        worker = RipScanWorker("C:/huge-rip")
        finished: list[object] = []
        failed: list[str] = []
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        with patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            side_effect=RipScanError(message),
        ):
            worker.run()

        assert failed == [message]
        assert finished == []

    def test_run_wraps_os_error_in_generic_message(self, qt_app) -> None:
        """An OSError from the scan is wrapped, reported once, and not raised."""
        worker = RipScanWorker("C:/locked-rip")
        finished: list[object] = []
        failed: list[str] = []
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        with patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            side_effect=OSError("access is denied"),
        ):
            worker.run()

        assert finished == []
        assert failed == ["Rip scan failed: access is denied"]


# ---------------------------------------------------------------------------
# BatchProbeWorker.run behaviour
# ---------------------------------------------------------------------------

class TestBatchProbeWorker:
    def test_progress_emitted_once_per_path(self, qt_app) -> None:
        """progress reports (i, total) after every file, in order."""
        paths = ["C:/rip/a.VOB", "C:/rip/b.VOB", "C:/rip/c.VOB"]
        service = MagicMock()
        service.probe_recording_from_path.side_effect = [
            _make_recording(source_path=path) for path in paths
        ]
        worker = BatchProbeWorker(service, list(paths))
        progress: list[tuple[int, int]] = []
        worker.progress.connect(lambda cur, tot: progress.append((cur, tot)))

        worker.run()

        assert progress == [(1, 3), (2, 3), (3, 3)]
        assert service.probe_recording_from_path.call_count == 3

    def test_finished_returns_all_recordings_when_every_probe_succeeds(
        self, qt_app,
    ) -> None:
        """All-success batches finish with every recording and no failures."""
        paths = ["C:/rip/a.VOB", "C:/rip/b.VOB"]
        rec_a = _make_recording(source_path=paths[0])
        rec_b = _make_recording(source_path=paths[1])
        service = MagicMock()
        service.probe_recording_from_path.side_effect = [rec_a, rec_b]
        worker = BatchProbeWorker(service, list(paths))
        payloads: list[tuple[list, list]] = []
        worker.finished.connect(lambda added, failed: payloads.append((added, failed)))

        worker.run()

        assert payloads == [([rec_a, rec_b], [])]

    def test_partial_failure_returns_added_and_failed_paths(self, qt_app) -> None:
        """A probe error lands in failed while the other files still finish."""
        paths = ["C:/rip/a.VOB", "C:/rip/bad.VOB", "C:/rip/c.VOB"]
        rec_a = _make_recording(source_path=paths[0])
        rec_c = _make_recording(source_path=paths[2])
        service = MagicMock()
        service.probe_recording_from_path.side_effect = [
            rec_a,
            ExportSessionServiceError("ffprobe exited with 1"),
            rec_c,
        ]
        worker = BatchProbeWorker(service, list(paths))
        payloads: list[tuple[list, list]] = []
        progress: list[tuple[int, int]] = []
        worker.finished.connect(lambda added, failed: payloads.append((added, failed)))
        worker.progress.connect(lambda cur, tot: progress.append((cur, tot)))

        worker.run()

        assert payloads == [([rec_a, rec_c], [paths[1]])]
        # The failed file still counts as a progress step.
        assert progress == [(1, 3), (2, 3), (3, 3)]

    def test_continues_after_unexpected_error(self, qt_app) -> None:
        """An unexpected per-file error is recorded and the loop continues."""
        paths = ["C:/rip/gone.VOB", "C:/rip/b.VOB"]
        rec_b = _make_recording(source_path=paths[1])
        service = MagicMock()
        service.probe_recording_from_path.side_effect = [OSError("disk vanished"), rec_b]
        worker = BatchProbeWorker(service, list(paths))
        payloads: list[tuple[list, list]] = []
        progress: list[tuple[int, int]] = []
        worker.finished.connect(lambda added, failed: payloads.append((added, failed)))
        worker.progress.connect(lambda cur, tot: progress.append((cur, tot)))

        worker.run()

        assert payloads == [([rec_b], [paths[0]])]
        assert progress == [(1, 2), (2, 2)]
