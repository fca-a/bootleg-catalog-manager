"""Session chooser dialog — pick a recent session or browse for one."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bootleg_catalog.core.recent_sessions import RecentSessions


class SessionChooserDialog(QDialog):
    """Choose a recent ``.bootleg`` session, or browse for one.

    Purely presentational: the dialog never records the chosen path in
    the recent-sessions store — the caller (app startup) does that.

    Rows whose file no longer exists on disk are greyed out with
    ``" (missing)"`` appended but stay selectable; the caller surfaces
    the load error.
    """

    def __init__(self, recents: RecentSessions, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open Session")
        self.setMinimumSize(480, 300)
        self._recents = recents
        self._selected_path: str | None = None

        layout = QVBoxLayout(self)

        self._list = QListWidget()
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._list)

        self._browse_btn = QPushButton("Browse for .bootleg\u2026")
        self._browse_btn.clicked.connect(self._on_browse)
        layout.addWidget(self._browse_btn)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._open_btn = QPushButton("Open")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._on_open)
        button_row.addWidget(self._open_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(self._cancel_btn)
        layout.addLayout(button_row)

        self._populate()

    @property
    def selected_path(self) -> str | None:
        """Absolute path of the chosen .bootleg; None if cancelled."""
        return self._selected_path

    def _populate(self) -> None:
        entries = self._recents.list()
        if not entries:
            hint = QListWidgetItem("No recent sessions.")
            hint.setFlags(Qt.ItemFlag.NoItemFlags)  # non-selectable hint row
            self._list.addItem(hint)
            return
        for entry in entries:
            exists = Path(entry.path).is_file()
            label = (
                entry.display_name
                if exists
                else f"{entry.display_name} (missing)"
            )
            # Two lines: primary = display name, secondary = path.
            item = QListWidgetItem(f"{label}\n{entry.path}")
            item.setData(Qt.ItemDataRole.UserRole, entry.path)
            if not exists:
                item.setForeground(QBrush(QColor(160, 160, 160)))
            self._list.addItem(item)

    def _on_selection_changed(self) -> None:
        self._open_btn.setEnabled(bool(self._list.selectedItems()))

    def _on_open(self) -> None:
        items = self._list.selectedItems()
        if not items:
            return
        path = items[0].data(Qt.ItemDataRole.UserRole)
        if not path:
            return  # the empty-state hint row
        self._selected_path = str(path)
        self.accept()

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        self._list.setCurrentItem(item)
        self._on_open()

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Session", "", "Bootleg sessions (*.bootleg)",
        )
        if path:
            self._selected_path = path
            self.accept()
