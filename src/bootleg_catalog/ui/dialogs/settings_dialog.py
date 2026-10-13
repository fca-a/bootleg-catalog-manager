"""Settings dialog for configuring FFmpeg/FFprobe paths and suggestion lists."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from bootleg_catalog.core.settings_service import AppSettings


class SettingsDialog(QDialog):
    """Modal dialog for editing global application settings.

    Use :attr:`settings` to retrieve the (possibly modified) settings
    object after the dialog is accepted.
    """

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        self._settings = AppSettings(
            ffmpeg_path=settings.ffmpeg_path,
            ffprobe_path=settings.ffprobe_path,
            type_suggestions=list(settings.type_suggestions),
            source_suggestions=list(settings.source_suggestions),
            quality_suggestions=list(settings.quality_suggestions),
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # --- FFmpeg ---
        layout.addWidget(QLabel("FFmpeg executable path (optional):"))
        ffmpeg_row = QHBoxLayout()
        self._ffmpeg_edit = QLineEdit(self._settings.ffmpeg_path)
        self._ffmpeg_edit.setPlaceholderText("Leave empty to use system PATH")
        ffmpeg_row.addWidget(self._ffmpeg_edit, stretch=1)
        ffmpeg_browse = QPushButton("Browse...")
        ffmpeg_browse.clicked.connect(self._browse_ffmpeg)
        ffmpeg_row.addWidget(ffmpeg_browse)
        layout.addLayout(ffmpeg_row)

        # --- FFprobe ---
        layout.addWidget(QLabel("FFprobe executable path (optional):"))
        ffprobe_row = QHBoxLayout()
        self._ffprobe_edit = QLineEdit(self._settings.ffprobe_path)
        self._ffprobe_edit.setPlaceholderText("Leave empty to use system PATH")
        ffprobe_row.addWidget(self._ffprobe_edit, stretch=1)
        ffprobe_browse = QPushButton("Browse...")
        ffprobe_browse.clicked.connect(self._browse_ffprobe)
        ffprobe_row.addWidget(ffprobe_browse)
        layout.addLayout(ffprobe_row)

        # --- Suggestion lists ---
        layout.addWidget(
            QLabel(
                "Suggestion lists (autocomplete only - free text is always allowed):",
            ),
        )
        self._type_list, self._type_edit = self._build_suggestion_section(
            layout, "Type suggestions:", self._settings.type_suggestions,
        )
        self._source_list, self._source_edit = self._build_suggestion_section(
            layout, "Source suggestions:", self._settings.source_suggestions,
        )
        self._quality_list, self._quality_edit = self._build_suggestion_section(
            layout, "Quality suggestions:", self._settings.quality_suggestions,
        )

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveButton")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def settings(self) -> AppSettings:
        return self._settings

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select FFmpeg Executable", "",
            "Executables (*.exe);;All files (*.*)",
        )
        if path:
            self._ffmpeg_edit.setText(path)

    def _browse_ffprobe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select FFprobe Executable", "",
            "Executables (*.exe);;All files (*.*)",
        )
        if path:
            self._ffprobe_edit.setText(path)

    def _on_save(self) -> None:
        ffmpeg = self._ffmpeg_edit.text().strip()
        ffprobe = self._ffprobe_edit.text().strip()

        # Validate non-empty paths
        if ffmpeg and not Path(ffmpeg).is_file():
            QMessageBox.warning(
                self,
                "Invalid Path",
                f"FFmpeg executable not found:\n{ffmpeg}",
            )
            return
        if ffprobe and not Path(ffprobe).is_file():
            QMessageBox.warning(
                self,
                "Invalid Path",
                f"FFprobe executable not found:\n{ffprobe}",
            )
            return

        self._settings.ffmpeg_path = ffmpeg
        self._settings.ffprobe_path = ffprobe
        self._settings.type_suggestions = self._read_suggestions(self._type_list)
        self._settings.source_suggestions = self._read_suggestions(self._source_list)
        self._settings.quality_suggestions = self._read_suggestions(self._quality_list)
        self.accept()

    # ------------------------------------------------------------------
    # Suggestion sections
    # ------------------------------------------------------------------

    def _build_suggestion_section(
        self,
        layout: QVBoxLayout,
        title: str,
        values: list[str],
    ) -> tuple[QListWidget, QLineEdit]:
        layout.addWidget(QLabel(title))
        list_widget = QListWidget()
        list_widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        list_widget.setMinimumHeight(72)
        list_widget.addItems(values)
        layout.addWidget(list_widget)

        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setPlaceholderText("New value...")
        edit.returnPressed.connect(
            lambda: self._add_suggestion(list_widget, edit),
        )
        row.addWidget(edit, stretch=1)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(
            lambda: self._add_suggestion(list_widget, edit),
        )
        row.addWidget(add_btn)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(lambda: self._remove_suggestion(list_widget))
        row.addWidget(remove_btn)
        layout.addLayout(row)
        return list_widget, edit

    @staticmethod
    def _add_suggestion(list_widget: QListWidget, edit: QLineEdit) -> None:
        text = edit.text().strip()
        if not text:
            return
        list_widget.addItem(text)
        edit.clear()

    @staticmethod
    def _remove_suggestion(list_widget: QListWidget) -> None:
        row = list_widget.currentRow()
        if row >= 0:
            list_widget.takeItem(row)

    @staticmethod
    def _read_suggestions(list_widget: QListWidget) -> list[str]:
        return [
            list_widget.item(i).text()
            for i in range(list_widget.count())
        ]
