"""Tests for ScreenshotManagerDialog (L15.4 manual screenshot attachment)."""

from __future__ import annotations

import gc
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.core.settings_service import AppSettings
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.ui.dialogs.screenshot_manager_dialog import (
    GenerateScreenshotsDialog,
    ScreenshotManagerDialog,
)

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QPushButton,
    QSpinBox,
)


# ---------------------------------------------------------------------------
# Fixtures (copied per test-file isolation rule — no cross-imports)
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
    """Flush queued Qt deferred deletes while live widgets are held.

    See test_export_preview_window for the full rationale (deferred-delete
    re-entrancy crash) and the gc.collect() standing fix for shutdown
    heap corruption.
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
    screenshots: list[str] | None = None,
    manual: set[str] | None = None,
    duration: float | None = None,
) -> ExportRecording:
    return ExportRecording(
        source_path="C:/media/concert.mkv",
        duration=duration,
        screenshot_paths=list(screenshots or []),
        manual_paths=set(manual or []),
    )


def _name_labels(dialog: ScreenshotManagerDialog) -> list[str]:
    return [
        widget.findChild(QLabel, "nameLabel").text()
        for widget in dialog._row_widgets
    ]


def _origin_labels(dialog: ScreenshotManagerDialog) -> list[str]:
    return [
        widget.findChild(QLabel, "originLabel").text()
        for widget in dialog._row_widgets
    ]


def _row_button(
    dialog: ScreenshotManagerDialog, row: int, object_name: str,
) -> QPushButton:
    return dialog._row_widgets[row].findChild(QPushButton, object_name)


# ---------------------------------------------------------------------------
# Test doubles for the generation flow (L15.9.2)
# ---------------------------------------------------------------------------

class _SyncQThread(QThread):
    """QThread double that runs its worker synchronously.

    Same double as test_probe_worker: ``start`` emits ``started``
    directly, so a worker that stays in the main thread runs in place;
    ``isRunning`` reports idle so close/lock guards treat it as done.
    """

    def start(self, *args, **kwargs) -> None:  # noqa: N802 (Qt API)
        self.start_called = True
        self.started.emit()

    def isRunning(self) -> bool:  # noqa: N802 (Qt API)
        return False


class _RecordingScreenshotWorker(QObject):
    """ScreenshotWorker double: records the call, never emits a result."""

    progress = Signal(int, int)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, video_path, plan, ffmpeg_path, overwrite=False) -> None:
        super().__init__()
        self.video_path = video_path
        self.plan = plan
        self.ffmpeg_path = ffmpeg_path
        self.overwrite = overwrite
        self.ran = False

    def moveToThread(self, thread) -> None:  # noqa: N802 (Qt API)
        # Deliberately a no-op: staying in the main thread keeps the
        # started -> run dispatch direct (synchronous) in tests.
        self.moved_thread = thread

    def run(self) -> None:
        self.ran = True


class _AcceptingGenerateDialog(GenerateScreenshotsDialog):
    """Modal double: set the count to 3 and accept immediately.

    Only ``exec`` is overridden, so the plan the manager builds comes
    from the real widgets (Auto stays the default radio).
    """

    def exec(self) -> QDialog.DialogCode:  # noqa: N802 (Qt API)
        self.findChild(QSpinBox, "frameCountSpin").setValue(3)
        self.accept()
        return self.result()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScreenshotManagerDialog:

    def test_construction_lists_screenshots_with_origins(self, qt_app) -> None:
        rec = _make_recording(
            screenshots=["C:/shots/abertura.jpg", "C:/shots/rosto.png"],
            manual={"C:/shots/rosto.png"},
        )

        dialog = ScreenshotManagerDialog(rec)

        assert _name_labels(dialog) == ["abertura.jpg", "rosto.png"]
        assert _origin_labels(dialog) == ["Auto", "Manual"]

    def test_add_image_appends_as_manual(self, qt_app) -> None:
        rec = _make_recording(screenshots=["C:/shots/abertura.jpg"])
        dialog = ScreenshotManagerDialog(rec)
        add_btn = dialog.findChild(QPushButton, "addImageButton")
        assert add_btn is not None

        with patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "QFileDialog.getOpenFileName",
            return_value=("C:/shots/nova.webp", ""),
        ):
            add_btn.click()

        assert dialog._paths == ["C:/shots/abertura.jpg", "C:/shots/nova.webp"]
        assert "C:/shots/nova.webp" in dialog._manual
        assert _origin_labels(dialog) == ["Auto", "Manual"]

        # One row per path: re-picking a listed file is ignored.
        with patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "QFileDialog.getOpenFileName",
            return_value=("C:/shots/nova.webp", ""),
        ):
            add_btn.click()
        assert dialog._paths == ["C:/shots/abertura.jpg", "C:/shots/nova.webp"]

    def test_remove_deletes_row_and_manual_flag(self, qt_app) -> None:
        rec = _make_recording(
            screenshots=["C:/shots/a.jpg", "C:/shots/b.jpg"],
            manual={"C:/shots/b.jpg"},
        )
        dialog = ScreenshotManagerDialog(rec)

        _row_button(dialog, 1, "removeButton").click()

        assert dialog._paths == ["C:/shots/a.jpg"]
        assert dialog._manual == set()
        assert _name_labels(dialog) == ["a.jpg"]
        assert _origin_labels(dialog) == ["Auto"]

    def test_move_up_and_down_reorder(self, qt_app) -> None:
        rec = _make_recording(
            screenshots=[
                "C:/shots/a.jpg",
                "C:/shots/b.jpg",
                "C:/shots/c.jpg",
            ],
        )
        dialog = ScreenshotManagerDialog(rec)

        _row_button(dialog, 1, "upButton").click()
        assert dialog._paths == [
            "C:/shots/b.jpg",
            "C:/shots/a.jpg",
            "C:/shots/c.jpg",
        ]

        _row_button(dialog, 2, "upButton").click()
        assert dialog._paths == [
            "C:/shots/b.jpg",
            "C:/shots/c.jpg",
            "C:/shots/a.jpg",
        ]

        _row_button(dialog, 0, "downButton").click()
        assert dialog._paths == [
            "C:/shots/c.jpg",
            "C:/shots/b.jpg",
            "C:/shots/a.jpg",
        ]

        # Clamped at both edges: no wrap-around.
        _row_button(dialog, 0, "upButton").click()
        assert dialog._paths == [
            "C:/shots/c.jpg",
            "C:/shots/b.jpg",
            "C:/shots/a.jpg",
        ]
        _row_button(dialog, 2, "downButton").click()
        assert dialog._paths == [
            "C:/shots/c.jpg",
            "C:/shots/b.jpg",
            "C:/shots/a.jpg",
        ]

    def test_cancel_returns_none_on_both_accessors(self, qt_app) -> None:
        rec = _make_recording(
            screenshots=["C:/shots/a.jpg"],
            manual={"C:/shots/a.jpg"},
        )
        dialog = ScreenshotManagerDialog(rec)

        dialog.reject()

        assert dialog.result_screenshot_paths is None
        assert dialog.result_manual_paths is None

    def test_ok_returns_edited_values(self, qt_app) -> None:
        rec = _make_recording(
            screenshots=["C:/shots/a.jpg", "C:/shots/b.jpg"],
            manual={"C:/shots/b.jpg"},
        )
        dialog = ScreenshotManagerDialog(rec)
        with patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "QFileDialog.getOpenFileName",
            return_value=("C:/shots/nova.png", ""),
        ):
            dialog.findChild(QPushButton, "addImageButton").click()
        _row_button(dialog, 0, "removeButton").click()

        dialog.accept()

        assert dialog.result_screenshot_paths == [
            "C:/shots/b.jpg",
            "C:/shots/nova.png",
        ]
        assert dialog.result_manual_paths == {
            "C:/shots/b.jpg",
            "C:/shots/nova.png",
        }

    def test_input_recording_is_not_mutated(self, qt_app) -> None:
        rec = _make_recording(
            screenshots=["C:/shots/a.jpg", "C:/shots/b.jpg"],
            manual={"C:/shots/b.jpg"},
        )
        dialog = ScreenshotManagerDialog(rec)
        with patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "QFileDialog.getOpenFileName",
            return_value=("C:/shots/nova.png", ""),
        ):
            dialog.findChild(QPushButton, "addImageButton").click()
        _row_button(dialog, 0, "removeButton").click()
        _row_button(dialog, 0, "downButton").click()

        dialog.accept()
        assert dialog.result_screenshot_paths is not None

        assert rec.screenshot_paths == ["C:/shots/a.jpg", "C:/shots/b.jpg"]
        assert rec.manual_paths == {"C:/shots/b.jpg"}


# ---------------------------------------------------------------------------
# Bug C regression — rows built from REAL image files
#
# The Add/Remove/Move slots always called _rebuild(); what broke was
# _make_row itself: scaledToHeight was passed an AspectRatioMode instead
# of a TransformationMode, raising TypeError only when the pixmap loads
# (a real file).  The exception escaped the Qt slot and the list never
# refreshed.  Fake paths (null pixmap branch) hid the bug, so every test
# below writes an actual image to disk.
# ---------------------------------------------------------------------------

# Minimal valid 1x1 RGB PNG.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc000000301010018dd8db00000000049"
    "454e44ae426082"
)


def _write_png(directory: Path, name: str) -> str:
    path = directory / name
    path.write_bytes(_TINY_PNG)
    return str(path)


def _laid_out_paths(dialog: ScreenshotManagerDialog) -> list[str]:
    """Row paths in actual on-screen layout order (not internal state)."""
    paths: list[str] = []
    for index in range(dialog._rows_box.count()):
        widget = dialog._rows_box.itemAt(index).widget()
        if widget is not None:
            paths.append(widget.path)
    return paths


class TestRealImageRows:
    """Bug C — the list refreshes for rows whose images actually load."""

    def test_one_real_screenshot_shows_one_row(self, qt_app, tmp_path) -> None:
        shot = _write_png(tmp_path, "a.png")
        dialog = ScreenshotManagerDialog(_make_recording(screenshots=[shot]))

        assert len(dialog._row_widgets) == 1
        assert _name_labels(dialog) == ["a.png"]
        assert _laid_out_paths(dialog) == [shot]

    def test_add_image_refreshes_list_to_two_rows(self, qt_app, tmp_path) -> None:
        first = _write_png(tmp_path, "a.png")
        second = _write_png(tmp_path, "b.png")
        dialog = ScreenshotManagerDialog(_make_recording(screenshots=[first]))

        with patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "QFileDialog.getOpenFileName",
            return_value=(second, ""),
        ):
            dialog.findChild(QPushButton, "addImageButton").click()

        assert len(dialog._row_widgets) == 2
        assert _name_labels(dialog) == ["a.png", "b.png"]
        assert _laid_out_paths(dialog) == [first, second]
        assert dialog._paths == [first, second]

    def test_remove_last_row_leaves_one_row_and_path_gone(
        self, qt_app, tmp_path,
    ) -> None:
        first = _write_png(tmp_path, "a.png")
        second = _write_png(tmp_path, "b.png")
        dialog = ScreenshotManagerDialog(
            _make_recording(screenshots=[first, second], manual={second}),
        )

        _row_button(dialog, 1, "removeButton").click()

        assert len(dialog._row_widgets) == 1
        assert _name_labels(dialog) == ["a.png"]
        assert _laid_out_paths(dialog) == [first]
        assert dialog._paths == [first]
        assert dialog._manual == set()

    def test_move_up_down_change_widget_row_order(self, qt_app, tmp_path) -> None:
        a = _write_png(tmp_path, "a.png")
        b = _write_png(tmp_path, "b.png")
        c = _write_png(tmp_path, "c.png")
        dialog = ScreenshotManagerDialog(_make_recording(screenshots=[a, b, c]))

        _row_button(dialog, 1, "upButton").click()
        assert _laid_out_paths(dialog) == [b, a, c]

        _row_button(dialog, 0, "downButton").click()
        assert _laid_out_paths(dialog) == [a, b, c]


# ---------------------------------------------------------------------------
# Two-part sections — Part 1 / Part 2 (L15.6.3a)
# ---------------------------------------------------------------------------

_PART_A = "C:/media/show-part1.mkv"
_PART_B = "C:/media/show-part2.mkv"
_SHOTS = [
    "C:/shots/a.jpg",
    "C:/shots/b.jpg",
    "C:/shots/c.jpg",
    "C:/shots/d.jpg",
]


def _make_two_part_recording(
    screenshots: list[str] | None = None,
    split: int | None = None,
) -> ExportRecording:
    return ExportRecording(
        source_path=_PART_A,
        parts=(_PART_A, _PART_B),
        screenshot_paths=list(screenshots or []),
        manual_paths=set(),
        screenshot_split=split,
    )


def _section_headers(dialog: ScreenshotManagerDialog) -> list[str]:
    """The "Part 1"/"Part 2" header labels, in layout order."""
    headers: list[str] = []
    for index in range(dialog._rows_box.count()):
        widget = dialog._rows_box.itemAt(index).widget()
        if (
            isinstance(widget, QLabel)
            and widget.objectName().startswith("partHeader")
        ):
            headers.append(widget.text())
    return headers


def _section_add_button(
    dialog: ScreenshotManagerDialog, part: int,
) -> QPushButton:
    """The live ``Add Image (Part N)…`` button (layout walk, never stale)."""
    for index in range(dialog._rows_box.count()):
        widget = dialog._rows_box.itemAt(index).widget()
        if (
            isinstance(widget, QPushButton)
            and widget.objectName() == f"partAddButton{part}"
        ):
            return widget
    raise AssertionError(f"no Add Image (Part {part}) button in the layout")


def _section_paths(
    dialog: ScreenshotManagerDialog,
) -> tuple[list[str], list[str]]:
    """Row paths per section, walked from the layout (headers delimit)."""
    sections: list[list[str]] = [[], []]
    current = -1
    for index in range(dialog._rows_box.count()):
        widget = dialog._rows_box.itemAt(index).widget()
        if widget is None:
            continue
        if (
            isinstance(widget, QLabel)
            and widget.objectName().startswith("partHeader")
        ):
            current = int(widget.objectName()[-1]) - 1
            continue
        path = getattr(widget, "path", None)
        if path is not None and 0 <= current < 2:
            sections[current].append(path)
    return sections[0], sections[1]


class TestTwoPartSections:
    """Part 1 / Part 2 sections for 2-part recordings (L15.6.3a)."""

    def test_single_part_has_no_sections(self, qt_app) -> None:
        """One part → no headers, no section buttons, flat list as before."""
        dialog = ScreenshotManagerDialog(
            _make_recording(screenshots=list(_SHOTS)),
        )

        assert _section_headers(dialog) == []
        assert dialog._split is None
        assert _laid_out_paths(dialog) == _SHOTS
        assert dialog.findChild(QPushButton, "partAddButton1") is None
        assert dialog.findChild(QPushButton, "partAddButton2") is None
        assert dialog.findChild(QPushButton, "addImageButton") is not None

        dialog.accept()
        assert dialog.result_screenshot_split is None

    def test_two_part_sections_computed_by_distribute_rule(
        self, qt_app,
    ) -> None:
        """split=None → 4 paths split 2/2 by the distribute rule."""
        dialog = ScreenshotManagerDialog(
            _make_two_part_recording(screenshots=list(_SHOTS)),
        )

        assert _section_headers(dialog) == ["Part 1", "Part 2"]
        part1, part2 = _section_paths(dialog)
        assert part1 == _SHOTS[:2]
        assert part2 == _SHOTS[2:]
        assert dialog._split == 2
        assert _section_add_button(dialog, 1).text() == "Add Image (Part 1)…"
        assert _section_add_button(dialog, 2).text() == "Add Image (Part 2)…"

    def test_two_part_explicit_split_respected(self, qt_app) -> None:
        """split=1 → Part 1 gets one row, Part 2 the rest."""
        dialog = ScreenshotManagerDialog(
            _make_two_part_recording(screenshots=list(_SHOTS), split=1),
        )

        part1, part2 = _section_paths(dialog)
        assert part1 == _SHOTS[:1]
        assert part2 == _SHOTS[1:]
        assert dialog._split == 1

    def test_add_image_part1_appends_to_part1_and_bumps_split(
        self, qt_app,
    ) -> None:
        """Add on Part 1 lands at the section's end; split grows by 1."""
        dialog = ScreenshotManagerDialog(
            _make_two_part_recording(screenshots=list(_SHOTS)),
        )

        with patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "QFileDialog.getOpenFileName",
            return_value=("C:/shots/new.png", ""),
        ):
            _section_add_button(dialog, 1).click()

        part1, part2 = _section_paths(dialog)
        assert part1 == [_SHOTS[0], _SHOTS[1], "C:/shots/new.png"]
        assert part2 == _SHOTS[2:]
        assert dialog._split == 3
        assert dialog._paths == [
            _SHOTS[0], _SHOTS[1], "C:/shots/new.png", _SHOTS[2], _SHOTS[3],
        ]
        assert "C:/shots/new.png" in dialog._manual

    def test_add_image_part2_keeps_split(self, qt_app) -> None:
        """Add on Part 2 lands at the list's end; split unchanged."""
        dialog = ScreenshotManagerDialog(
            _make_two_part_recording(screenshots=list(_SHOTS)),
        )

        with patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "QFileDialog.getOpenFileName",
            return_value=("C:/shots/new.png", ""),
        ):
            _section_add_button(dialog, 2).click()

        part1, part2 = _section_paths(dialog)
        assert part1 == _SHOTS[:2]
        assert part2 == [_SHOTS[2], _SHOTS[3], "C:/shots/new.png"]
        assert dialog._split == 2

    def test_remove_part1_row_decrements_split(self, qt_app) -> None:
        """Removing part 1's row shrinks part 1; part 2 unchanged."""
        dialog = ScreenshotManagerDialog(
            _make_two_part_recording(screenshots=list(_SHOTS)),
        )

        _row_button(dialog, 0, "removeButton").click()

        assert dialog._split == 1
        part1, part2 = _section_paths(dialog)
        assert part1 == [_SHOTS[1]]
        assert part2 == _SHOTS[2:]

    def test_remove_part2_row_keeps_split(self, qt_app) -> None:
        """Removing part 2's row leaves the boundary where it was."""
        dialog = ScreenshotManagerDialog(
            _make_two_part_recording(screenshots=list(_SHOTS)),
        )

        _row_button(dialog, 2, "removeButton").click()

        assert dialog._split == 2
        part1, part2 = _section_paths(dialog)
        assert part1 == _SHOTS[:2]
        assert part2 == [_SHOTS[3]]

    def test_move_down_from_part1_cannot_cross_boundary(self, qt_app) -> None:
        """Move Down on part 1's last row is a no-op; within-part works."""
        dialog = ScreenshotManagerDialog(
            _make_two_part_recording(screenshots=list(_SHOTS)),
        )
        before = list(dialog._paths)

        _row_button(dialog, 1, "downButton").click()
        assert dialog._paths == before
        assert dialog._split == 2

        _row_button(dialog, 0, "downButton").click()
        assert dialog._paths == [_SHOTS[1], _SHOTS[0], _SHOTS[2], _SHOTS[3]]
        assert dialog._split == 2

    def test_move_up_from_part2_cannot_cross_boundary(self, qt_app) -> None:
        """Move Up on part 2's first row is a no-op; within-part works."""
        dialog = ScreenshotManagerDialog(
            _make_two_part_recording(screenshots=list(_SHOTS)),
        )
        before = list(dialog._paths)

        _row_button(dialog, 2, "upButton").click()
        assert dialog._paths == before
        assert dialog._split == 2

        _row_button(dialog, 3, "upButton").click()
        assert dialog._paths == [_SHOTS[0], _SHOTS[1], _SHOTS[3], _SHOTS[2]]

    def test_result_screenshot_split_reflects_edits(self, qt_app) -> None:
        """Accepted → the edited integer; rejected → None."""
        dialog = ScreenshotManagerDialog(
            _make_two_part_recording(screenshots=list(_SHOTS)),
        )
        _row_button(dialog, 0, "removeButton").click()  # part 1: 2 → 1
        with patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "QFileDialog.getOpenFileName",
            return_value=("C:/shots/new.png", ""),
        ):
            _section_add_button(dialog, 2).click()  # part 2 append

        dialog.accept()

        assert dialog.result_screenshot_split == 1
        assert dialog.result_screenshot_paths == [
            _SHOTS[1], _SHOTS[2], _SHOTS[3], "C:/shots/new.png",
        ]

        rejected = ScreenshotManagerDialog(
            _make_two_part_recording(screenshots=list(_SHOTS)),
        )
        rejected.reject()
        assert rejected.result_screenshot_split is None


