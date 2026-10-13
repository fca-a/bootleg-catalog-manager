"""Search and filter bar for recordings."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from bootleg_catalog.core.filters import RecordingFilter


class SearchBar(QWidget):
    """A compact search bar with text input and filter dropdowns."""

    filter_changed = Signal(RecordingFilter)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Search field
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search recordings...")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(180)
        layout.addWidget(self._search_edit, stretch=2)

        # Year filter
        layout.addWidget(QLabel("Year:"))
        self._year_edit = QLineEdit()
        self._year_edit.setPlaceholderText("Any")
        self._year_edit.setMaximumWidth(60)
        layout.addWidget(self._year_edit)

        # Source filter
        layout.addWidget(QLabel("Source:"))
        self._source_combo = QComboBox()
        self._source_combo.addItem("")
        self._source_combo.setMinimumWidth(100)
        layout.addWidget(self._source_combo)

        # Quality filter
        layout.addWidget(QLabel("Quality:"))
        self._quality_combo = QComboBox()
        self._quality_combo.addItem("")
        self._quality_combo.setMinimumWidth(90)
        layout.addWidget(self._quality_combo)

        # Master filter
        layout.addWidget(QLabel("Master:"))
        self._master_combo = QComboBox()
        self._master_combo.addItem("")
        self._master_combo.setMinimumWidth(90)
        layout.addWidget(self._master_combo)

        # Missing filter
        layout.addWidget(QLabel("Missing:"))
        self._missing_combo = QComboBox()
        self._missing_combo.addItem("")
        self._missing_combo.addItem("Yes")
        self._missing_combo.addItem("No")
        self._missing_combo.setMinimumWidth(60)
        layout.addWidget(self._missing_combo)

        # Clear button
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setMaximumWidth(50)
        layout.addWidget(self._clear_btn)

        # Connect signals
        self._search_edit.textChanged.connect(self._on_filter_changed)
        self._year_edit.textChanged.connect(self._on_filter_changed)
        self._source_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._quality_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._master_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._missing_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._clear_btn.clicked.connect(self.clear_all)

    def set_filter_values(self, column: str, values: list[str]) -> None:
        """Populate a filter combo with values derived from the database."""
        combo = self._get_combo(column)
        if combo is None:
            return
        combo.blockSignals(True)
        current = combo.currentText()
        combo.clear()
        combo.addItem("")
        combo.addItems(values)
        # Restore previous selection if still present
        index = combo.findText(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def clear_all(self) -> None:
        """Reset all filter controls to their default state.

        Signals are blocked during the reset so that only one
        ``filter_changed`` signal is emitted at the end.
        """
        self.blockSignals(True)
        self._search_edit.clear()
        self._year_edit.clear()
        self._source_combo.setCurrentIndex(0)
        self._quality_combo.setCurrentIndex(0)
        self._master_combo.setCurrentIndex(0)
        self._missing_combo.setCurrentIndex(0)
        self.blockSignals(False)
        self.filter_changed.emit(self.current_filter())

    def current_filter(self) -> RecordingFilter:
        """Build a RecordingFilter from the current UI state."""
        year_text = self._year_edit.text().strip()
        year = int(year_text) if year_text.isdigit() else None

        source = self._source_combo.currentText().strip() or None
        quality = self._quality_combo.currentText().strip() or None
        master = self._master_combo.currentText().strip() or None

        missing_text = self._missing_combo.currentText().strip()
        if missing_text == "Yes":
            missing = True
        elif missing_text == "No":
            missing = False
        else:
            missing = None

        return RecordingFilter(
            query=self._search_edit.text().strip(),
            year=year,
            source=source,
            quality=quality,
            master=master,
            missing=missing,
        )

    def _get_combo(self, column: str) -> QComboBox | None:
        mapping = {
            "source": self._source_combo,
            "quality": self._quality_combo,
            "master": self._master_combo,
        }
        return mapping.get(column)

    def _on_filter_changed(self) -> None:
        self.filter_changed.emit(self.current_filter())
