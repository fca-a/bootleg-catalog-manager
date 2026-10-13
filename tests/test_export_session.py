"""Tests for the export session model."""

from __future__ import annotations

import pytest

from bootleg_catalog.core.export_session import ExportSession, ExportSessionError
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    FieldType,
    LayoutElement,
    LayoutLine,
    LayoutModel,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def layout() -> LayoutModel:
    return LayoutModel(
        name="Test Layout",
        show_header=[
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL),
            ]),
        ],
        recording_body=[
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL),
            ]),
        ],
    )


@pytest.fixture
def session(layout: LayoutModel) -> ExportSession:
    return ExportSession(layout=layout)


def _rec(path: str = "/test.mkv", **kwargs) -> ExportRecording:
    return ExportRecording(source_path=path, **kwargs)


# ---------------------------------------------------------------------------
# Empty session
# ---------------------------------------------------------------------------

class TestEmptySession:
    def test_empty_session(self, session: ExportSession) -> None:
        assert session.recording_count == 0
        assert session.recordings == []

    def test_layout_preserved(self, session: ExportSession, layout: LayoutModel) -> None:
        assert session.layout is layout
        assert session.layout.name == "Test Layout"


# ---------------------------------------------------------------------------
# Add recording
# ---------------------------------------------------------------------------

class TestAddRecording:
    def test_add_one(self, session: ExportSession) -> None:
        rec = _rec("/a.mkv")
        session.add_recording(rec)
        assert session.recording_count == 1
        assert session.recordings[0] is rec

    def test_add_multiple(self, session: ExportSession) -> None:
        r1 = _rec("/a.mkv")
        r2 = _rec("/b.mkv")
        r3 = _rec("/c.mkv")
        session.add_recording(r1)
        session.add_recording(r2)
        session.add_recording(r3)
        assert session.recording_count == 3

    def test_order_preserved(self, session: ExportSession) -> None:
        r1 = _rec("/a.mkv")
        r2 = _rec("/b.mkv")
        r3 = _rec("/c.mkv")
        session.add_recording(r1)
        session.add_recording(r2)
        session.add_recording(r3)
        recordings = session.recordings
        assert recordings[0] is r1
        assert recordings[1] is r2
        assert recordings[2] is r3

    def test_duplicate_raises(self, session: ExportSession) -> None:
        session.add_recording(_rec("/a.mkv"))
        with pytest.raises(ExportSessionError, match="already exists"):
            session.add_recording(_rec("/a.mkv"))


# ---------------------------------------------------------------------------
# Remove recording
# ---------------------------------------------------------------------------

class TestRemoveRecording:
    def test_remove_existing(self, session: ExportSession) -> None:
        rec = _rec("/a.mkv")
        session.add_recording(rec)
        removed = session.remove_recording("/a.mkv")
        assert removed is rec
        assert session.recording_count == 0

    def test_remove_preserves_order(self, session: ExportSession) -> None:
        r1 = _rec("/a.mkv")
        r2 = _rec("/b.mkv")
        r3 = _rec("/c.mkv")
        session.add_recording(r1)
        session.add_recording(r2)
        session.add_recording(r3)
        session.remove_recording("/b.mkv")
        assert session.recording_count == 2
        assert session.recordings[0] is r1
        assert session.recordings[1] is r3

    def test_remove_nonexistent_raises(self, session: ExportSession) -> None:
        with pytest.raises(ExportSessionError, match="No recording"):
            session.remove_recording("/nonexistent.mkv")


# ---------------------------------------------------------------------------
# Update recording
# ---------------------------------------------------------------------------

class TestUpdateRecording:
    def test_update_existing(self, session: ExportSession) -> None:
        old = _rec("/a.mkv", show="Old Show")
        session.add_recording(old)
        new = _rec("/a.mkv", show="New Show")
        session.update_recording("/a.mkv", new)
        assert session.recording_count == 1
        assert session.recordings[0] is new
        assert session.recordings[0].show == "New Show"

    def test_update_preserves_position(self, session: ExportSession) -> None:
        r1 = _rec("/a.mkv")
        r2 = _rec("/b.mkv")
        r3 = _rec("/c.mkv")
        session.add_recording(r1)
        session.add_recording(r2)
        session.add_recording(r3)
        new_r2 = _rec("/b.mkv", show="Updated")
        session.update_recording("/b.mkv", new_r2)
        assert session.recordings[0] is r1
        assert session.recordings[1] is new_r2
        assert session.recordings[2] is r3

    def test_update_nonexistent_raises(self, session: ExportSession) -> None:
        with pytest.raises(ExportSessionError, match="No recording"):
            session.update_recording("/nonexistent.mkv", _rec("/nonexistent.mkv"))

    def test_update_path_mismatch_raises(self, session: ExportSession) -> None:
        session.add_recording(_rec("/a.mkv"))
        with pytest.raises(ExportSessionError, match="does not match"):
            session.update_recording("/a.mkv", _rec("/different.mkv"))


# ---------------------------------------------------------------------------
# Clear recordings
# ---------------------------------------------------------------------------

class TestClearRecordings:
    def test_clear(self, session: ExportSession) -> None:
        session.add_recording(_rec("/a.mkv"))
        session.add_recording(_rec("/b.mkv"))
        session.clear_recordings()
        assert session.recording_count == 0
        assert session.recordings == []

    def test_clear_empty(self, session: ExportSession) -> None:
        session.clear_recordings()
        assert session.recording_count == 0


# ---------------------------------------------------------------------------
# Get recording
# ---------------------------------------------------------------------------

class TestGetRecording:
    def test_get_existing(self, session: ExportSession) -> None:
        rec = _rec("/a.mkv")
        session.add_recording(rec)
        assert session.get_recording("/a.mkv") is rec

    def test_get_nonexistent_raises(self, session: ExportSession) -> None:
        with pytest.raises(ExportSessionError, match="No recording"):
            session.get_recording("/nonexistent.mkv")


# ---------------------------------------------------------------------------
# Layout setter
# ---------------------------------------------------------------------------

class TestLayoutSetter:
    def test_replace_layout(self, session: ExportSession) -> None:
        new_layout = LayoutModel(
            name="New Layout",
            show_header=[],
            recording_body=[],
        )
        session.layout = new_layout
        assert session.layout.name == "New Layout"


# ---------------------------------------------------------------------------
# Recordings list is a copy
# ---------------------------------------------------------------------------

class TestRecordingsCopy:
    def test_recordings_returns_copy(self, session: ExportSession) -> None:
        session.add_recording(_rec("/a.mkv"))
        copy = session.recordings
        copy.clear()
        assert session.recording_count == 1  # original unaffected


# ---------------------------------------------------------------------------
# Independence from catalog
# ---------------------------------------------------------------------------

class TestIndependence:
    def test_no_catalog_import(self) -> None:
        """The module should not import Catalog, Repository, or SQLite."""
        import bootleg_catalog.core.export_session as mod
        source = open(mod.__file__, encoding="utf-8").read()
        assert "from bootleg_catalog.core.catalog" not in source
        assert "from bootleg_catalog.database" not in source
        assert "from bootleg_catalog.models.recording import Recording" not in source
        assert "Catalog" not in source
        assert "Repository" not in source

    def test_no_qt_dependency(self) -> None:
        import bootleg_catalog.core.export_session as mod
        source = open(mod.__file__, encoding="utf-8").read()
        assert "PySide6" not in source
        assert "QtWidgets" not in source
