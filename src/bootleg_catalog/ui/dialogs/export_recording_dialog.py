"""Dialogs for adding and editing ExportRecording entries.

Provides two modal dialogs for the Export Preview workflow:

- :class:`AddRecordingDialog` — fills archival fields on a newly-probed
  recording before committing to the session.
- :class:`EditExportRecordingDialog` — edits archival fields on an
  existing recording in the session.

Both dialogs operate on :class:`ExportRecording` and
:class:`AppSettings` — never on :class:`Recording`, Catalog, or SQLite.

Design: a shared base class ``_ExportRecordingFormDialog`` builds the
form and handles validation.  The two public classes are thin wrappers
that adjust titles and labels.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bootleg_catalog.core.settings_service import AppSettings, MASTER_VALUES
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.utils.paths import format_duration, format_file_size


# ---------------------------------------------------------------------------
# Shared base dialog
# ---------------------------------------------------------------------------

class _ExportRecordingFormDialog(QDialog):
    """Shared form for editing ExportRecording archival fields.

    Subclasses override :meth:`_dialog_title` and :meth:`_dialog_label`.
    """

    def __init__(
        self,
        recording: ExportRecording,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self._dialog_title())
        self.setMinimumWidth(500)
        self.setMinimumHeight(600)
        self.setModal(True)

        self._recording = recording
        self._settings = settings
        self._result_recording: ExportRecording | None = None

        layout = QVBoxLayout(self)

        # Scroll area for the form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        main_layout = QVBoxLayout(container)

        # --- Header ---
        header = QLabel(f"<b>{self._dialog_label()}</b>")
        header.setTextFormat(Qt.TextFormat.RichText)
        main_layout.addWidget(header)

        # --- Probe error warning ---
        if recording.probe_error:
            warning = QLabel(
                f"<span style='color: #B8860B;'>⚠ FFprobe could not analyze "
                f"this file: {recording.probe_error}</span>"
            )
            warning.setTextFormat(Qt.TextFormat.RichText)
            warning.setWordWrap(True)
            main_layout.addWidget(warning)

        main_layout.addSpacing(4)

        # --- Technical section (read-only) ---
        tech_group = QGroupBox("Technical Information")
        tech_layout = QFormLayout(tech_group)
        tech_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._tech_labels: dict[str, QLabel] = {}
        tech_fields = [
            ("Extension", recording.extension or ""),
            ("File Size", format_file_size(recording.file_size)),
            ("Duration", format_duration(recording.duration)),
            ("Container", recording.container_format or ""),
            ("Video Codec", recording.video_codec or ""),
            ("Resolution", self._format_resolution(recording)),
            ("Frame Rate", f"{recording.frame_rate:.2f} fps" if recording.frame_rate else ""),
            ("Pixel Format", recording.pixel_format or ""),
            ("Audio Codec", recording.audio_codec or ""),
            ("Audio Channels", str(recording.audio_channels) if recording.audio_channels is not None else ""),
            ("Sample Rate", f"{recording.audio_sample_rate} Hz" if recording.audio_sample_rate else ""),
            ("Audio Bitrate", self._format_bitrate(recording)),
        ]

        for label_text, value in tech_fields:
            lbl = QLabel(value if value else "—")
            lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            self._tech_labels[label_text] = lbl
            tech_layout.addRow(label_text, lbl)

        main_layout.addWidget(tech_group)
        main_layout.addSpacing(4)

        # --- Archival section (editable) ---
        archival_group = QGroupBox("Archival Fields")
        archival_layout = QFormLayout(archival_group)
        archival_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        rec = recording

        # Text fields
        self.title_edit = QLineEdit("" if rec.title is None else rec.title)
        self.year_edit = QLineEdit("" if rec.year is None else str(rec.year))
        self.date_edit = QLineEdit("" if rec.date is None else rec.date)
        self.venue_edit = QLineEdit("" if rec.venue is None else rec.venue)
        self.location_edit = QLineEdit("" if rec.location is None else rec.location)
        self.country_edit = QLineEdit("" if rec.country is None else rec.country)
        self.tour_edit = QLineEdit("" if rec.tour is None else rec.tour)
        self.show_edit = QLineEdit("" if rec.show is None else rec.show)
        self.generation_edit = QLineEdit("" if rec.generation is None else rec.generation)
        self.audio_source_edit = QLineEdit("" if rec.audio_source is None else rec.audio_source)
        self.video_source_edit = QLineEdit("" if rec.video_source is None else rec.video_source)

        # Source — editable combo from settings
        self.source_edit = QComboBox()
        self.source_edit.setEditable(True)
        self.source_edit.addItem("")
        self.source_edit.addItems(list(settings.source_suggestions))
        self._set_combo(self.source_edit, rec.source or "")

        # Type — editable combo from settings
        self.type_edit = QComboBox()
        self.type_edit.setEditable(True)
        self.type_edit.addItem("")
        self.type_edit.addItems(list(settings.type_suggestions))
        self._set_combo(self.type_edit, rec.type or "")

        # Quality — editable combo from settings
        self.quality_edit = QComboBox()
        self.quality_edit.setEditable(True)
        self.quality_edit.addItem("")
        self.quality_edit.addItems(list(settings.quality_suggestions))
        self._set_combo(self.quality_edit, rec.quality or "")

        # Master — combo from MASTER_VALUES
        self.master_edit = QComboBox()
        self.master_edit.setEditable(True)
        self.master_edit.addItems(list(MASTER_VALUES))
        self.master_edit.setCurrentText(rec.master or "Unknown")

        # Notes / Description — multi-line
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(100)
        self.notes_edit.setPlainText("" if rec.notes is None else rec.notes)

        self.description_edit = QPlainTextEdit()
        self.description_edit.setMaximumHeight(100)
        self.description_edit.setPlainText("" if rec.description is None else rec.description)

        # Add rows
        archival_layout.addRow("Title", self.title_edit)
        archival_layout.addRow("Year", self.year_edit)
        archival_layout.addRow("Date", self.date_edit)
        archival_layout.addRow("Venue", self.venue_edit)
        archival_layout.addRow("Location", self.location_edit)
        archival_layout.addRow("Country", self.country_edit)
        archival_layout.addRow("Tour", self.tour_edit)
        archival_layout.addRow("Show", self.show_edit)
        archival_layout.addRow("Source", self.source_edit)
        archival_layout.addRow("Type", self.type_edit)
        archival_layout.addRow("Generation", self.generation_edit)
        archival_layout.addRow("Quality", self.quality_edit)
        archival_layout.addRow("Audio Source", self.audio_source_edit)
        archival_layout.addRow("Video Source", self.video_source_edit)
        archival_layout.addRow("Master", self.master_edit)
        archival_layout.addRow("Notes", self.notes_edit)
        archival_layout.addRow("Description", self.description_edit)

        main_layout.addWidget(archival_group)

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # --- Buttons ---
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._save_btn = QPushButton("Save")
        self._save_btn.setDefault(True)
        self._cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(self._save_btn)
        btn_layout.addWidget(self._cancel_btn)
        layout.addLayout(btn_layout)

        # Connections
        self._save_btn.clicked.connect(self._on_save)
        self._cancel_btn.clicked.connect(self.reject)

    # -- Public API ----------------------------------------------------------

    @property
    def result_recording(self) -> ExportRecording | None:
        """The updated ExportRecording after Accept, or None if cancelled."""
        return self._result_recording

    # -- Subclass hooks ------------------------------------------------------

    def _dialog_title(self) -> str:
        return "Export Recording"

    def _dialog_label(self) -> str:
        return "Recording"

    # -- Save ----------------------------------------------------------------

    def _on_save(self) -> None:
        """Validate and collect fields into a new ExportRecording."""
        year_text = self.year_edit.text().strip()
        if year_text:
            try:
                year = int(year_text)
            except ValueError:
                QMessageBox.warning(
                    self,
                    "Invalid Year",
                    f'"{year_text}" is not a valid year. '
                    f"Enter a whole number or leave empty.",
                )
                return
        else:
            year = None

        self._result_recording = replace(
            self._recording,
            title=self.title_edit.text().strip() or None,
            year=year,
            date=self.date_edit.text().strip() or None,
            venue=self.venue_edit.text().strip() or None,
            location=self.location_edit.text().strip() or None,
            country=self.country_edit.text().strip() or None,
            tour=self.tour_edit.text().strip() or None,
            show=self.show_edit.text().strip() or None,
            source=self.source_edit.currentText().strip() or None,
            type=self.type_edit.currentText().strip() or None,
            generation=self.generation_edit.text().strip() or None,
            quality=self.quality_edit.currentText().strip() or None,
            audio_source=self.audio_source_edit.text().strip() or None,
            video_source=self.video_source_edit.text().strip() or None,
            master=self.master_edit.currentText() or "Unknown",
            notes=self.notes_edit.toPlainText().strip() or None,
            description=self.description_edit.toPlainText().strip() or None,
        )
        self.accept()

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _set_combo(combo: QComboBox, text: str) -> None:
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(text)

    @staticmethod
    def _format_resolution(rec: ExportRecording) -> str:
        if rec.video_width and rec.video_height:
            return f"{rec.video_width}×{rec.video_height}"
        return ""

    @staticmethod
    def _format_bitrate(rec: ExportRecording) -> str:
        if rec.audio_bitrate is None:
            return ""
        if rec.audio_bitrate >= 1_000_000:
            return f"{rec.audio_bitrate / 1_000_000:.1f} Mbps"
        if rec.audio_bitrate >= 1_000:
            return f"{rec.audio_bitrate / 1_000:.0f} kbps"
        return f"{rec.audio_bitrate} bps"


# ---------------------------------------------------------------------------
# Public dialogs
# ---------------------------------------------------------------------------

class AddRecordingDialog(_ExportRecordingFormDialog):
    """Dialog for filling archival fields on a newly-probed recording.

    The recording is expected to have already been probed and added to the
    session.  This dialog lets the user fill in archival fields before
    the session is updated.
    """

    def _dialog_title(self) -> str:
        return "Add Recording — Fill Details"

    def _dialog_label(self) -> str:
        # Path.name works on Windows where source_path uses backslashes.
        return Path(self._recording.source_path).name


class EditExportRecordingDialog(_ExportRecordingFormDialog):
    """Dialog for editing archival fields on an existing session recording.

    Pre-populates all fields from the current ExportRecording values.
    """

    def _dialog_title(self) -> str:
        return "Edit Recording Details"

    def _dialog_label(self) -> str:
        return Path(self._recording.source_path).name
