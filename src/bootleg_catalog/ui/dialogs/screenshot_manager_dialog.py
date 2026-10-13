"""Screenshot manager dialog — manual attachment and FFmpeg generation.

Lists a recording's screenshots with an Auto/Manual origin label and
lets the user add images (always manual), remove rows, and reorder them.
Two-part recordings render a ``Part 1`` / ``Part 2`` section per part:
rows live in one flat list split at the screenshot split,
every mutation keeps the split consistent, and moves never cross the
boundary.  The dialog never mutates the input recording: the caller
reads ``result_screenshot_paths`` / ``result_manual_paths`` /
``result_screenshot_split`` after ``Accepted``; all three return
``None`` otherwise.

``Generate Screenshots…`` runs FFmpeg on a worker thread to extract
frames beside the source video; generated paths join the internal
list as Auto rows (never ``manual_paths``) and leave the dialog
through the same accessors.

Manual screenshots are embedded into the ``.bootleg`` on save; auto
sidecar screenshots stay referenced (see session_store).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bootleg_catalog.core.multi_part import distribute_screenshot_counts
from bootleg_catalog.core.screenshot_generator import (
    ScreenshotPlan,
    auto_timestamps,
)
from bootleg_catalog.core.settings_service import load_settings
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.ui.workers.screenshot_worker import ScreenshotWorker

_IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.webp)"
_THUMBNAIL_HEIGHT_PX = 32
_THUMBNAIL_WIDTH_PX = 64


class ScreenshotManagerDialog(QDialog):
    """Edit a recording's screenshot list without mutating it.

    Rows show a thumbnail, the file name, and an ``Auto``/``Manual``
    origin label, with per-row ``Remove`` / ``Move Up`` / ``Move Down``
    buttons.  ``Add Image…`` picks files via a dialog filtered to
    images; chosen files are appended as manual.  One row per path:
    re-adding an existing path is ignored.

    ``Generate Screenshots…`` opens :class:`GenerateScreenshotsDialog`,
    then runs a :class:`~bootleg_catalog.ui.workers.screenshot_worker.
    ScreenshotWorker` on a :class:`~PySide6.QtCore.QThread` while the
    dialog's buttons are locked and a status line reports progress.
    Generated paths are appended as Auto rows on ``finished``.

    For two-part recordings the rows are grouped into ``Part 1`` and
    ``Part 2`` sections delimited by ``screenshot_split`` (defaults to
    the distribute rule when the recording has none), each section with
    its own ``Add Image (Part N)…`` button.  Adds append to the end of
    their section, removes shrink part 1 when they take its row, and
    moves stay within a section.
    """

    def __init__(
        self,
        recording: ExportRecording,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Screenshots")
        # Copies: the input recording is never mutated.
        self._paths = list(recording.screenshot_paths)
        self._manual = set(recording.manual_paths)
        self._row_widgets: list[QWidget] = []
        # Generation state: source/duration are read once (never the
        # live recording), and the thread/worker refs mirror the
        # ExportPreviewWindow probe pair.
        self._source_path = recording.source_path
        self._duration = recording.duration
        self._generating = False
        self._screenshot_thread: QThread | None = None
        self._screenshot_worker: ScreenshotWorker | None = None
        # Effective split: None for single-part (one section, no
        # headers); always an int for two-part recordings.
        self._split: int | None = None
        if len(recording.parts) == 2:
            if recording.screenshot_split is not None:
                self._split = recording.screenshot_split
            else:
                self._split = distribute_screenshot_counts(
                    len(self._paths), 2,
                )[0]

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(f"Screenshots for {Path(recording.source_path).name}", self),
        )

        if recording.is_rip:
            # Rips are folders, not media files: nothing is auto-discovered
            # next to them.  Keyed on the rip flag, not on the extension
            # label.
            hint = QLabel(
                "Disk-based rips are not auto-analyzed; "
                "add screenshots manually.",
                self,
            )
            hint.setObjectName("ripHintLabel")
            hint.setWordWrap(True)
            layout.addWidget(hint)

        self._rows_box = QVBoxLayout()
        container = QWidget(self)
        container.setLayout(self._rows_box)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(container)
        layout.addWidget(self._scroll)

        self._add_btn = QPushButton("Add Image\u2026", self)
        self._add_btn.setObjectName("addImageButton")
        self._add_btn.clicked.connect(self._on_add_image)
        if self._split is not None:
            # Sections carry their own add buttons; a part-less "Add
            # Image..." would be ambiguous here.
            self._add_btn.hide()
        layout.addWidget(self._add_btn)

        self._generate_btn = QPushButton("Generate Screenshots\u2026", self)
        self._generate_btn.setObjectName("generateScreenshotsButton")
        self._generate_btn.clicked.connect(self._on_generate_screenshots)
        layout.addWidget(self._generate_btn)

        # Progress line shown only while the worker runs.
        self._status_label = QLabel("", self)
        self._status_label.setObjectName("generationStatusLabel")
        self._status_label.hide()
        layout.addWidget(self._status_label)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._rebuild()

    # -- Result accessors ----------------------------------------------------

    @property
    def result_screenshot_paths(self) -> list[str] | None:
        """The edited path list after ``Accepted``; ``None`` otherwise."""
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        return list(self._paths)

    @property
    def result_manual_paths(self) -> set[str] | None:
        """The edited manual subset after ``Accepted``; ``None`` otherwise."""
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        return set(self._manual)

    @property
    def result_screenshot_split(self) -> int | None:
        """The edited part-1 block size after ``Accepted``.

        ``None`` on reject, and for single-part recordings (no
        sections, no split).
        """
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        return self._split

    # -- Row rendering -------------------------------------------------------

    def _rebuild(self) -> None:
        """Recreate the row widgets from the current paths."""
        while self._rows_box.count():
            item = self._rows_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._row_widgets = []

        split = self._split
        if split is None:
            # Single section: the pre-multi-part layout, headers off.
            for index, path in enumerate(self._paths):
                row = self._make_row(index, path)
                self._row_widgets.append(row)
                self._rows_box.addWidget(row)
            self._rows_box.addStretch()
            return

        # Two sections, part-1 block first, each with its own add button.
        for part in (1, 2):
            header = QLabel(f"Part {part}", self)
            header.setObjectName(f"partHeader{part}")
            self._rows_box.addWidget(header)
            start = 0 if part == 1 else split
            end = split if part == 1 else len(self._paths)
            for index in range(start, end):
                row = self._make_row(index, self._paths[index])
                self._row_widgets.append(row)
                self._rows_box.addWidget(row)
            self._rows_box.addWidget(self._make_section_add_button(part))
        self._rows_box.addStretch()

    def _make_section_add_button(self, part: int) -> QPushButton:
        """Build the ``Add Image (Part N)…`` button for one section.

        *part* is captured as this builder's own parameter (a fresh
        binding per call), and the slot is a zero-argument lambda so
        Qt's ``clicked(bool)`` never leaks into it.
        """
        button = QPushButton(f"Add Image (Part {part})…", self)
        button.setObjectName(f"partAddButton{part}")
        button.clicked.connect(lambda: self._on_add_image_part(part))
        return button

    def _make_row(self, index: int, path: str) -> QWidget:
        """Build one row widget for *path* at *index*.

        Buttons capture the index at build time; every mutation
        rebuilds the rows, so the captured indexes stay fresh.
        """
        row = QWidget(self)
        row.path = path  # introspection hook (used by the tests)
        inner = QHBoxLayout(row)
        inner.setContentsMargins(4, 2, 4, 2)

        thumb = QLabel(row)
        pixmap = QPixmap(path)
        if pixmap.isNull():
            thumb.setText("-")
        else:
            # NB: scaledToHeight takes a TransformationMode, not an
            # AspectRatioMode (passing the latter raises TypeError and
            # would kill the whole rebuild for real image files).
            thumb.setPixmap(
                pixmap.scaledToHeight(
                    _THUMBNAIL_HEIGHT_PX,
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )
        thumb.setFixedWidth(_THUMBNAIL_WIDTH_PX)
        inner.addWidget(thumb)

        name = QLabel(Path(path).name, row)
        name.setObjectName("nameLabel")
        inner.addWidget(name, stretch=1)

        origin = QLabel("Manual" if path in self._manual else "Auto", row)
        origin.setObjectName("originLabel")
        inner.addWidget(origin)

        remove_btn = QPushButton("Remove", row)
        remove_btn.setObjectName("removeButton")
        remove_btn.clicked.connect(lambda: self._on_remove(index))
        inner.addWidget(remove_btn)

        up_btn = QPushButton("Move Up", row)
        up_btn.setObjectName("upButton")
        up_btn.clicked.connect(lambda: self._on_move(index, -1))
        inner.addWidget(up_btn)

        down_btn = QPushButton("Move Down", row)
        down_btn.setObjectName("downButton")
        down_btn.clicked.connect(lambda: self._on_move(index, 1))
        inner.addWidget(down_btn)

        return row

    # -- Mutations (on the internal copies only) -----------------------------

    def _on_add_image(self) -> None:
        """Pick an image file and append it as a manual screenshot.

        Flat append: for a two-part list that lands at the end of part 2.
        """
        path = self._pick_image()
        if path is None:
            return
        self._paths.append(path)
        self._manual.add(path)
        self._rebuild()

    def _on_add_image_part(self, part: int) -> None:
        """Append a new manual image at the end of *part*'s section."""
        path = self._pick_image()
        if path is None:
            return
        if part == 1 and self._split is not None:
            # End of part 1's block: insert at the boundary, then grow it.
            self._paths.insert(self._split, path)
            self._split += 1
        else:
            self._paths.append(path)
        self._manual.add(path)
        self._rebuild()

    def _pick_image(self) -> str | None:
        """Open the image picker; ``None`` on cancel or a listed path."""
        path, _ = QFileDialog.getOpenFileName(self, "Add Image", "", _IMAGE_FILTER)
        if not path:
            return None
        if path in self._paths:
            return None  # one row per path
        return path

    def _on_remove(self, index: int) -> None:
        """Remove the row at *index*; part 1 shrinks if it owned the row."""
        path = self._paths.pop(index)
        self._manual.discard(path)
        if self._split is not None and index < self._split:
            self._split -= 1
        self._rebuild()

    def _on_move(self, index: int, delta: int) -> None:
        """Swap the row with its neighbour (never across a part boundary)."""
        target = index + delta
        if not (0 <= target < len(self._paths)):
            return
        if self._split is not None and (
            (index < self._split) != (target < self._split)
        ):
            return  # a part boundary blocks the move
        self._paths[index], self._paths[target] = (
            self._paths[target],
            self._paths[index],
        )
        self._rebuild()

    # -- FFmpeg generation ---------------------------------------------------

    def _on_generate_screenshots(self) -> None:
        """Plan a generation run and start it on a worker thread."""
        if not self._duration:
            QMessageBox.information(
                self,
                "Generate Screenshots",
                "This recording has no known duration.",
            )
            return

        modal = GenerateScreenshotsDialog(self._duration, parent=self)
        if modal.exec() != QDialog.DialogCode.Accepted:
            return

        if modal.use_auto_timestamps:
            plan = ScreenshotPlan(
                auto_timestamps(self._duration, modal.frame_count),
            )
        else:
            plan = ScreenshotPlan(tuple(sorted(modal.manual_timestamps)))

        self._start_generation(plan, load_settings().ffmpeg_path or "ffmpeg",
                               modal.overwrite)

    def _start_generation(
        self, plan: ScreenshotPlan, ffmpeg_path: str, overwrite: bool,
    ) -> None:
        """Wire a ScreenshotWorker onto a fresh QThread and start it.

        The wiring mirrors ProbeWorker in ExportPreviewWindow:
        ``started`` → ``run``, ``finished``/``failed`` → ``quit`` and
        ``worker.deleteLater``, ``thread.finished`` →
        ``thread.deleteLater``.  This dialog only owns the refs, never
        the thread's event loop.
        """
        self._set_generating(True)
        total = len(plan.timestamps)
        self._status_label.setText(f"Generating 0 of {total}\u2026")
        self._status_label.show()

        thread = QThread()
        worker = ScreenshotWorker(
            self._source_path, plan, ffmpeg_path, overwrite,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished_paths)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._screenshot_thread = thread
        self._screenshot_worker = worker
        thread.start()

    def _on_progress(self, current: int, total: int) -> None:
        """Update the status line after each completed timestamp."""
        self._status_label.setText(f"Generating {current} of {total}\u2026")

    def _on_finished_paths(self, paths: list) -> None:
        """Append generated paths as Auto rows, then unlock the dialog."""
        for path in paths:
            # One row per path: a regenerated sidecar already listed
            # is not appended twice.
            if path not in self._paths:
                self._paths.append(path)
        # Deliberately NOT self._manual: generated sidecars are Auto
        # screenshots; manual_paths is only for hand-attached images.
        self._status_label.clear()
        self._status_label.hide()
        self._set_generating(False)
        self._rebuild()

    def _on_failed(self, message: str) -> None:
        """Unlock and report the worker's error."""
        self._status_label.clear()
        self._status_label.hide()
        self._set_generating(False)
        QMessageBox.warning(self, "Screenshot Error", message)

    def _set_generating(self, running: bool) -> None:
        """Lock every dialog control while the worker runs."""
        self._generating = running
        self._buttons.setEnabled(not running)
        self._add_btn.setEnabled(not running)
        self._generate_btn.setEnabled(not running)
        self._scroll.setEnabled(not running)


