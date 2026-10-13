"""Tests for the layout resolver."""

from __future__ import annotations

import pytest

from bootleg_catalog.core.field_resolver import ResolvedElement, ResolvedLine
from bootleg_catalog.core.layout_resolver import LayoutResolver
from bootleg_catalog.core.show_grouper import ShowGroup, group_by_show
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FieldType,
    LayoutElement,
    LayoutLine,
    LayoutModel,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def resolver() -> LayoutResolver:
    return LayoutResolver()


def _rec(path: str = "/test.mkv", **kwargs) -> ExportRecording:
    return ExportRecording(source_path=path, **kwargs)


def _field(name: str, ft: FieldType, bold: bool = False) -> LayoutElement:
    style = ElementStyle(bold=bold) if bold else None
    return LayoutElement(kind="field", content=name, field_type=ft, style=style)


def _text(content: str, bold: bool = False) -> LayoutElement:
    style = ElementStyle(bold=bold) if bold else None
    return LayoutElement(kind="text", content=content, style=style)


def _line(*elements: LayoutElement) -> LayoutLine:
    return LayoutLine(elements=list(elements))


# ---------------------------------------------------------------------------
# Empty document
# ---------------------------------------------------------------------------

class TestEmptyDocument:
    def test_empty_groups(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(name="L", show_header=[], recording_body=[])
        doc = resolver.resolve(layout, [])
        assert doc.show_sections == []
        assert doc.separator == "\u2500"
        assert doc.layout is layout

    def test_empty_layout_with_groups(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(name="L", show_header=[], recording_body=[])
        groups = [ShowGroup(show_name="Show A", recordings=[_rec("/a.mkv")])]
        doc = resolver.resolve(layout, groups)
        assert len(doc.show_sections) == 1
        assert doc.show_sections[0].show_name == "Show A"
        assert len(doc.show_sections[0].header_lines) == 0
        assert len(doc.show_sections[0].recordings) == 1
        assert len(doc.show_sections[0].recordings[0].lines) == 0


# ---------------------------------------------------------------------------
# Single show, single recording
# ---------------------------------------------------------------------------

class TestSingleShowSingleRecording:
    def test_basic(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[_line(_field("Show", FieldType.ARCHIVAL))],
            recording_body=[_line(_field("Duration", FieldType.TECHNICAL))],
        )
        rec = _rec("/a.mkv", show="Munich '97", duration=7421.0)
        groups = [ShowGroup(show_name="Munich '97", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        section = doc.show_sections[0]
        assert section.show_name == "Munich '97"
        # Header resolved with first recording
        assert section.header_lines[0].elements[0].text == "Munich '97"
        # Body resolved
        assert section.recordings[0].lines[0].elements[0].text == "2:03:41"


# ---------------------------------------------------------------------------
# Show header archival resolution (L15.8.2)
# ---------------------------------------------------------------------------

class TestShowHeaderArchivalResolution:
    def test_show_header_resolves_archival_fields(self) -> None:
        """[Show]/[Year] markers in show_header resolve against the group's
        first recording, through the real flow (group_by_show + resolve)."""
        layout = LayoutModel(
            name="L",
            show_header=[
                _line(
                    _field("Show", FieldType.ARCHIVAL),
                    _text(" — "),
                    _field("Year", FieldType.ARCHIVAL),
                ),
            ],
            recording_body=[_line(_field("Title", FieldType.ARCHIVAL))],
        )
        rec = _rec("/a.mkv", show="Alegría", year=2010, title="Encore")

        doc = LayoutResolver().resolve(layout, group_by_show([rec]))

        section = doc.show_sections[0]
        texts = [
            el.text for line in section.header_lines for el in line.elements
        ]
        assert "Alegría" in texts
        assert "2010" in texts
        # Body still resolves alongside the header.
        body_texts = [
            el.text
            for line in section.recordings[0].lines
            for el in line.elements
        ]
        assert "Encore" in body_texts
        assert doc.errors == []


# ---------------------------------------------------------------------------
# Multiple shows
# ---------------------------------------------------------------------------

class TestMultipleShows:
    def test_order_preserved(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[_line(_field("Show", FieldType.ARCHIVAL))],
            recording_body=[],
        )
        groups = [
            ShowGroup(show_name="Alpha", recordings=[_rec("/a.mkv", show="Alpha")]),
            ShowGroup(show_name="Zebra", recordings=[_rec("/b.mkv", show="Zebra")]),
        ]
        doc = resolver.resolve(layout, groups)
        names = [s.show_name for s in doc.show_sections]
        assert names == ["Alpha", "Zebra"]


# ---------------------------------------------------------------------------
# Multiple recordings per show
# ---------------------------------------------------------------------------

class TestMultipleRecordingsPerShow:
    def test_recording_order_preserved(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("Title", FieldType.ARCHIVAL))],
        )
        r1 = _rec("/a.mkv", title="First")
        r2 = _rec("/b.mkv", title="Second")
        r3 = _rec("/c.mkv", title="Third")
        groups = [ShowGroup(show_name="Show", recordings=[r1, r2, r3])]
        doc = resolver.resolve(layout, groups)

        recs = doc.show_sections[0].recordings
        assert recs[0].lines[0].elements[0].text == "First"
        assert recs[1].lines[0].elements[0].text == "Second"
        assert recs[2].lines[0].elements[0].text == "Third"

    def test_each_recording_resolved_individually(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("Duration", FieldType.TECHNICAL))],
        )
        r1 = _rec("/a.mkv", duration=100.0)
        r2 = _rec("/b.mkv", duration=200.0)
        groups = [ShowGroup(show_name="Show", recordings=[r1, r2])]
        doc = resolver.resolve(layout, groups)

        recs = doc.show_sections[0].recordings
        assert recs[0].lines[0].elements[0].text == "1:40"
        assert recs[1].lines[0].elements[0].text == "3:20"


