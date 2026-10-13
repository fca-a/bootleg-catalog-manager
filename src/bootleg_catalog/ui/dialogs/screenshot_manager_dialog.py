"""Screenshot manager dialog — manual screenshot attachment.

Lists a recording's screenshots with an Auto/Manual origin label and
lets the user add images (always manual), remove rows, and reorder them.
Two-part recordings render a ``Part 1`` / ``Part 2`` section per part:
rows live in one flat list split at the screenshot split,
every mutation keeps the split consistent, and moves never cross the
boundary.  The dialog never mutates the input recording: the caller
reads ``result_screenshot_paths`` / ``result_manual_paths`` /
``result_screenshot_split`` after ``Accepted``; all three return
``None`` otherwise.

Manual screenshots are embedded into the ``.bootleg`` on save; auto
sidecar screenshots stay referenced (see session_store).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bootleg_catalog.core.multi_part import distribute_screenshot_counts
from bootleg_catalog.models.export_recording import ExportRecording

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
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        layout.addWidget(scroll)

        add_btn = QPushButton("Add Image\u2026", self)
        add_btn.setObjectName("addImageButton")
        add_btn.clicked.connect(self._on_add_image)
        if self._split is not None:
            # Sections carry their own add buttons; a part-less "Add
            # Image..." would be ambiguous here.
            add_btn.hide()
        layout.addWidget(add_btn)

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