class GenerateScreenshotsDialog(QDialog):
    """Choose how many frames to extract and at which timestamps.

    ``How many`` (1–20, default 5) drives Auto mode, which spaces the
    frames across the runtime via
    :func:`~bootleg_catalog.core.screenshot_generator.auto_timestamps`.
    Manual mode lists one timestamp spin box per row (0.0 … duration,
    step 0.5), added with ``+ Add`` (up to 20 rows) and removed with a
    per-row ``− Remove``.  The caller reads the property accessors
    after ``Accepted``; nothing is executed here.
    """

    def __init__(
        self,
        recording_duration: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Generate Screenshots")
        self.setModal(True)
        self._duration = float(recording_duration)
        self._manual_values: list[float] = []

        layout = QVBoxLayout(self)

        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("How many:", self))
        self._count_spin = QSpinBox(self)
        self._count_spin.setObjectName("frameCountSpin")
        self._count_spin.setRange(1, 20)
        self._count_spin.setValue(5)
        count_row.addWidget(self._count_spin)
        count_row.addStretch()
        layout.addLayout(count_row)

        self._auto_radio = QRadioButton("Auto timestamps", self)
        self._auto_radio.setObjectName("autoTimestampsRadio")
        self._auto_radio.setChecked(True)
        self._auto_radio.toggled.connect(self._on_mode_changed)
        layout.addWidget(self._auto_radio)

        self._manual_radio = QRadioButton("Manual timestamps", self)
        self._manual_radio.setObjectName("manualTimestampsRadio")
        layout.addWidget(self._manual_radio)

        self._manual_box = QGroupBox("Manual timestamps", self)
        self._manual_box.setObjectName("manualTimestampsBox")
        manual_layout = QVBoxLayout(self._manual_box)
        self._manual_rows = QVBoxLayout()
        manual_layout.addLayout(self._manual_rows)
        self._add_row_btn = QPushButton("+ Add", self._manual_box)
        self._add_row_btn.setObjectName("addTimestampButton")
        self._add_row_btn.clicked.connect(self._on_add_timestamp_row)
        manual_layout.addWidget(self._add_row_btn)
        layout.addWidget(self._manual_box)

        self._overwrite_check = QCheckBox(
            "Overwrite existing screenshots", self,
        )
        self._overwrite_check.setObjectName("overwriteCheckbox")
        layout.addWidget(self._overwrite_check)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        # Auto is the default mode: the manual rows start hidden.
        self._on_mode_changed(self._auto_radio.isChecked())

    # -- Result accessors (read by ScreenshotManagerDialog on accept) -------

    @property
    def frame_count(self) -> int:
        """The requested frame count (Auto mode)."""
        return self._count_spin.value()

    @property
    def use_auto_timestamps(self) -> bool:
        """True when the Auto radio is selected."""
        return self._auto_radio.isChecked()

    @property
    def manual_timestamps(self) -> list[float]:
        """The manual rows' current values."""
        return list(self._manual_values)

    @property
    def overwrite(self) -> bool:
        """Whether existing sidecars may be replaced."""
        return self._overwrite_check.isChecked()

    # -- Manual rows ---------------------------------------------------------

    def _on_mode_changed(self, auto: bool) -> None:
        """Show the manual rows only in Manual mode."""
        self._manual_box.setVisible(not auto)

    def _on_add_timestamp_row(self) -> None:
        """Append a manual row, up to 20."""
        if len(self._manual_values) >= 20:
            return
        self._manual_values.append(0.0)
        self._rebuild_manual_rows()

    def _on_remove_timestamp_row(self, index: int) -> None:
        """Drop the row at *index*."""
        if not (0 <= index < len(self._manual_values)):
            return
        self._manual_values.pop(index)
        self._rebuild_manual_rows()

    def _on_row_value_changed(self, index: int, value: float) -> None:
        """Keep the backing list in step with a spin box edit."""
        if 0 <= index < len(self._manual_values):
            self._manual_values[index] = value

    def _rebuild_manual_rows(self) -> None:
        """Recreate the row widgets from the backing value list."""
        while self._manual_rows.count():
            item = self._manual_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for index in range(len(self._manual_values)):
            row = QWidget(self._manual_box)
            inner = QHBoxLayout(row)
            inner.setContentsMargins(0, 0, 0, 0)

            spin = QDoubleSpinBox(row)
            spin.setObjectName("timestampSpin")
            spin.setRange(0.0, self._duration)
            spin.setSingleStep(0.5)
            spin.setValue(self._manual_values[index])
            # Connect after the initial setValue: the list already
            # holds the same value.
            spin.valueChanged.connect(
                lambda value, i=index: self._on_row_value_changed(i, value),
            )
            inner.addWidget(spin)

            remove_btn = QPushButton("\u2212 Remove", row)
            remove_btn.setObjectName("removeTimestampButton")
            remove_btn.clicked.connect(
                lambda i=index: self._on_remove_timestamp_row(i),
            )
            inner.addWidget(remove_btn)

            self._manual_rows.addWidget(row)
