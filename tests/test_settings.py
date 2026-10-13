"""Tests for settings service, settings dialog, and integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.app.application import BootlegCatalogApplication
from bootleg_catalog.core.settings_service import (
    DEFAULT_QUALITY_SUGGESTIONS,
    DEFAULT_SOURCE_SUGGESTIONS,
    DEFAULT_TYPE_SUGGESTIONS,
    AppSettings,
    _settings_path,
    load_settings,
    save_settings,
)


# ---------------------------------------------------------------------------
# AppSettings dataclass
# ---------------------------------------------------------------------------


class TestAppSettings:
    def test_defaults(self) -> None:
        s = AppSettings()
        assert s.ffmpeg_path == ""
        assert s.ffprobe_path == ""

    def test_to_dict(self) -> None:
        s = AppSettings(ffmpeg_path="/usr/bin/ffmpeg", ffprobe_path="/usr/bin/ffprobe")
        d = s.to_dict()
        assert d["ffmpeg_path"] == "/usr/bin/ffmpeg"
        assert d["ffprobe_path"] == "/usr/bin/ffprobe"
        assert "suggestion_lists" in d
        assert isinstance(d["suggestion_lists"]["type"], list)
        assert isinstance(d["suggestion_lists"]["source"], list)

    def test_from_dict(self) -> None:
        d = {"ffmpeg_path": "/a/ffmpeg", "ffprobe_path": "/b/ffprobe"}
        s = AppSettings.from_dict(d)
        assert s.ffmpeg_path == "/a/ffmpeg"
        assert s.ffprobe_path == "/b/ffprobe"

    def test_from_dict_missing_keys(self) -> None:
        s = AppSettings.from_dict({})
        assert s.ffmpeg_path == ""
        assert s.ffprobe_path == ""

    def test_from_dict_extra_keys_ignored(self) -> None:
        s = AppSettings.from_dict({"ffmpeg_path": "/x", "unknown": "val"})
        assert s.ffmpeg_path == "/x"
        assert s.ffprobe_path == ""


# ---------------------------------------------------------------------------
# Suggestion lists
# ---------------------------------------------------------------------------


class TestSuggestionLists:
    def test_new_instance_has_defaults(self) -> None:
        s = AppSettings()
        assert s.type_suggestions == [
            "Audience", "Broadcast", "Fancam", "Housecam",
            "Multicam", "Rehearsal", "Soundboard", "Pro-shot",
        ]
        assert s.source_suggestions == [
            "Audience", "Blu-ray", "CD", "DAT", "DVD",
            "FM Broadcast", "TV Broadcast", "VHS", "Vinyl", "Web",
        ]

    def test_defaults_are_not_shared_mutable(self) -> None:
        s1 = AppSettings()
        s2 = AppSettings()
        s1.type_suggestions.append("Custom")
        assert "Custom" not in s2.type_suggestions

    def test_to_dict_has_nested_suggestion_lists(self) -> None:
        s = AppSettings()
        d = s.to_dict()
        assert "suggestion_lists" in d
        assert "type" in d["suggestion_lists"]
        assert "source" in d["suggestion_lists"]
        assert isinstance(d["suggestion_lists"]["type"], list)
        assert isinstance(d["suggestion_lists"]["source"], list)

    def test_to_dict_suggestion_lists_are_copies(self) -> None:
        s = AppSettings()
        d = s.to_dict()
        d["suggestion_lists"]["type"].append("Modified")
        assert "Modified" not in s.type_suggestions

    def test_roundtrip_preserves_suggestions(self, tmp_path: Path) -> None:
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            original = AppSettings(
                type_suggestions=["A", "B", "C"],
                source_suggestions=["X", "Y"],
            )
            save_settings(original)
            loaded = load_settings()
        assert loaded.type_suggestions == ["A", "B", "C"]
        assert loaded.source_suggestions == ["X", "Y"]

    def test_from_dict_missing_suggestion_lists(self) -> None:
        """Pre-L4 JSON without suggestion_lists → defaults applied."""
        s = AppSettings.from_dict({"ffmpeg_path": "/f"})
        assert s.type_suggestions == list(DEFAULT_TYPE_SUGGESTIONS)
        assert s.source_suggestions == list(DEFAULT_SOURCE_SUGGESTIONS)

    def test_from_dict_type_present_source_missing(self) -> None:
        s = AppSettings.from_dict({
            "suggestion_lists": {"type": ["Custom"]},
        })
        assert s.type_suggestions == ["Custom"]
        assert s.source_suggestions == list(DEFAULT_SOURCE_SUGGESTIONS)

    def test_from_dict_source_present_type_missing(self) -> None:
        s = AppSettings.from_dict({
            "suggestion_lists": {"source": ["DVD"]},
        })
        assert s.type_suggestions == list(DEFAULT_TYPE_SUGGESTIONS)
        assert s.source_suggestions == ["DVD"]

    def test_from_dict_empty_type_list_respected(self) -> None:
        """Empty list [] is valid — means free-text only."""
        s = AppSettings.from_dict({
            "suggestion_lists": {"type": [], "source": []},
        })
        assert s.type_suggestions == []
        assert s.source_suggestions == []

    def test_from_dict_non_list_type_falls_back(self) -> None:
        """Non-list value for type → defaults."""
        s = AppSettings.from_dict({
            "suggestion_lists": {"type": "not a list"},
        })
        assert s.type_suggestions == list(DEFAULT_TYPE_SUGGESTIONS)

    def test_from_dict_non_string_entries_filtered(self) -> None:
        """Non-string entries in a list are dropped."""
        s = AppSettings.from_dict({
            "suggestion_lists": {"type": ["A", 123, None, "B"]},
        })
        assert s.type_suggestions == ["A", "B"]

    def test_from_dict_unknown_keys_ignored(self) -> None:
        s = AppSettings.from_dict({
            "suggestion_lists": {"type": ["X"], "unknown_key": "val"},
        })
        assert s.type_suggestions == ["X"]

    def test_legacy_json_loads_without_error(self) -> None:
        """Pre-L4 settings JSON loads and produces default suggestions."""
        legacy = {"ffmpeg_path": "/usr/bin/ffmpeg", "ffprobe_path": "/usr/bin/ffprobe"}
        s = AppSettings.from_dict(legacy)
        assert s.ffmpeg_path == "/usr/bin/ffmpeg"
        assert s.ffprobe_path == "/usr/bin/ffprobe"
        assert s.type_suggestions == list(DEFAULT_TYPE_SUGGESTIONS)
        assert s.source_suggestions == list(DEFAULT_SOURCE_SUGGESTIONS)

    def test_custom_suggestions_persist_and_reload(self, tmp_path: Path) -> None:
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            original = AppSettings(
                type_suggestions=["MyType1", "MyType2"],
                source_suggestions=["MySource1"],
            )
            save_settings(original)
            loaded = load_settings()
        assert loaded.type_suggestions == ["MyType1", "MyType2"]
        assert loaded.source_suggestions == ["MySource1"]

    def test_clear_all_suggestions_persists_empty(self, tmp_path: Path) -> None:
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            original = AppSettings(type_suggestions=[], source_suggestions=[])
            save_settings(original)
            loaded = load_settings()
        assert loaded.type_suggestions == []
        assert loaded.source_suggestions == []

    def test_json_shape_after_save(self, tmp_path: Path) -> None:
        """Verify the exact JSON shape written to disk."""
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            save_settings(AppSettings(
                ffmpeg_path="/f",
                ffprobe_path="/p",
                type_suggestions=["T"],
                source_suggestions=["S"],
            ))
            raw = json.loads((_settings_path()).read_text(encoding="utf-8"))
        assert raw["ffmpeg_path"] == "/f"
        assert raw["ffprobe_path"] == "/p"
        assert raw["suggestion_lists"]["type"] == ["T"]
        assert raw["suggestion_lists"]["source"] == ["S"]

    def test_different_order_not_equal(self) -> None:
        """Two AppSettings with different suggestion order are NOT equal."""
        s1 = AppSettings(type_suggestions=["A", "B"])
        s2 = AppSettings(type_suggestions=["B", "A"])
        assert s1 != s2


# ---------------------------------------------------------------------------
# Quality suggestion list
# ---------------------------------------------------------------------------


class TestQualitySuggestions:
    def test_new_instance_has_defaults(self) -> None:
        from bootleg_catalog.core.settings_service import QUALITY_VALUES
        s = AppSettings()
        assert s.quality_suggestions == list(QUALITY_VALUES)

    def test_defaults_are_not_shared_mutable(self) -> None:
        s1 = AppSettings()
        s2 = AppSettings()
        s1.quality_suggestions.append("Custom")
        assert "Custom" not in s2.quality_suggestions

    def test_to_dict_has_quality_in_suggestion_lists(self) -> None:
        s = AppSettings()
        d = s.to_dict()
        assert "quality" in d["suggestion_lists"]
        assert isinstance(d["suggestion_lists"]["quality"], list)

    def test_to_dict_does_not_have_master(self) -> None:
        s = AppSettings()
        d = s.to_dict()
        assert "master" not in d["suggestion_lists"]

    def test_roundtrip_preserves_quality(self, tmp_path: Path) -> None:
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            original = AppSettings(
                quality_suggestions=["Excellent", "Good", "Fair"],
            )
            save_settings(original)
            loaded = load_settings()
        assert loaded.quality_suggestions == ["Excellent", "Good", "Fair"]

    def test_from_dict_missing_suggestion_lists_quality_defaults(self) -> None:
        s = AppSettings.from_dict({"ffmpeg_path": "/f"})
        assert s.quality_suggestions == list(DEFAULT_QUALITY_SUGGESTIONS)

    def test_from_dict_empty_quality_list_respected(self) -> None:
        s = AppSettings.from_dict({
            "suggestion_lists": {"quality": []},
        })
        assert s.quality_suggestions == []

    def test_from_dict_non_list_quality_falls_back(self) -> None:
        s = AppSettings.from_dict({
            "suggestion_lists": {"quality": "oops"},
        })
        assert s.quality_suggestions == list(DEFAULT_QUALITY_SUGGESTIONS)

    def test_from_dict_non_string_entries_filtered(self) -> None:
        s = AppSettings.from_dict({
            "suggestion_lists": {"quality": ["A", 123, None, "B"]},
        })
        assert s.quality_suggestions == ["A", "B"]

    def test_from_dict_master_key_ignored(self) -> None:
        """A master key inside suggestion_lists is ignored."""
        s = AppSettings.from_dict({
            "suggestion_lists": {"master": ["Yes", "No"]},
        })
        assert not hasattr(s, "master_suggestions")
        assert s.quality_suggestions == list(DEFAULT_QUALITY_SUGGESTIONS)

    def test_default_quality_suggestions_equals_quality_values(self) -> None:
        from bootleg_catalog.core.settings_service import QUALITY_VALUES
        assert DEFAULT_QUALITY_SUGGESTIONS == list(QUALITY_VALUES)

    def test_quality_values_contains_letter_grades(self) -> None:
        from bootleg_catalog.core.settings_service import QUALITY_VALUES
        for grade in ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-"]:
            assert grade in QUALITY_VALUES

    def test_master_values_is_unknown_only(self) -> None:
        """Master suggestions are minimal; the field stays free text."""
        from bootleg_catalog.core.settings_service import MASTER_VALUES
        assert MASTER_VALUES == ("Unknown",)

    def test_default_master_suggestions_is_unknown_only(self) -> None:
        from bootleg_catalog.core.settings_service import (
            DEFAULT_MASTER_SUGGESTIONS,
        )
        assert DEFAULT_MASTER_SUGGESTIONS == ["Unknown"]

    def test_equality_same_values(self) -> None:
        s1 = AppSettings(type_suggestions=["A"], source_suggestions=["X"])
        s2 = AppSettings(type_suggestions=["A"], source_suggestions=["X"])
        assert s1 == s2

    def test_equality_different_quality(self) -> None:
        s1 = AppSettings(quality_suggestions=["A"])
        s2 = AppSettings(quality_suggestions=["B"])
        assert s1 != s2

    def test_equality_different_paths(self) -> None:
        s1 = AppSettings(ffmpeg_path="/a")
        s2 = AppSettings(ffmpeg_path="/b")
        assert s1 != s2

    def test_equality_not_app_settings(self) -> None:
        s = AppSettings()
        assert s != "not a settings"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestSettingsPersistence:
    def test_load_defaults_when_no_file(self, tmp_path: Path) -> None:
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            s = load_settings()
        assert s == AppSettings()

    def test_save_then_load(self, tmp_path: Path) -> None:
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            original = AppSettings(ffmpeg_path="/opt/ffmpeg", ffprobe_path="/opt/ffprobe")
            save_settings(original)
            loaded = load_settings()
        assert loaded == original

    def test_save_empty_values(self, tmp_path: Path) -> None:
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            save_settings(AppSettings())
            loaded = load_settings()
        assert loaded.ffmpeg_path == ""
        assert loaded.ffprobe_path == ""

    def test_independent_paths(self, tmp_path: Path) -> None:
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            save_settings(AppSettings(ffmpeg_path="/a/ffmpeg"))
            loaded = load_settings()
        assert loaded.ffmpeg_path == "/a/ffmpeg"
        assert loaded.ffprobe_path == ""

    def test_overwrite_previous(self, tmp_path: Path) -> None:
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            save_settings(AppSettings(ffmpeg_path="/old"))
            save_settings(AppSettings(ffmpeg_path="/new"))
            loaded = load_settings()
        assert loaded.ffmpeg_path == "/new"

    def test_load_corrupt_file_returns_defaults(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("NOT JSON {{{", encoding="utf-8")
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            s = load_settings()
        assert s == AppSettings()

    def test_load_non_dict_json_returns_defaults(self, tmp_path: Path) -> None:
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('["not", "a", "dict"]', encoding="utf-8")
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            s = load_settings()
        assert s == AppSettings()

    def test_settings_file_is_valid_json(self, tmp_path: Path) -> None:
        with patch("bootleg_catalog.core.settings_service.app_data_dir", return_value=tmp_path):
            save_settings(AppSettings(ffmpeg_path="/f", ffprobe_path="/p"))
            raw = json.loads((_settings_path()).read_text(encoding="utf-8"))
        assert raw["ffmpeg_path"] == "/f"
        assert raw["ffprobe_path"] == "/p"


# ---------------------------------------------------------------------------
# Settings dialog (widget tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def qt_app() -> BootlegCatalogApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = BootlegCatalogApplication.instance()
    if app is None:
        app = BootlegCatalogApplication([])
    return app


class TestSettingsDialog:
    def test_dialog_loads_current_values(self, qt_app: BootlegCatalogApplication) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        settings = AppSettings(ffmpeg_path="/my/ffmpeg", ffprobe_path="/my/ffprobe")
        dlg = SettingsDialog(settings)
        assert dlg._ffmpeg_edit.text() == "/my/ffmpeg"
        assert dlg._ffprobe_edit.text() == "/my/ffprobe"

    def test_dialog_loads_empty_values(self, qt_app: BootlegCatalogApplication) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(AppSettings())
        assert dlg._ffmpeg_edit.text() == ""
        assert dlg._ffprobe_edit.text() == ""

    def test_cancel_returns_rejected(self, qt_app: BootlegCatalogApplication) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        from PySide6.QtWidgets import QDialog
        dlg = SettingsDialog(AppSettings())
        dlg._ffmpeg_edit.setText("/new/path")
        dlg.reject()
        assert dlg.result() == QDialog.DialogCode.Rejected
        # Settings should NOT be updated
        assert dlg.settings.ffmpeg_path == ""

    def test_save_updates_settings(self, qt_app: BootlegCatalogApplication, tmp_path: Path) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        fake_ffmpeg = tmp_path / "ffmpeg"
        fake_ffmpeg.write_bytes(b"")
        fake_ffprobe = tmp_path / "ffprobe"
        fake_ffprobe.write_bytes(b"")

        dlg = SettingsDialog(AppSettings())
        dlg._ffmpeg_edit.setText(str(fake_ffmpeg))
        dlg._ffprobe_edit.setText(str(fake_ffprobe))
        with patch.object(dlg, "accept") as mock_accept:
            dlg._on_save()
            mock_accept.assert_called_once()

        assert dlg.settings.ffmpeg_path == str(fake_ffmpeg)
        assert dlg.settings.ffprobe_path == str(fake_ffprobe)

    def test_save_validates_ffmpeg_path(self, qt_app: BootlegCatalogApplication) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        with patch("bootleg_catalog.ui.dialogs.settings_dialog.QMessageBox") as mock_mb:
            dlg = SettingsDialog(AppSettings())
            dlg._ffmpeg_edit.setText("/nonexistent/ffmpeg")
            dlg._on_save()
        mock_mb.warning.assert_called_once()
        assert dlg.result() != 1  # not accepted

    def test_save_validates_ffprobe_path(self, qt_app: BootlegCatalogApplication) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        with patch("bootleg_catalog.ui.dialogs.settings_dialog.QMessageBox") as mock_mb:
            dlg = SettingsDialog(AppSettings())
            dlg._ffprobe_edit.setText("/nonexistent/ffprobe")
            dlg._on_save()
        mock_mb.warning.assert_called_once()
        assert dlg.result() != 1  # not accepted

    def test_save_accepts_empty_paths(self, qt_app: BootlegCatalogApplication) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(AppSettings(ffmpeg_path="/something"))
        dlg._ffmpeg_edit.clear()
        dlg._ffprobe_edit.clear()
        with patch.object(dlg, "accept") as mock_accept:
            dlg._on_save()
            mock_accept.assert_called_once()
        assert dlg.settings.ffmpeg_path == ""
        assert dlg.settings.ffprobe_path == ""

    def test_save_strips_whitespace(self, qt_app: BootlegCatalogApplication, tmp_path: Path) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        fake = tmp_path / "ffmpeg"
        fake.write_bytes(b"")
        dlg = SettingsDialog(AppSettings())
        dlg._ffmpeg_edit.setText(f"  {fake}  ")
        with patch.object(dlg, "accept") as mock_accept:
            dlg._on_save()
            mock_accept.assert_called_once()
        assert dlg.settings.ffmpeg_path == str(fake)


# ---------------------------------------------------------------------------
# Suggestion list sections (SettingsDialog)
# ---------------------------------------------------------------------------


class TestSuggestionSections:
    def test_sections_load_current_lists(self, qt_app) -> None:
        from PySide6.QtWidgets import QListWidget

        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        settings = AppSettings(
            type_suggestions=["T1", "T2"],
            source_suggestions=["S1"],
            quality_suggestions=["A+", "A"],
        )
        dlg = SettingsDialog(settings)
        assert isinstance(dlg._type_list, QListWidget)
        assert isinstance(dlg._source_list, QListWidget)
        assert isinstance(dlg._quality_list, QListWidget)
        assert [
            dlg._type_list.item(i).text() for i in range(dlg._type_list.count())
        ] == ["T1", "T2"]
        assert [
            dlg._source_list.item(i).text() for i in range(dlg._source_list.count())
        ] == ["S1"]
        assert [
            dlg._quality_list.item(i).text() for i in range(dlg._quality_list.count())
        ] == ["A+", "A"]

    def test_add_suggestion_appends_and_clears_edit(self, qt_app) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(AppSettings())
        dlg._type_edit.setText("  MyType  ")
        dlg._add_suggestion(dlg._type_list, dlg._type_edit)
        last = dlg._type_list.count() - 1
        assert dlg._type_list.item(last).text() == "MyType"
        assert dlg._type_edit.text() == ""

    def test_add_blank_suggestion_ignored(self, qt_app) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(AppSettings())
        before = dlg._type_list.count()
        dlg._type_edit.setText("   ")
        dlg._add_suggestion(dlg._type_list, dlg._type_edit)
        assert dlg._type_list.count() == before

    def test_save_writes_lists_and_leaves_caller_untouched(self, qt_app) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        incoming = AppSettings(type_suggestions=["Base"])
        dlg = SettingsDialog(incoming)
        dlg._type_list.setCurrentRow(0)
        dlg._remove_suggestion(dlg._type_list)
        dlg._quality_edit.setText("NewQ")
        dlg._add_suggestion(dlg._quality_list, dlg._quality_edit)
        with patch.object(dlg, "accept") as mock_accept:
            dlg._on_save()
        mock_accept.assert_called_once()
        assert dlg.settings.type_suggestions == []
        assert dlg.settings.quality_suggestions[-1] == "NewQ"
        # Caller's object is never mutated before acceptance
        assert incoming.type_suggestions == ["Base"]
        assert incoming.quality_suggestions == list(DEFAULT_QUALITY_SUGGESTIONS)

    def test_remove_selected_suggestion(self, qt_app) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(AppSettings(type_suggestions=["Keep", "Drop"]))
        dlg._type_list.setCurrentRow(1)
        dlg._remove_suggestion(dlg._type_list)
        assert [
            dlg._type_list.item(i).text() for i in range(dlg._type_list.count())
        ] == ["Keep"]
        with patch.object(dlg, "accept"):
            dlg._on_save()
        assert dlg.settings.type_suggestions == ["Keep"]

    def test_remove_without_selection_is_noop(self, qt_app) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(AppSettings(source_suggestions=["Only"]))
        dlg._remove_suggestion(dlg._source_list)
        assert dlg._source_list.count() == 1

    def test_cancel_discards_list_edits(self, qt_app) -> None:
        from PySide6.QtWidgets import QDialog

        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(AppSettings(type_suggestions=["Original"]))
        dlg._type_list.setCurrentRow(0)
        dlg._remove_suggestion(dlg._type_list)
        dlg.reject()
        assert dlg.result() == QDialog.DialogCode.Rejected
        assert dlg.settings.type_suggestions == ["Original"]

    def test_save_keeps_untouched_lists(self, qt_app) -> None:
        from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(AppSettings(
            type_suggestions=["OnlyType"],
            source_suggestions=["OnlySource"],
        ))
        with patch.object(dlg, "accept"):
            dlg._on_save()
        assert dlg.settings.type_suggestions == ["OnlyType"]
        assert dlg.settings.source_suggestions == ["OnlySource"]


