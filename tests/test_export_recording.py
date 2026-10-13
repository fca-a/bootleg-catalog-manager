"""Tests for ExportRecording model."""

from __future__ import annotations

import pytest

from bootleg_catalog.models.export_recording import ExportRecording


class TestExportRecordingDefaults:
    """ExportRecording should have sensible defaults for all optional fields."""

    def test_source_path_required(self) -> None:
        rec = ExportRecording(source_path="/some/path.mkv")
        assert rec.source_path == "/some/path.mkv"

    def test_technical_defaults_are_none(self) -> None:
        rec = ExportRecording(source_path="/x.mkv")
        assert rec.extension is None
        assert rec.file_size is None
        assert rec.duration is None
        assert rec.container_format is None
        assert rec.video_codec is None
        assert rec.video_width is None
        assert rec.video_height is None
        assert rec.frame_rate is None
        assert rec.pixel_format is None
        assert rec.audio_codec is None
        assert rec.audio_channels is None
        assert rec.audio_sample_rate is None
        assert rec.audio_bitrate is None

    def test_archival_defaults_are_none(self) -> None:
        rec = ExportRecording(source_path="/x.mkv")
        assert rec.title is None
        assert rec.year is None
        assert rec.date is None
        assert rec.venue is None
        assert rec.location is None
        assert rec.country is None
        assert rec.tour is None
        assert rec.show is None
        assert rec.source is None
        assert rec.type is None
        assert rec.generation is None
        assert rec.quality is None
        assert rec.audio_source is None
        assert rec.video_source is None
        assert rec.master is None
        assert rec.notes is None
        assert rec.description is None

    def test_screenshot_paths_defaults_to_empty_list(self) -> None:
        rec = ExportRecording(source_path="/x.mkv")
        assert rec.screenshot_paths == []

    def test_screenshot_paths_independent_between_instances(self) -> None:
        rec1 = ExportRecording(source_path="/a.mkv")
        rec2 = ExportRecording(source_path="/b.mkv")
        rec1.screenshot_paths.append("/a.screenshot.01.jpg")
        assert rec2.screenshot_paths == []


class TestExportRecordingFields:
    """ExportRecording stores all specified fields."""

    def test_all_technical_fields(self) -> None:
        rec = ExportRecording(
            source_path="/test.mkv",
            extension=".mkv",
            file_size=2_500_000_000,
            duration=7421.0,
            container_format="matroska",
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
        assert rec.extension == ".mkv"
        assert rec.file_size == 2_500_000_000
        assert rec.duration == 7421.0
        assert rec.container_format == "matroska"
        assert rec.video_codec == "h264"
        assert rec.video_width == 1920
        assert rec.video_height == 1080
        assert rec.frame_rate == 29.97
        assert rec.pixel_format == "yuv420p"
        assert rec.audio_codec == "aac"
        assert rec.audio_channels == 2
        assert rec.audio_sample_rate == 48000
        assert rec.audio_bitrate == 192000

    def test_all_archival_fields(self) -> None:
        rec = ExportRecording(
            source_path="/test.mkv",
            title="Test Title",
            year=1997,
            date="1997-03-15",
            venue="Olympiahalle",
            location="Munich",
            country="Germany",
            tour="European Tour",
            show="Munich '97",
            source="Audience",
            type="Housecam",
            generation="1st gen",
            quality="Excellent",
            audio_source="Soundboard",
            video_source="Camera",
            master="Yes",
            notes="Test notes",
            description="Test description",
        )
        assert rec.title == "Test Title"
        assert rec.year == 1997
        assert rec.date == "1997-03-15"
        assert rec.venue == "Olympiahalle"
        assert rec.location == "Munich"
        assert rec.country == "Germany"
        assert rec.tour == "European Tour"
        assert rec.show == "Munich '97"
        assert rec.source == "Audience"
        assert rec.type == "Housecam"
        assert rec.generation == "1st gen"
        assert rec.quality == "Excellent"
        assert rec.audio_source == "Soundboard"
        assert rec.video_source == "Camera"
        assert rec.master == "Yes"
        assert rec.notes == "Test notes"
        assert rec.description == "Test description"

    def test_screenshot_paths_stored(self) -> None:
        rec = ExportRecording(
            source_path="/test.mkv",
            screenshot_paths=["/test.screenshot.01.jpg", "/test.screenshot.02.jpg"],
        )
        assert len(rec.screenshot_paths) == 2
        assert rec.screenshot_paths[0] == "/test.screenshot.01.jpg"
        assert rec.screenshot_paths[1] == "/test.screenshot.02.jpg"


class TestExportRecordingIndependence:
    """ExportRecording must not depend on the catalog."""

    def test_no_catalog_import(self) -> None:
        """The module should not import Catalog, Repository, or Recording."""
        import bootleg_catalog.models.export_recording as mod
        source = open(mod.__file__, encoding="utf-8").read()
        assert "from bootleg_catalog.core.catalog" not in source
        assert "from bootleg_catalog.database" not in source
        assert "from bootleg_catalog.models.recording" not in source
        assert "Catalog" not in source
        assert "Repository" not in source

    def test_no_database_id(self) -> None:
        rec = ExportRecording(source_path="/x.mkv")
        assert not hasattr(rec, "id")
        assert not hasattr(rec, "folder_id")

    def test_no_missing_field(self) -> None:
        rec = ExportRecording(source_path="/x.mkv")
        assert not hasattr(rec, "missing")