# ---------------------------------------------------------------------------
# Show header
# ---------------------------------------------------------------------------

class TestShowHeader:
    def test_header_resolved_with_first_recording(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[
                _line(
                    _field("Show", FieldType.ARCHIVAL),
                    _text(" — "),
                    _field("Venue", FieldType.ARCHIVAL),
                ),
            ],
            recording_body=[],
        )
        r1 = _rec("/a.mkv", show="Munich", venue="Olympiahalle")
        r2 = _rec("/b.mkv", show="Munich", venue="Different")
        groups = [ShowGroup(show_name="Munich", recordings=[r1, r2])]
        doc = resolver.resolve(layout, groups)

        header = doc.show_sections[0].header_lines
        assert header[0].elements[0].text == "Munich"
        assert header[0].elements[1].text == " — "
        # Uses first recording's venue
        assert header[0].elements[2].text == "Olympiahalle"

    def test_empty_header(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("Duration", FieldType.TECHNICAL))],
        )
        groups = [ShowGroup(show_name="Show", recordings=[_rec("/a.mkv")])]
        doc = resolver.resolve(layout, groups)
        assert doc.show_sections[0].header_lines == []

    def test_header_empty_when_no_recordings(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[_line(_field("Show", FieldType.ARCHIVAL))],
            recording_body=[],
        )
        groups = [ShowGroup(show_name="Empty Show", recordings=[])]
        doc = resolver.resolve(layout, groups)
        assert doc.show_sections[0].header_lines == []


# ---------------------------------------------------------------------------
# Recording body
# ---------------------------------------------------------------------------

