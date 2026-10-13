"""Tests for AddRecordingDialog, EditExportRecordingDialog, and the ScreenshotManagerDialog rip hint (L15.8.1)."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from bootleg_catalog.app.application import BootlegCatalogApplication
from bootleg_catalog.core.settings_service import AppSettings
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.ui.dialogs.export_recording_dialog import (
    AddRecordingDialog,
    EditExportRecordingDialog,
    _ExportRecordingFormDialog,
)
from bootleg_catalog.ui.dialogs.screenshot_manager_dialog import (
    ScreenshotManagerDialog,
)

from PySide6.QtWidgets import QLabel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def qt_app() -> BootlegCatalogApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = BootlegCatalogApplication.instance()
    if app is None:
        app = BootlegCatalogApplication([])
    return app


def _make_recording(**overrides) -> ExportRecording:
    defaults = dict(
        source_path="/media/concert.mkv",
        extension=".mkv",
        file_size=2_500_000_000,
        duration=7421.0,
        container_format="matroska,webm",
        video_codec="h264",
        video_width=1920,
        video_height=1080,
        frame_rate=29.97,
        pixel_format="yuv420p",
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate=192000,
    )
    defaults.update(overrides)
    return ExportRecording(**defaults)


def _make_settings(**overrides) -> AppSettings:
    defaults = dict(
        source_suggestions=["Audience", "Blu-ray", "DVD"],
        type_suggestions=["Housecam", "Fancam", "Pro-shot"],
        quality_suggestions=["Excellent", "Good", "Fair"],
    )
    defaults.update(overrides)
    return AppSettings(**defaults)


# ---------------------------------------------------------------------------
# AddRecordingDialog — technical fields
# ---------------------------------------------------------------------------

class TestAddRecordingTechnicalFields:
    def test_loads_technical_fields(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, _make_settings())

        assert dlg._tech_labels["Extension"].text() == ".mkv"
        assert "h264" in dlg._tech_labels["Video Codec"].text()
        assert "aac" in dlg._tech_labels["Audio Codec"].text()
        assert "1920" in dlg._tech_labels["Resolution"].text()
        assert "1080" in dlg._tech_labels["Resolution"].text()

    def test_technical_labels_are_readonly(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, _make_settings())

        from PySide6.QtCore import Qt
        for lbl in dlg._tech_labels.values():
            flags = lbl.textInteractionFlags()
            assert flags & Qt.TextInteractionFlag.TextSelectableByMouse


# ---------------------------------------------------------------------------
# AddRecordingDialog — archival defaults
# ---------------------------------------------------------------------------

class TestAddRecordingArchivalDefaults:
    def test_empty_archival_fields(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, _make_settings())

        assert dlg.title_edit.text() == ""
        assert dlg.year_edit.text() == ""
        assert dlg.venue_edit.text() == ""
        assert dlg.notes_edit.toPlainText() == ""
        assert dlg.description_edit.toPlainText() == ""

    def test_master_defaults_to_unknown(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, _make_settings())

        assert dlg.master_edit.currentText() == "Unknown"


# ---------------------------------------------------------------------------
# AddRecordingDialog — probe error
# ---------------------------------------------------------------------------

class TestAddRecordingProbeError:
    def test_probe_error_shows_warning(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(probe_error="FFprobe timed out")
        dlg = AddRecordingDialog(rec, _make_settings())

        # The warning label should contain the error text
        # Find it by checking all labels in the dialog
        from PySide6.QtWidgets import QLabel
        found = False
        for child in dlg.findChildren(QLabel):
            if "FFprobe timed out" in child.text():
                found = True
                break
        assert found, "Warning label with probe_error text not found"

    def test_probe_error_allows_archival_editing(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(probe_error="FFprobe failed")
        dlg = AddRecordingDialog(rec, _make_settings())

        # The archival fields should still be editable
        dlg.title_edit.setText("My Title")
        dlg.year_edit.setText("2024")

        dlg._on_save()

        result = dlg.result_recording
        assert result is not None
        assert result.title == "My Title"
        assert result.year == 2024

    def test_no_probe_error_no_warning(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(probe_error=None)
        dlg = AddRecordingDialog(rec, _make_settings())

        from PySide6.QtWidgets import QLabel
        for child in dlg.findChildren(QLabel):
            assert "FFprobe" not in child.text()


# ---------------------------------------------------------------------------
# EditExportRecordingDialog — pre-population
# ---------------------------------------------------------------------------

class TestEditExportRecordingPrePopulate:
    def test_pre_populates_archival_fields(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(
            title="Oakland '77",
            year=1977,
            venue="Oakland Coliseum",
            source="Audience",
            type="Housecam",
            quality="Excellent",
            master="Yes",
            notes="Great show",
        )
        dlg = EditExportRecordingDialog(rec, _make_settings())

        assert dlg.title_edit.text() == "Oakland '77"
        assert dlg.year_edit.text() == "1977"
        assert dlg.venue_edit.text() == "Oakland Coliseum"
        assert dlg.source_edit.currentText() == "Audience"
        assert dlg.type_edit.currentText() == "Housecam"
        assert dlg.quality_edit.currentText() == "Excellent"
        assert dlg.master_edit.currentText() == "Yes"
        assert dlg.notes_edit.toPlainText() == "Great show"

    def test_preserves_technical_fields(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = EditExportRecordingDialog(rec, _make_settings())

        assert dlg._tech_labels["Extension"].text() == ".mkv"
        assert "h264" in dlg._tech_labels["Video Codec"].text()


# ---------------------------------------------------------------------------
# Editable combos — free text
# ---------------------------------------------------------------------------

class TestEditableCombos:
    def test_custom_source_not_in_list(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(source="My Custom Source")
        dlg = EditExportRecordingDialog(rec, _make_settings())

        assert dlg.source_edit.currentText() == "My Custom Source"

    def test_custom_type_not_in_list(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(type="Custom Type")
        dlg = EditExportRecordingDialog(rec, _make_settings())

        assert dlg.type_edit.currentText() == "Custom Type"

    def test_type_combo_from_settings(self, qt_app: BootlegCatalogApplication) -> None:
        settings = _make_settings(type_suggestions=["X", "Y", "Z"])
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, settings)

        # Should have empty + X, Y, Z
        assert dlg.type_edit.count() == 4
        assert dlg.type_edit.itemText(1) == "X"
        assert dlg.type_edit.itemText(2) == "Y"
        assert dlg.type_edit.itemText(3) == "Z"

    def test_source_combo_from_settings(self, qt_app: BootlegCatalogApplication) -> None:
        settings = _make_settings(source_suggestions=["A", "B"])
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, settings)

        assert dlg.source_edit.count() == 3  # empty + A + B

    def test_quality_combo_from_settings(self, qt_app: BootlegCatalogApplication) -> None:
        settings = _make_settings(quality_suggestions=["Q1", "Q2"])
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, settings)

        assert dlg.quality_edit.count() == 3  # empty + Q1 + Q2


# ---------------------------------------------------------------------------
# Master combo
# ---------------------------------------------------------------------------

class TestMasterCombo:
    def test_master_populated_from_master_values(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, _make_settings())

        items = [dlg.master_edit.itemText(i) for i in range(dlg.master_edit.count())]
        # L15.8.1: one suggestion only; the combo stays editable.
        assert items == ["Unknown"]
        assert dlg.master_edit.isEditable()

    def test_master_combo_single_item_in_edit_dialog(
        self, qt_app: BootlegCatalogApplication,
    ) -> None:
        rec = _make_recording()
        dlg = EditExportRecordingDialog(rec, _make_settings())

        items = [dlg.master_edit.itemText(i) for i in range(dlg.master_edit.count())]
        assert items == ["Unknown"]

    def test_master_custom_value(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(master="Custom Master")
        dlg = EditExportRecordingDialog(rec, _make_settings())

        assert dlg.master_edit.currentText() == "Custom Master"

    def test_master_accepts_free_text_typing(
        self, qt_app: BootlegCatalogApplication,
    ) -> None:
        """Master stays free text: the user can type any value."""
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, _make_settings())

        dlg.master_edit.setEditText("Bootleg dub")
        dlg._on_save()

        assert dlg.master_edit.currentText() == "Bootleg dub"
        result = dlg.result_recording
        assert result is not None
        assert result.master == "Bootleg dub"


# ---------------------------------------------------------------------------
# Save behavior
# ---------------------------------------------------------------------------

class TestSaveBehavior:
    def test_save_returns_new_recording(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.title_edit.setText("New Title")
        dlg.year_edit.setText("2024")

        dlg._on_save()

        result = dlg.result_recording
        assert result is not None
        assert result is not rec
        assert result.title == "New Title"
        assert result.year == 2024
        assert result.source_path == rec.source_path

    def test_original_not_modified(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(title="Original")
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.title_edit.setText("Changed")

        dlg._on_save()

        assert rec.title == "Original"

    def test_cancel_returns_none(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(title="Original")
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.title_edit.setText("Changed")
        dlg.reject()

        assert dlg.result_recording is None

    def test_whitespace_becomes_none(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(title="Something")
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.title_edit.setText("   ")

        dlg._on_save()

        result = dlg.result_recording
        assert result is not None
        assert result.title is None

    def test_empty_year_saved_as_none(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(year=1999)
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.year_edit.setText("")

        dlg._on_save()

        result = dlg.result_recording
        assert result is not None
        assert result.year is None

    def test_whitespace_only_fields_become_none(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(venue="Fillmore")
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.venue_edit.setText("   \t  ")

        dlg._on_save()

        result = dlg.result_recording
        assert result is not None
        assert result.venue is None

    def test_notes_multiline(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.notes_edit.setPlainText("Line 1\nLine 2\nLine 3")

        dlg._on_save()

        result = dlg.result_recording
        assert result is not None
        assert result.notes == "Line 1\nLine 2\nLine 3"

    def test_description_multiline(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.description_edit.setPlainText("First paragraph.\n\nSecond paragraph.")

        dlg._on_save()

        result = dlg.result_recording
        assert result is not None
        assert result.description == "First paragraph.\n\nSecond paragraph."


# ---------------------------------------------------------------------------
# Year validation
# ---------------------------------------------------------------------------

class TestYearValidation:
    def test_invalid_year_stays_open(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.year_edit.setText("not a year")

        from unittest.mock import patch
        with patch(
            "bootleg_catalog.ui.dialogs.export_recording_dialog.QMessageBox.warning"
        ) as mock_warn:
            dlg._on_save()
            mock_warn.assert_called_once()
            assert "not a valid year" in mock_warn.call_args[0][2]

        assert dlg.result_recording is None

    def test_valid_year_accepted(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.year_edit.setText("2024")

        dlg._on_save()

        result = dlg.result_recording
        assert result is not None
        assert result.year == 2024

    def test_negative_year_accepted(self, qt_app: BootlegCatalogApplication) -> None:
        """Negative years are valid integers — the dialog accepts them."""
        rec = _make_recording()
        dlg = EditExportRecordingDialog(rec, _make_settings())
        dlg.year_edit.setText("-500")

        dlg._on_save()

        result = dlg.result_recording
        assert result is not None
        assert result.year == -500


# ---------------------------------------------------------------------------
# Result accessor before exec
# ---------------------------------------------------------------------------

class TestResultAccessorBeforeExec:
    def test_result_none_before_exec(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, _make_settings())
        assert dlg.result_recording is None

    def test_result_none_before_exec_edit(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording()
        dlg = EditExportRecordingDialog(rec, _make_settings())
        assert dlg.result_recording is None


# ---------------------------------------------------------------------------
# Settings flow — suggestion lists from AppSettings
# ---------------------------------------------------------------------------

class TestSettingsFlow:
    def test_suggestions_from_settings(self, qt_app: BootlegCatalogApplication) -> None:
        settings = _make_settings(
            source_suggestions=["S1", "S2", "S3"],
            type_suggestions=["T1", "T2"],
            quality_suggestions=["Q1"],
        )
        rec = _make_recording()
        dlg = AddRecordingDialog(rec, settings)

        # Source: empty + S1 + S2 + S3
        assert dlg.source_edit.count() == 4
        assert dlg.source_edit.itemText(1) == "S1"

        # Type: empty + T1 + T2
        assert dlg.type_edit.count() == 3
        assert dlg.type_edit.itemText(1) == "T1"

        # Quality: empty + Q1
        assert dlg.quality_edit.count() == 2
        assert dlg.quality_edit.itemText(1) == "Q1"

    def test_different_settings_different_combos(self, qt_app: BootlegCatalogApplication) -> None:
        s1 = _make_settings(source_suggestions=["A"])
        s2 = _make_settings(source_suggestions=["X", "Y", "Z"])
        rec = _make_recording()

        dlg1 = AddRecordingDialog(rec, s1)
        dlg2 = AddRecordingDialog(rec, s2)

        assert dlg1.source_edit.count() != dlg2.source_edit.count()
        assert dlg1.source_edit.count() == 2   # empty + A
        assert dlg2.source_edit.count() == 4   # empty + X + Y + Z


# ---------------------------------------------------------------------------
# ScreenshotManagerDialog — rip hint (L15.8.1)
# ---------------------------------------------------------------------------

class TestScreenshotManagerRipHint:
    def test_rip_recording_shows_hint(self, qt_app: BootlegCatalogApplication) -> None:
        rec = _make_recording(
            source_path="C:/rips/concert", extension="VIDEO_TS", is_rip=True,
        )

        dialog = ScreenshotManagerDialog(rec)

        hint = dialog.findChild(QLabel, "ripHintLabel")
        assert hint is not None
        assert "not auto-analyzed" in hint.text()

    def test_non_rip_dvd_shows_no_hint(
        self, qt_app: BootlegCatalogApplication,
    ) -> None:
        """A user-chosen "DVD" extension alone must not mark a rip."""
        rec = _make_recording(extension="DVD", is_rip=False)

        dialog = ScreenshotManagerDialog(rec)

        assert dialog.findChild(QLabel, "ripHintLabel") is None