# ---------------------------------------------------------------------------
# Generation flow (L15.9.2)
# ---------------------------------------------------------------------------


class TestGenerateScreenshotsFlow:
    """FFmpeg generation runs on a worker thread from the manager dialog."""

    def test_generate_screenshots_button_exists(self, qt_app) -> None:
        """The manager offers the generation entry point."""
        dialog = ScreenshotManagerDialog(_make_recording())

        button = dialog.findChild(QPushButton, "generateScreenshotsButton")

        assert button is not None
        assert button.text() == "Generate Screenshots\u2026"

    def test_accept_modal_starts_worker_with_three_timestamps(
        self, qt_app,
    ) -> None:
        """Count=3 in Auto mode starts a worker with a 3-point plan."""
        dialog = ScreenshotManagerDialog(_make_recording(duration=3600.0))

        with patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "GenerateScreenshotsDialog",
            _AcceptingGenerateDialog,
        ), patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "load_settings",
            return_value=AppSettings(ffmpeg_path="C:/tools/ffmpeg.exe"),
        ), patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "ScreenshotWorker",
            _RecordingScreenshotWorker,
        ), patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog.QThread",
            _SyncQThread,
        ):
            dialog.findChild(
                QPushButton, "generateScreenshotsButton",
            ).click()

        assert isinstance(dialog._screenshot_thread, _SyncQThread)
        assert dialog._screenshot_thread.start_called
        worker = dialog._screenshot_worker
        assert worker.ran is True
        assert worker.video_path == "C:/media/concert.mkv"
        assert worker.ffmpeg_path == "C:/tools/ffmpeg.exe"
        assert len(worker.plan.timestamps) == 3

    def test_finished_appends_generated_paths_only(self, qt_app) -> None:
        """finished(paths) adds Auto rows; manual_paths stays untouched."""
        rec = _make_recording(
            screenshots=["C:/shots/abertura.jpg"],
            manual={"C:/shots/abertura.jpg"},
            duration=3600.0,
        )
        dialog = ScreenshotManagerDialog(rec)
        generated = [
            "C:/media/concert.screenshot.01.jpg",
            "C:/media/concert.screenshot.02.jpg",
        ]

        with patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "GenerateScreenshotsDialog",
            _AcceptingGenerateDialog,
        ), patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "load_settings",
            return_value=AppSettings(ffmpeg_path=""),
        ), patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog."
            "ScreenshotWorker",
            _RecordingScreenshotWorker,
        ), patch(
            "bootleg_catalog.ui.dialogs.screenshot_manager_dialog.QThread",
            _SyncQThread,
        ):
            dialog.findChild(
                QPushButton, "generateScreenshotsButton",
            ).click()
            dialog._screenshot_worker.finished.emit(generated)
            dialog.accept()

        assert dialog.result_screenshot_paths == [
            "C:/shots/abertura.jpg",
            *generated,
        ]
        assert dialog.result_manual_paths == {"C:/shots/abertura.jpg"}
        # Status cleared and the buttons unlocked again.
        assert dialog._status_label.isHidden()
        assert dialog._buttons.isEnabled()