class TestRecordingBody:
    def test_text_literal_preserved(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_text("Format: "), _field("Extension", FieldType.TECHNICAL))],
        )
        rec = _rec("/a.mkv", extension=".mkv")
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        body = doc.show_sections[0].recordings[0].lines
        assert body[0].elements[0].text == "Format: "
        assert body[0].elements[1].text == ".mkv"

    def test_technical_fields_resolved(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[
                _line(
                    _field("Resolution", FieldType.TECHNICAL),
                    _text(" | "),
                    _field("Duration", FieldType.TECHNICAL),
                ),
            ],
        )
        rec = _rec("/a.mkv", video_width=1920, video_height=1080, duration=7421.0)
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        body = doc.show_sections[0].recordings[0].lines[0]
        assert "1920" in body.elements[0].text
        assert "1080" in body.elements[0].text
        assert body.elements[1].text == " | "
        assert body.elements[2].text == "2:03:41"

    def test_archival_fields_resolved(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[
                _line(_field("Type", FieldType.ARCHIVAL), _text(" / "), _field("Source", FieldType.ARCHIVAL)),
            ],
        )
        rec = _rec("/a.mkv", type="Housecam", source="Audience")
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        body = doc.show_sections[0].recordings[0].lines[0]
        assert body.elements[0].text == "Housecam"
        assert body.elements[1].text == " / "
        assert body.elements[2].text == "Audience"

    def test_elements_stay_separate(self, resolver: LayoutResolver) -> None:
        """Elements must NOT be concatenated into a single string."""
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[
                _line(
                    _field("Title", FieldType.ARCHIVAL),
                    _text(" — "),
                    _field("Duration", FieldType.TECHNICAL),
                ),
            ],
        )
        rec = _rec("/a.mkv", title="My Bootleg", duration=4500.0)
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        elements = doc.show_sections[0].recordings[0].lines[0].elements
        assert len(elements) == 3
        assert elements[0].text == "My Bootleg"
        assert elements[1].text == " — "
        assert elements[2].text == "1:15:00"


# ---------------------------------------------------------------------------
# Style preservation
# ---------------------------------------------------------------------------

class TestStylePreservation:
    def test_style_on_text_element(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_text("Header", bold=True), _text("Normal"))],
        )
        rec = _rec("/a.mkv")
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        elements = doc.show_sections[0].recordings[0].lines[0].elements
        assert elements[0].style is not None
        assert elements[0].style.bold is True
        assert elements[1].style is None

    def test_style_on_field_element(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("Show", FieldType.ARCHIVAL, bold=True))],
        )
        rec = _rec("/a.mkv", show="Test")
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        elements = doc.show_sections[0].recordings[0].lines[0].elements
        assert elements[0].style is not None
        assert elements[0].style.bold is True
        assert elements[0].text == "Test"


# ---------------------------------------------------------------------------
# Separator
# ---------------------------------------------------------------------------

class TestSeparator:
    def test_separator_preserved(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(name="L", show_header=[], recording_body=[], separator="===")
        doc = resolver.resolve(layout, [])
        assert doc.separator == "==="

    def test_separator_none(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(name="L", show_header=[], recording_body=[], separator=None)
        doc = resolver.resolve(layout, [])
        assert doc.separator is None


# ---------------------------------------------------------------------------
# None values
# ---------------------------------------------------------------------------

class TestNoneValues:
    def test_none_field_resolves_to_empty(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("Duration", FieldType.TECHNICAL))],
        )
        rec = _rec("/a.mkv", duration=None)
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        assert doc.show_sections[0].recordings[0].lines[0].elements[0].text == ""


# ---------------------------------------------------------------------------
# Invalid field (unknown / wrong category)
# ---------------------------------------------------------------------------

class TestInvalidFields:
    def test_unknown_field_resolves_to_empty(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("Nonexistent", FieldType.TECHNICAL))],
        )
        rec = _rec("/a.mkv")
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        assert doc.show_sections[0].recordings[0].lines[0].elements[0].text == ""
        assert len(doc.errors) == 1
        assert doc.errors[0].field_name == "Nonexistent"

    def test_wrong_category_resolves_to_empty(self, resolver: LayoutResolver) -> None:
        """{Show} (technical syntax for archival field) → empty string."""
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("Show", FieldType.TECHNICAL))],
        )
        rec = _rec("/a.mkv", show="My Show")
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        # FieldResolver returns empty for category mismatch
        assert doc.show_sections[0].recordings[0].lines[0].elements[0].text == ""
        assert len(doc.errors) == 1
        assert doc.errors[0].field_name == "Show"
        assert doc.errors[0].declared_type == FieldType.TECHNICAL


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

