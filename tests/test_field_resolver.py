"""Tests for the field resolver."""

from __future__ import annotations

import pytest

from bootleg_catalog.core.field_resolver import (
    FieldResolver,
    FieldResolutionError,
    ResolvedElement,
    ResolvedLine,
)
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FieldType,
    LayoutElement,
    LayoutLine,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def resolver() -> FieldResolver:
    return FieldResolver()


@pytest.fixture
def full_recording() -> ExportRecording:
    return ExportRecording(
        source_path="/shows/munich.mkv",
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
        title="Munich 1997",
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
        description="A test recording",
    )


@pytest.fixture
def empty_recording() -> ExportRecording:
    return ExportRecording(source_path="/empty.mkv")


# ---------------------------------------------------------------------------
# Text literal
# ---------------------------------------------------------------------------

class TestTextLiteral:
    def test_text_literal_unchanged(self, resolver: FieldResolver) -> None:
        elem = LayoutElement(kind="text", content="Hello World")
        resolved, err = resolver.resolve_element(elem, ExportRecording(source_path="/x.mkv"))
        assert resolved.text == "Hello World"
        assert err is None

    def test_text_literal_preserves_style(self, resolver: FieldResolver) -> None:
        style = ElementStyle(bold=True, font_size=14)
        elem = LayoutElement(kind="text", content="Header", style=style)
        resolved, _ = resolver.resolve_element(elem, ExportRecording(source_path="/x.mkv"))
        assert resolved.style is style


# ---------------------------------------------------------------------------
# Technical fields — valid
# ---------------------------------------------------------------------------

