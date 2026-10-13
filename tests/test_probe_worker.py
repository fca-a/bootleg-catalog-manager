"""Tests for the background probe worker and its window wiring (L15.3d.2)."""

from __future__ import annotations

import gc
import os
from unittest.mock import MagicMock, patch

import pytest

from bootleg_catalog.core.export_session_service import (
    ExportSessionService,
    ExportSessionServiceError,
)
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    LayoutElement,
    LayoutLine,
    LayoutModel,
)
from bootleg_catalog.ui.export_preview_window import ExportPreviewWindow
from bootleg_catalog.ui.workers.probe_worker import ProbeWorker

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox


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

    A test that drops its last strong reference to a window while a Qt
    deferred delete is still pending can leave the widget referenced only
    by the closure that queued the delete, so the re-entrant destruction
    crashes the process on the next event dispatch.  Holding strong
    references across the flush lets the deferred deletes fire while the
    widgets are still alive; releasing afterwards then destroys them with
    an empty queue.
    """
    yield
    live_widgets = list(qt_app.topLevelWidgets())
    if live_widgets:
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        live_widgets.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    # Collect this test's cycle garbage now, while Qt is fully alive.
    # Patched-dialog call_args (e.g. a mocked
    # QFileDialog.getOpenFileName) pins its window in a reference cycle
    # that plain refcounting never frees; letting such garbage accumulate
    # until interpreter shutdown makes the final large batch finalize in
    # an order that corrupts the heap (observed as a crash with Windows
    # status 0xC0000374 before the summary line could print).
    gc.collect()


# ---------------------------------------------------------------------------
# Helpers / test doubles
# ---------------------------------------------------------------------------

def _make_layout(name: str = "Test Layout") -> LayoutModel:
    return LayoutModel(
        name=name,
        show_header=[
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Show", field_type=None),
            ]),
        ],
        recording_body=[
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Duration", field_type=None),
            ]),
        ],
    )


def _make_recording(
    source_path: str = "C:/media/concert.mkv",
    show: str | None = None,
    title: str | None = None,
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
        show=show,
        title=title,
    )
    defaults.update(overrides)
    return ExportRecording(**defaults)


def _make_service(layout: LayoutModel | None = None) -> ExportSessionService:
    return ExportSessionService(layout or _make_layout())


class _SyncQThread(QThread):
    """QThread double that runs its worker synchronously.

    ``start`` emits ``started`` directly; paired with a worker that
    stays in the main thread, the connected ``worker.run`` slot is
    invoked in place instead of being queued to a never-started thread.
    ``isRunning`` always reports False so the close guard treats the
    fake as idle.
    """

    def start(self, *args, **kwargs) -> None:  # noqa: N802 (Qt API)
        self.start_called = True
        self.started.emit()

    def isRunning(self) -> bool:  # noqa: N802 (Qt API)
        return False


class _SilentProbeWorker(QObject):
    """ProbeWorker double: records the call, never emits a result."""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, service, path) -> None:
        super().__init__()
        self._service = service
        self.path = path
        self.ran = False

    def moveToThread(self, thread) -> None:  # noqa: N802 (Qt API)
        # Deliberately a no-op: staying in the main thread keeps the
        # started -> run dispatch direct (synchronous) in tests.
        self.moved_thread = thread

    def run(self) -> None:
        self.ran = True


class _RunningQThread(QThread):
    """QThread double that always reports a running thread."""

    def isRunning(self) -> bool:  # noqa: N802 (Qt API)
        return True


# ---------------------------------------------------------------------------
# ProbeWorker.run behaviour
# ---------------------------------------------------------------------------

class TestProbeWorker:
    def test_run_emits_finished_with_probed_recording(self, qt_app) -> None:
        """Successful probe emits finished with exactly the probed recording."""
        service = MagicMock()
        recording = _make_recording()
        service.probe_recording_from_path.return_value = recording
        worker = ProbeWorker(service, "C:/media/concert.mkv")
        finished: list[object] = []
        failed: list[str] = []
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        worker.run()

        assert finished == [recording]
        assert failed == []
        service.probe_recording_from_path.assert_called_once_with(
            "C:/media/concert.mkv",
        )

    def test_run_emits_failed_with_service_error_message(self, qt_app) -> None:
        """ExportSessionServiceError is reported verbatim through failed."""
        service = MagicMock()
        service.probe_recording_from_path.side_effect = ExportSessionServiceError(
            "ffprobe failed: no such file",
        )
        worker = ProbeWorker(service, "C:/media/broken.mkv")
        finished: list[object] = []
        failed: list[str] = []
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        worker.run()

        assert failed == ["ffprobe failed: no such file"]
        assert finished == []

    def test_run_wraps_unexpected_errors_in_generic_message(self, qt_app) -> None:
        """An OSError from probing is wrapped, reported once, and not raised."""
        service = MagicMock()
        service.probe_recording_from_path.side_effect = OSError("disk vanished")
        worker = ProbeWorker(service, "C:/media/gone.mkv")
        finished: list[object] = []
        failed: list[str] = []
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        worker.run()

        assert finished == []
        assert len(failed) == 1
        assert failed[0].startswith("Probe failed")
        assert "disk vanished" in failed[0]


# ---------------------------------------------------------------------------
# ExportPreviewWindow background add flow
# ---------------------------------------------------------------------------

class TestProbeWindowWiring:
    def test_add_recording_locks_toolbar_and_starts_worker(self, qt_app) -> None:
        """Choosing a file locks the toolbar, shows busy, and starts the worker."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["C:/media/new.mkv"], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _SilentProbeWorker,
        ):
            win._on_add_recording()

        assert isinstance(win._probe_thread, _SyncQThread)
        assert win._probe_thread.start_called
        assert win._probe_worker.ran is True
        assert win._probe_worker.path == "C:/media/new.mkv"
        assert win._busy_label.text() == "Probing\u2026"
        assert not win._busy_label.isHidden()
        assert not win._toolbar.isEnabled()
        assert service.session.recording_count == 0

    def test_probe_finished_adds_recording_and_unlocks_toolbar(
        self, qt_app,
    ) -> None:
        """A finished probe runs the dialog, adds its result, and unlocks."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        probed = _make_recording(source_path="C:/media/other.mkv")
        accepted = _make_recording(title="From Dialog")
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["C:/media/new.mkv"], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _SilentProbeWorker,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
        ) as MockDialog:
            instance = MockDialog.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            instance.result_recording = accepted
            win._on_add_recording()
            win._probe_worker.finished.emit(probed)

        assert service.session.recording_count == 1
        assert service.session.get_recording(accepted.source_path).title == (
            "From Dialog"
        )
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_probe_failed_shows_warning_and_unlocks_toolbar(self, qt_app) -> None:
        """A failed probe warns with the worker message and unlocks."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["C:/media/new.mkv"], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _SilentProbeWorker,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._on_add_recording()
            assert not win._toolbar.isEnabled()  # locked while probing
            assert win._busy_label.text() == "Probing\u2026"
            win._probe_worker.failed.emit("ffprobe exploded")

        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[1] == "Probe Error"
        assert mock_warning.call_args.args[2] == "ffprobe exploded"
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()
        assert service.session.recording_count == 0

    def test_close_is_ignored_while_probe_thread_is_running(self, qt_app) -> None:
        """Closing with a running probe thread is refused before any prompt."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        win._probe_thread = _RunningQThread()
        event = MagicMock()

        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info:
            win.closeEvent(event)

        mock_info.assert_called_once()
        assert "still running" in mock_info.call_args.args[2]
        event.ignore.assert_called_once()
        event.accept.assert_not_called()