class TestErrorPropagation:
    """Errors from FieldResolver must be collected on the document."""

    def test_technical_syntax_for_archival_field(self, resolver: LayoutResolver) -> None:
        """{Show} (technical syntax for archival field) → 1 error."""
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("Show", FieldType.TECHNICAL))],
        )
        rec = _rec("/a.mkv", show="My Show")
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        assert len(doc.errors) == 1
        err = doc.errors[0]
        assert err.field_name == "Show"
        assert err.declared_type == FieldType.TECHNICAL

    def test_archival_syntax_for_technical_field(self, resolver: LayoutResolver) -> None:
        """[Duration] (archival syntax for technical field) → 1 error."""
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("Duration", FieldType.ARCHIVAL))],
        )
        rec = _rec("/a.mkv", duration=100.0)
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        assert len(doc.errors) == 1
        err = doc.errors[0]
        assert err.field_name == "Duration"
        assert err.declared_type == FieldType.ARCHIVAL

    def test_unregistered_field_name(self, resolver: LayoutResolver) -> None:
        """Unknown field name → 1 error."""
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("FakeField", FieldType.TECHNICAL))],
        )
        rec = _rec("/a.mkv")
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        assert len(doc.errors) == 1
        assert doc.errors[0].field_name == "FakeField"

    def test_valid_layout_no_errors(self, resolver: LayoutResolver) -> None:
        """All correct fields → document.errors == []."""
        layout = LayoutModel(
            name="L",
            show_header=[_line(_field("Show", FieldType.ARCHIVAL))],
            recording_body=[_line(_field("Duration", FieldType.TECHNICAL))],
        )
        rec = _rec("/a.mkv", show="Test", duration=100.0)
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        assert doc.errors == []

    def test_error_does_not_corrupt_resolved_element(self, resolver: LayoutResolver) -> None:
        """Even with an error, the element text must be empty string."""
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[_line(_field("Show", FieldType.TECHNICAL))],
        )
        rec = _rec("/a.mkv", show="My Show")
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        doc = resolver.resolve(layout, groups)

        # Error exists
        assert len(doc.errors) == 1
        # Element text is empty
        element = doc.show_sections[0].recordings[0].lines[0].elements[0]
        assert element.text == ""

    def test_multiple_errors_across_recordings(self, resolver: LayoutResolver) -> None:
        """Errors from multiple recordings are all collected."""
        layout = LayoutModel(
            name="L",
            show_header=[],
            recording_body=[
                _line(_field("Show", FieldType.TECHNICAL)),  # wrong syntax
                _line(_field("Duration", FieldType.ARCHIVAL)),  # wrong syntax
            ],
        )
        r1 = _rec("/a.mkv", show="A", duration=1.0)
        r2 = _rec("/b.mkv", show="B", duration=2.0)
        groups = [ShowGroup(show_name="S", recordings=[r1, r2])]
        doc = resolver.resolve(layout, groups)

        # 2 errors per recording × 2 recordings = 4
        assert len(doc.errors) == 4

    def test_multiple_errors_across_shows(self, resolver: LayoutResolver) -> None:
        """Errors from multiple show groups are all collected."""
        layout = LayoutModel(
            name="L",
            show_header=[_line(_field("Duration", FieldType.ARCHIVAL))],  # wrong
            recording_body=[_line(_field("Show", FieldType.TECHNICAL))],  # wrong
        )
        r1 = _rec("/a.mkv", show="X", duration=1.0)
        r2 = _rec("/b.mkv", show="Y", duration=2.0)
        groups = [
            ShowGroup(show_name="X", recordings=[r1]),
            ShowGroup(show_name="Y", recordings=[r2]),
        ]
        doc = resolver.resolve(layout, groups)

        # 1 header error + 1 body error per group × 2 groups = 4
        assert len(doc.errors) == 4

    def test_empty_show_group_no_errors(self, resolver: LayoutResolver) -> None:
        """Empty show group with header layout produces no errors."""
        layout = LayoutModel(
            name="L",
            show_header=[_line(_field("Show", FieldType.ARCHIVAL))],
            recording_body=[],
        )
        groups = [ShowGroup(show_name="Empty", recordings=[])]
        doc = resolver.resolve(layout, groups)

        # Header not resolved (no recordings), body empty → no errors
        assert doc.errors == []

    def test_no_error_for_valid_layout_with_valid_layout(self, resolver: LayoutResolver) -> None:
        """Completely valid layout with valid fields produces no errors."""
        layout = LayoutModel(
            name="L",
            show_header=[
                _line(_field("Show", FieldType.ARCHIVAL, bold=True)),
                _line(_field("Location", FieldType.ARCHIVAL), _text(", "), _field("Country", FieldType.ARCHIVAL)),
            ],
            recording_body=[
                _line(_text("Format: "), _field("Extension", FieldType.TECHNICAL)),
                _line(_field("Type", FieldType.ARCHIVAL), _text(" / "), _field("Source", FieldType.ARCHIVAL)),
            ],
        )
        r1 = _rec(
            "/a.mkv", show="Munich", location="Munich", country="DE",
            extension=".mkv", type="Housecam", source="Audience",
        )
        groups = [ShowGroup(show_name="Munich", recordings=[r1])]
        doc = resolver.resolve(layout, groups)
        assert doc.errors == []