class TestTechnicalFieldsValid:
    """{Extension}, {Size}, {Duration}, etc. resolve correctly."""

    @pytest.mark.parametrize("field_name,expected_substring", [
        ("Extension", ".mkv"),
        ("Container", "matroska"),
        ("Video Codec", "h264"),
        ("Audio Codec", "aac"),
        ("Audio Channels", "2"),
    ])
    def test_simple_fields(
        self, resolver: FieldResolver, full_recording: ExportRecording,
        field_name: str, expected_substring: str,
    ) -> None:
        elem = LayoutElement(kind="field", content=field_name, field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert expected_substring in resolved.text
        assert err is None

    def test_size(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        elem = LayoutElement(kind="field", content="Size", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert "GB" in resolved.text
        assert err is None

    def test_duration(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        elem = LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert resolved.text == "2:03:41"
        assert err is None

    def test_resolution(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        elem = LayoutElement(kind="field", content="Resolution", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert "1920" in resolved.text and "1080" in resolved.text
        assert err is None

    def test_frame_rate(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        elem = LayoutElement(kind="field", content="Frame Rate", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert "29.97" in resolved.text
        assert err is None

    def test_sample_rate(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        elem = LayoutElement(kind="field", content="Sample Rate", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert "48000" in resolved.text
        assert err is None

    def test_audio_bitrate(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        elem = LayoutElement(kind="field", content="Audio Bitrate", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert "kbps" in resolved.text
        assert err is None


# ---------------------------------------------------------------------------
# Archival fields — valid
# ---------------------------------------------------------------------------

class TestArchivalFieldsValid:
    """[Show], [Location], [Type], etc. resolve correctly."""

    @pytest.mark.parametrize("field_name,expected", [
        ("Title", "Munich 1997"),
        ("Year", "1997"),
        ("Date", "1997-03-15"),
        ("Venue", "Olympiahalle"),
        ("Location", "Munich"),
        ("Country", "Germany"),
        ("Tour", "European Tour"),
        ("Show", "Munich '97"),
        ("Source", "Audience"),
        ("Type", "Housecam"),
        ("Generation", "1st gen"),
        ("Quality", "Excellent"),
        ("Audio Source", "Soundboard"),
        ("Video Source", "Camera"),
        ("Master", "Yes"),
        ("Notes", "Test notes"),
        ("Description", "A test recording"),
    ])
    def test_all_archival_fields(
        self, resolver: FieldResolver, full_recording: ExportRecording,
        field_name: str, expected: str,
    ) -> None:
        elem = LayoutElement(kind="field", content=field_name, field_type=FieldType.ARCHIVAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert resolved.text == expected
        assert err is None


# ---------------------------------------------------------------------------
# None values → empty string
# ---------------------------------------------------------------------------

class TestNoneValues:
    """Fields that are None should resolve to empty string."""

    def test_technical_none(self, resolver: FieldResolver, empty_recording: ExportRecording) -> None:
        elem = LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, empty_recording)
        assert resolved.text == ""
        assert err is None

    def test_archival_none(self, resolver: FieldResolver, empty_recording: ExportRecording) -> None:
        elem = LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL)
        resolved, err = resolver.resolve_element(elem, empty_recording)
        assert resolved.text == ""
        assert err is None


# ---------------------------------------------------------------------------
# Unknown field
# ---------------------------------------------------------------------------

class TestUnknownField:
    """Unknown fields should resolve to empty string with an error."""

    def test_unknown_technical(self, resolver: FieldResolver) -> None:
        rec = ExportRecording(source_path="/x.mkv")
        elem = LayoutElement(kind="field", content="UnknownField", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, rec)
        assert resolved.text == ""
        assert err is not None
        assert "not registered" in err.message

    def test_unknown_archival(self, resolver: FieldResolver) -> None:
        rec = ExportRecording(source_path="/x.mkv")
        elem = LayoutElement(kind="field", content="FakeField", field_type=FieldType.ARCHIVAL)
        resolved, err = resolver.resolve_element(elem, rec)
        assert resolved.text == ""
        assert err is not None
        assert "not registered" in err.message


# ---------------------------------------------------------------------------
# Invalid syntax — semantic mismatch
# ---------------------------------------------------------------------------

class TestInvalidSyntax:
    """Using {} with archival or [] with technical is invalid."""

    def test_technical_show_is_invalid(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        """{Show} should NOT resolve — Show is archival."""
        elem = LayoutElement(kind="field", content="Show", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert resolved.text == ""
        assert err is not None
        assert "technical" in err.message.lower() or "archival" in err.message.lower()

    def test_archival_duration_is_invalid(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        """[Duration] should NOT resolve — Duration is technical."""
        elem = LayoutElement(kind="field", content="Duration", field_type=FieldType.ARCHIVAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert resolved.text == ""
        assert err is not None

    def test_technical_video_codec_is_invalid(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        """[Video Codec] as archival should fail — it's technical."""
        elem = LayoutElement(kind="field", content="Video Codec", field_type=FieldType.ARCHIVAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert resolved.text == ""
        assert err is not None

    def test_technical_show_error_message(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        elem = LayoutElement(kind="field", content="Show", field_type=FieldType.TECHNICAL)
        _, err = resolver.resolve_element(elem, full_recording)
        assert err is not None
        assert "Show" in err.field_name
        assert err.declared_type == FieldType.TECHNICAL

    def test_suggestion_type_with_technical_syntax(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        """{Type} should fail — Type is a suggestion field, not technical."""
        elem = LayoutElement(kind="field", content="Type", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert resolved.text == ""
        assert err is not None

    def test_suggestion_source_with_technical_syntax(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        """{Source} should fail — Source is a suggestion field, not technical."""
        elem = LayoutElement(kind="field", content="Source", field_type=FieldType.TECHNICAL)
        resolved, err = resolver.resolve_element(elem, full_recording)
        assert resolved.text == ""
        assert err is not None


# ---------------------------------------------------------------------------
# Line resolution
# ---------------------------------------------------------------------------

class TestLineResolution:
    """LayoutLine resolves all elements and collects errors."""

    def test_mixed_line(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        line = LayoutLine(elements=[
            LayoutElement(kind="text", content="Show: "),
            LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL),
            LayoutElement(kind="text", content=" | "),
            LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL),
        ])
        resolved, errors = resolver.resolve_line(line, full_recording)
        assert len(errors) == 0
        assert len(resolved.elements) == 4
        assert resolved.elements[0].text == "Show: "
        assert resolved.elements[1].text == "Munich '97"
        assert resolved.elements[2].text == " | "
        assert resolved.elements[3].text == "2:03:41"

    def test_line_with_invalid_field(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        line = LayoutLine(elements=[
            LayoutElement(kind="field", content="Show", field_type=FieldType.TECHNICAL),
        ])
        resolved, errors = resolver.resolve_line(line, full_recording)
        assert len(errors) == 1
        assert resolved.elements[0].text == ""

    def test_resolve_lines(self, resolver: FieldResolver, full_recording: ExportRecording) -> None:
        lines = [
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL),
            ]),
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL),
            ]),
        ]
        resolved, errors = resolver.resolve_lines(lines, full_recording)
        assert len(errors) == 0
        assert len(resolved) == 2
        assert resolved[0].elements[0].text == "Munich '97"
        assert resolved[1].elements[0].text == "2:03:41"


# ---------------------------------------------------------------------------
# Style preservation
# ---------------------------------------------------------------------------

class TestStylePreservation:
    """Styles on elements are preserved through resolution."""

    def test_style_on_field(self, resolver: FieldResolver) -> None:
        rec = ExportRecording(source_path="/x.mkv", show="My Show")
        style = ElementStyle(bold=True, color="#FF0000")
        elem = LayoutElement(
            kind="field", content="Show", field_type=FieldType.ARCHIVAL, style=style,
        )
        resolved, _ = resolver.resolve_element(elem, rec)
        assert resolved.style is style
        assert resolved.text == "My Show"

    def test_style_on_text(self, resolver: FieldResolver) -> None:
        rec = ExportRecording(source_path="/x.mkv")
        style = ElementStyle(italic=True)
        elem = LayoutElement(kind="text", content="---", style=style)
        resolved, _ = resolver.resolve_element(elem, rec)
        assert resolved.style is style
        assert resolved.text == "---"


# ---------------------------------------------------------------------------
# Screenshot token (audio suppression)
# ---------------------------------------------------------------------------

class TestScreenshotElement:
    """[Screenshot:N] passes through for video, vanishes for audio."""

    def test_audio_suppresses_video_passes_through(
        self, resolver: FieldResolver,
    ) -> None:
        elem = LayoutElement(kind="screenshot", content="3")

        audio = ExportRecording(
            source_path="/albums/live",
            is_audio=True,
            tracks=("/albums/live/01.mp3",),
        )
        resolved, err = resolver.resolve_element(elem, audio)
        assert err is None
        assert resolved.text == ""
        assert resolved.content == "0"
        # No screenshot directive → every renderer draws nothing.
        assert resolved.kind != "screenshot"

        video = ExportRecording(source_path="/shows/munich.mkv")
        resolved_video, err_video = resolver.resolve_element(elem, video)
        assert err_video is None
        assert resolved_video.kind == "screenshot"
        assert resolved_video.content == "3"
