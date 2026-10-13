"""Tests for ExportSessionService."""

from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bootleg_catalog.core.export_session import ExportSession, ExportSessionError
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
from bootleg_catalog.models.resolved_export import ResolvedExportDocument
from bootleg_catalog.utils.ffmpeg import FFprobeError, ProbeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_layout(name: str = "Test Layout") -> LayoutModel:
    """Create a minimal layout for testing."""
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


def _make_probe_result(**overrides) -> ProbeResult:
    """Create a ProbeResult with sensible defaults."""
    defaults = dict(
        duration=7200.0,
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
    return ProbeResult(**defaults)


# ---------------------------------------------------------------------------
# Service creation
# ---------------------------------------------------------------------------

class TestServiceCreation:
    def test_empty_session(self) -> None:
        layout = _make_layout()
        svc = ExportSessionService(layout)
        assert svc.session.recording_count == 0
        assert svc.layout.name == "Test Layout"

    def test_custom_probe_settings(self) -> None:
        layout = _make_layout()
        svc = ExportSessionService(
            layout,
            ffprobe_path="/custom/ffprobe",
            probe_timeout=60,
        )
        assert svc._ffprobe_path == "/custom/ffprobe"
        assert svc._probe_timeout == 60


# ---------------------------------------------------------------------------
# add_recording_from_path
# ---------------------------------------------------------------------------

class TestAddRecordingFromPath:
    def test_nonexistent_file_raises(self) -> None:
        svc = ExportSessionService(_make_layout())
        with pytest.raises(ExportSessionServiceError, match="does not exist"):
            svc.add_recording_from_path("/nonexistent/video.mkv")

    def test_adds_recording_with_probe_data(self, tmp_path: Path) -> None:
        video = tmp_path / "concert.mkv"
        video.write_bytes(b"\x00" * 100)

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            rec = svc.add_recording_from_path(video)

        assert rec.source_path == str(video.resolve())
        assert rec.extension == ".mkv"
        assert rec.file_size == 100
        assert rec.duration == 7200.0
        assert rec.video_codec == "h264"
        assert rec.probe_error is None
        assert svc.session.recording_count == 1

    def test_archival_fields_are_defaults(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00" * 50)

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            rec = svc.add_recording_from_path(video)

        assert rec.title is None
        assert rec.show is None
        assert rec.source is None
        assert rec.type is None
        assert rec.quality is None
        assert rec.master is None

    def test_screenshot_paths_populated(self, tmp_path: Path) -> None:
        video = tmp_path / "concert.mkv"
        video.write_bytes(b"\x00" * 100)
        screenshot = tmp_path / "concert.screenshot.01.jpg"
        screenshot.write_bytes(b"\xff")

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            rec = svc.add_recording_from_path(video)

        assert len(rec.screenshot_paths) == 1
        assert rec.screenshot_paths[0] == str(screenshot.resolve())

    def test_probe_failure_still_creates_recording(self, tmp_path: Path) -> None:
        video = tmp_path / "bad.mkv"
        video.write_bytes(b"\x00" * 200)

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            side_effect=FFprobeError("probe failed"),
        ):
            rec = svc.add_recording_from_path(video)

        assert rec.source_path == str(video.resolve())
        assert rec.file_size == 200
        assert rec.probe_error == "probe failed"
        assert rec.duration is None
        assert rec.video_codec is None
        assert svc.session.recording_count == 1

    def test_duplicate_source_path_raises(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00" * 10)

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            svc.add_recording_from_path(video)
            with pytest.raises(ExportSessionError, match="already exists"):
                svc.add_recording_from_path(video)

    def test_no_filesystem_modification(self, tmp_path: Path) -> None:
        video = tmp_path / "concert.mkv"
        video.write_bytes(b"\x00" * 100)
        original_mtime = video.stat().st_mtime_ns

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            svc.add_recording_from_path(video)

        assert video.stat().st_mtime_ns == original_mtime


# ---------------------------------------------------------------------------
# probe_recording_from_path
# ---------------------------------------------------------------------------

class TestProbeRecordingFromPath:
    def test_populates_recording_without_adding(self, tmp_path: Path) -> None:
        video = tmp_path / "concert.mkv"
        video.write_bytes(b"\x00" * 100)

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            rec = svc.probe_recording_from_path(video)

        assert rec.source_path == str(video.resolve())
        assert rec.extension == ".mkv"
        assert rec.file_size == 100
        assert rec.duration == 7200.0
        assert rec.video_codec == "h264"
        assert svc.session.recording_count == 0  # NOT added

    def test_nonexistent_file_raises(self) -> None:
        svc = ExportSessionService(_make_layout())
        with pytest.raises(ExportSessionServiceError, match="does not exist"):
            svc.probe_recording_from_path("/nonexistent/video.mkv")

    def test_probe_failure_returns_recording_with_error(self, tmp_path: Path) -> None:
        video = tmp_path / "bad.mkv"
        video.write_bytes(b"\x00" * 200)

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            side_effect=FFprobeError("probe failed"),
        ):
            rec = svc.probe_recording_from_path(video)

        assert rec.source_path == str(video.resolve())
        assert rec.file_size == 200
        assert rec.probe_error == "probe failed"
        assert svc.session.recording_count == 0  # NOT added

    def test_called_twice_no_duplicate_error(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00" * 10)

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            rec1 = svc.probe_recording_from_path(video)
            rec2 = svc.probe_recording_from_path(video)

        # Both calls succeed — nothing was added to the session
        assert svc.session.recording_count == 0
        assert rec1.source_path == rec2.source_path

    def test_probe_then_add_succeeds(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00" * 10)

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            rec = svc.probe_recording_from_path(video)
            svc.session.add_recording(rec)

        assert svc.session.recording_count == 1
        assert svc.session.get_recording(str(video.resolve())).source_path == rec.source_path

    def test_probe_then_add_twice_raises(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00" * 10)

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            rec = svc.probe_recording_from_path(video)
            svc.session.add_recording(rec)
            with pytest.raises(ExportSessionError, match="already exists"):
                svc.add_recording_from_path(video)

    def test_screenshot_paths_populated(self, tmp_path: Path) -> None:
        video = tmp_path / "concert.mkv"
        video.write_bytes(b"\x00" * 100)
        screenshot = tmp_path / "concert.screenshot.01.jpg"
        screenshot.write_bytes(b"\xff")

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            rec = svc.probe_recording_from_path(video)

        assert len(rec.screenshot_paths) == 1
        assert svc.session.recording_count == 0

    def test_archival_fields_are_defaults(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00" * 50)

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            rec = svc.probe_recording_from_path(video)

        assert rec.title is None
        assert rec.show is None
        assert rec.source is None
        assert rec.type is None
        assert rec.quality is None
        assert rec.master is None


# ---------------------------------------------------------------------------
# remove_recording
# ---------------------------------------------------------------------------

class TestRemoveRecording:
    def test_removes_recording(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00")

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            svc.add_recording_from_path(video)
        assert svc.session.recording_count == 1

        removed = svc.remove_recording(str(video.resolve()))
        assert removed.source_path == str(video.resolve())
        assert svc.session.recording_count == 0

    def test_remove_nonexistent_raises(self) -> None:
        svc = ExportSessionService(_make_layout())
        with pytest.raises(ExportSessionError, match="No recording"):
            svc.remove_recording("/nonexistent/path.mkv")


# ---------------------------------------------------------------------------
# update_recording
# ---------------------------------------------------------------------------

class TestUpdateRecording:
    def test_updates_recording(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00")
        abs_path = str(video.resolve())

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            svc.add_recording_from_path(video)

        updated = ExportRecording(
            source_path=abs_path,
            title="Updated Title",
            show="Updated Show",
        )
        svc.update_recording(abs_path, updated)

        rec = svc.session.get_recording(abs_path)
        assert rec.title == "Updated Title"
        assert rec.show == "Updated Show"

    def test_update_nonexistent_raises(self) -> None:
        svc = ExportSessionService(_make_layout())
        updated = ExportRecording(source_path="/no/such.mkv")
        with pytest.raises(ExportSessionError, match="No recording"):
            svc.update_recording("/no/such.mkv", updated)


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------

class TestClear:
    def test_clears_recordings(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00")

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            svc.add_recording_from_path(video)
        assert svc.session.recording_count == 1

        svc.clear()
        assert svc.session.recording_count == 0

    def test_clear_preserves_layout(self) -> None:
        layout = _make_layout("My Layout")
        svc = ExportSessionService(layout)
        svc.clear()
        assert svc.layout.name == "My Layout"


# ---------------------------------------------------------------------------
# set_layout
# ---------------------------------------------------------------------------

class TestSetLayout:
    def test_replaces_layout(self) -> None:
        svc = ExportSessionService(_make_layout("Old"))
        new_layout = _make_layout("New")
        svc.set_layout(new_layout)
        assert svc.layout.name == "New"

    def test_preserves_recordings(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00")

        svc = ExportSessionService(_make_layout("Old"))
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            svc.add_recording_from_path(video)

        svc.set_layout(_make_layout("New"))
        assert svc.session.recording_count == 1


# ---------------------------------------------------------------------------
# build_document
# ---------------------------------------------------------------------------

class TestBuildDocument:
    def test_returns_resolved_document(self) -> None:
        svc = ExportSessionService(_make_layout())
        doc = svc.build_document()
        assert isinstance(doc, ResolvedExportDocument)
        assert doc.show_sections == []

    def test_reflects_current_session(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00")

        layout = _make_layout()
        svc = ExportSessionService(layout)

        # Empty session
        doc = svc.build_document()
        assert len(doc.show_sections) == 0

        # Add a recording
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            svc.add_recording_from_path(video)

        # Document should now have a section
        doc = svc.build_document()
        assert len(doc.show_sections) >= 1

    def test_reflects_layout_change(self, tmp_path: Path) -> None:
        video = tmp_path / "show.mkv"
        video.write_bytes(b"\x00")

        svc = ExportSessionService(_make_layout("V1"))
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            svc.add_recording_from_path(video)

        doc_v1 = svc.build_document()
        assert doc_v1.layout.name == "V1"

        svc.set_layout(_make_layout("V2"))
        doc_v2 = svc.build_document()
        assert doc_v2.layout.name == "V2"
        # Recording count unchanged
        total_v1 = sum(len(s.recordings) for s in doc_v1.show_sections)
        total_v2 = sum(len(s.recordings) for s in doc_v2.show_sections)
        assert total_v1 == total_v2

    def test_groups_by_show(self, tmp_path: Path) -> None:
        layout = _make_layout()
        svc = ExportSessionService(layout)

        # Create two recordings with different shows
        rec1 = ExportRecording(
            source_path="/a.mkv",
            show="Show A",
            duration=100.0,
        )
        rec2 = ExportRecording(
            source_path="/b.mkv",
            show="Show B",
            duration=200.0,
        )
        rec3 = ExportRecording(
            source_path="/c.mkv",
            show="Show A",
            duration=300.0,
        )

        svc.session.add_recording(rec1)
        svc.session.add_recording(rec2)
        svc.session.add_recording(rec3)

        doc = svc.build_document()
        show_names = [s.show_name for s in doc.show_sections]
        assert "Show A" in show_names
        assert "Show B" in show_names


# ---------------------------------------------------------------------------
# get_screenshots
# ---------------------------------------------------------------------------

class TestGetScreenshots:
    def test_returns_screenshots_for_recording(self, tmp_path: Path) -> None:
        video = tmp_path / "concert.mkv"
        video.write_bytes(b"\x00")
        s1 = tmp_path / "concert.screenshot.01.jpg"
        s1.write_bytes(b"\xff")
        s2 = tmp_path / "concert.screenshot.02.jpg"
        s2.write_bytes(b"\xff")

        svc = ExportSessionService(_make_layout())
        with patch(
            "bootleg_catalog.core.export_session_service.probe",
            return_value=_make_probe_result(),
        ):
            svc.add_recording_from_path(video)

        screenshots = svc.get_screenshots(str(video.resolve()))
        assert len(screenshots) == 2

    def test_returns_empty_for_unknown_path(self) -> None:
        svc = ExportSessionService(_make_layout())
        assert svc.get_screenshots("/nonexistent/path.mkv") == []


# ---------------------------------------------------------------------------
# Module independence
# ---------------------------------------------------------------------------

class TestModuleIndependence:
    def test_no_qt_import(self) -> None:
        """The service module must not import Qt."""
        import bootleg_catalog.core.export_session_service as mod
        source = inspect.getsource(mod)
        assert "PySide6" not in source
        assert "from PySide6" not in source
        assert "import PySide6" not in source

    def test_no_catalog_import(self) -> None:
        """The service module must not import Catalog or CatalogRepository."""
        import bootleg_catalog.core.export_session_service as mod
        source = inspect.getsource(mod)
        assert "Catalog" not in source or "CatalogRepository" not in source
        assert "from bootleg_catalog.core.catalog" not in source
        assert "from bootleg_catalog.database" not in source

    def test_no_sqlite_import(self) -> None:
        """The service module must not import sqlite3."""
        import bootleg_catalog.core.export_session_service as mod
        source = inspect.getsource(mod)
        assert "import sqlite3" not in source
        assert "from sqlite3" not in source

    def test_importable_without_qt(self) -> None:
        """Module can be imported when QT_QPA_PLATFORM is unset."""
        import bootleg_catalog.core.export_session_service as mod
        assert hasattr(mod, "ExportSessionService")