# ---------------------------------------------------------------------------
# Input immutability
# ---------------------------------------------------------------------------

class TestInputImmutability:
    def test_layout_not_modified(self, resolver: LayoutResolver) -> None:
        layout = LayoutModel(
            name="L",
            show_header=[_line(_field("Show", FieldType.ARCHIVAL))],
            recording_body=[_line(_field("Duration", FieldType.TECHNICAL))],
        )
        original_header_content = layout.show_header[0].elements[0].content
        original_body_content = layout.recording_body[0].elements[0].content

        groups = [ShowGroup(show_name="S", recordings=[_rec("/a.mkv", show="X", duration=1.0)])]
        resolver.resolve(layout, groups)

        assert layout.show_header[0].elements[0].content == original_header_content
        assert layout.recording_body[0].elements[0].content == original_body_content

    def test_recording_not_modified(self, resolver: LayoutResolver) -> None:
        rec = _rec("/a.mkv", show="Test", duration=100.0, title="Original")
        layout = LayoutModel(
            name="L",
            show_header=[_line(_field("Show", FieldType.ARCHIVAL))],
            recording_body=[_line(_field("Title", FieldType.ARCHIVAL))],
        )
        groups = [ShowGroup(show_name="S", recordings=[rec])]
        resolver.resolve(layout, groups)

        assert rec.show == "Test"
        assert rec.duration == 100.0
        assert rec.title == "Original"


# ---------------------------------------------------------------------------
# Comprehensive scenario
# ---------------------------------------------------------------------------

class TestComprehensiveScenario:
    def test_full_layout_resolution(self, resolver: LayoutResolver) -> None:
        """End-to-end: header + body with mixed fields, multiple recordings."""
        layout = LayoutModel(
            name="Default",
            show_header=[
                _line(_field("Show", FieldType.ARCHIVAL, bold=True)),
                _line(
                    _field("Location", FieldType.ARCHIVAL),
                    _text(", "),
                    _field("Country", FieldType.ARCHIVAL),
                ),
            ],
            recording_body=[
                _line(
                    _text("Format: "),
                    _field("Extension", FieldType.TECHNICAL),
                    _text(" | Duration: "),
                    _field("Duration", FieldType.TECHNICAL),
                    _text(" | Resolution: "),
                    _field("Resolution", FieldType.TECHNICAL),
                ),
                _line(
                    _field("Type", FieldType.ARCHIVAL),
                    _text(" / "),
                    _field("Source", FieldType.ARCHIVAL),
                ),
            ],
            separator="\u2500",
        )

        r1 = _rec(
            "/munich.mkv",
            show="Munich '97",
            location="Munich",
            country="Germany",
            extension=".mkv",
            duration=7421.0,
            video_width=1920,
            video_height=1080,
            type="Housecam",
            source="Audience",
        )
        r2 = _rec(
            "/munich2.mkv",
            show="Munich '97",
            location="Munich",
            country="Germany",
            extension=".avi",
            duration=5400.0,
            video_width=720,
            video_height=480,
            type="Fancam",
            source="Audience",
        )
        r3 = _rec(
            "/berlin.mkv",
            show="Berlin '82",
            location="Berlin",
            country="Germany",
            extension=".mkv",
            duration=6000.0,
            video_width=1920,
            video_height=1080,
            type="Pro-shot",
            source="TV Broadcast",
        )

        groups = [
            ShowGroup(show_name="Berlin '82", recordings=[r3]),
            ShowGroup(show_name="Munich '97", recordings=[r1, r2]),
        ]

        doc = resolver.resolve(layout, groups)

        # --- Structure checks ---
        assert doc.separator == "\u2500"
        assert len(doc.show_sections) == 2

        # --- Berlin section ---
        berlin = doc.show_sections[0]
        assert berlin.show_name == "Berlin '82"

        # Header
        assert len(berlin.header_lines) == 2
        h0 = berlin.header_lines[0]
        assert h0.elements[0].text == "Berlin '82"
        assert h0.elements[0].style is not None
        assert h0.elements[0].style.bold is True

        h1 = berlin.header_lines[1]
        assert h1.elements[0].text == "Berlin"
        assert h1.elements[1].text == ", "
        assert h1.elements[2].text == "Germany"

        # Single recording
        assert len(berlin.recordings) == 1
        berlin_rec = berlin.recordings[0]
        assert berlin_rec.recording.source_path == "/berlin.mkv"

        body0 = berlin_rec.lines[0]
        assert body0.elements[0].text == "Format: "
        assert body0.elements[1].text == ".mkv"
        assert body0.elements[2].text == " | Duration: "
        assert body0.elements[3].text == "1:40:00"
        assert body0.elements[4].text == " | Resolution: "
        assert "1920" in body0.elements[5].text

        body1 = berlin_rec.lines[1]
        assert body1.elements[0].text == "Pro-shot"
        assert body1.elements[1].text == " / "
        assert body1.elements[2].text == "TV Broadcast"

        # --- Munich section ---
        munich = doc.show_sections[1]
        assert munich.show_name == "Munich '97"

        # Header uses first recording
        assert munich.header_lines[0].elements[0].text == "Munich '97"
        assert munich.header_lines[1].elements[0].text == "Munich"
        assert munich.header_lines[1].elements[2].text == "Germany"

        # Two recordings
        assert len(munich.recordings) == 2

        m1 = munich.recordings[0]
        assert m1.recording.source_path == "/munich.mkv"
        assert m1.lines[0].elements[1].text == ".mkv"
        assert m1.lines[0].elements[3].text == "2:03:41"
        assert m1.lines[1].elements[0].text == "Housecam"
        assert m1.lines[1].elements[2].text == "Audience"

        m2 = munich.recordings[1]
        assert m2.recording.source_path == "/munich2.mkv"
        assert m2.lines[0].elements[1].text == ".avi"
        assert m2.lines[0].elements[3].text == "1:30:00"
        assert m2.lines[1].elements[0].text == "Fancam"
        assert m2.lines[1].elements[2].text == "Audience"


# ---------------------------------------------------------------------------
# Independence
# ---------------------------------------------------------------------------

class TestIndependence:
    def test_no_catalog_import(self) -> None:
        import bootleg_catalog.core.layout_resolver as mod
        source = open(mod.__file__, encoding="utf-8").read()
        assert "from bootleg_catalog.core.catalog" not in source
        assert "from bootleg_catalog.database" not in source
        assert "Catalog" not in source
        assert "Repository" not in source

    def test_no_qt_dependency(self) -> None:
        import bootleg_catalog.core.layout_resolver as mod
        source = open(mod.__file__, encoding="utf-8").read()
        assert "PySide6" not in source
        assert "QtWidgets" not in source
