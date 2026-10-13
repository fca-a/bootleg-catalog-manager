"""Tests for the resolved export document models."""

from __future__ import annotations

import pytest

from bootleg_catalog.core.field_resolver import ResolvedElement, ResolvedLine
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FieldType,
    LayoutElement,
    LayoutLine,
    LayoutModel,
)
from bootleg_catalog.models.resolved_export import (
    ResolvedExportDocument,
    ResolvedRecording,
    ResolvedShowSection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _elem(text: str, bold: bool = False) -> ResolvedElement:
    style = ElementStyle(bold=bold) if bold else None
    return ResolvedElement(text=text, style=style)


def _line(*elements: ResolvedElement) -> ResolvedLine:
    return ResolvedLine(elements=list(elements))


def _rec(path: str = "/test.mkv", **kwargs) -> ExportRecording:
    return ExportRecording(source_path=path, **kwargs)


# ---------------------------------------------------------------------------
# ResolvedElement
# ---------------------------------------------------------------------------

class TestResolvedElement:
    def test_construction(self) -> None:
        e = ResolvedElement(text="hello")
        assert e.text == "hello"
        assert e.style is None

    def test_with_style(self) -> None:
        style = ElementStyle(bold=True, font_size=14)
        e = ResolvedElement(text="hello", style=style)
        assert e.style is style
        assert e.style.bold is True

    def test_empty_text(self) -> None:
        e = ResolvedElement(text="")
        assert e.text == ""


# ---------------------------------------------------------------------------
# ResolvedLine
# ---------------------------------------------------------------------------

class TestResolvedLine:
    def test_construction(self) -> None:
        line = ResolvedLine(elements=[_elem("A"), _elem("B")])
        assert len(line.elements) == 2
        assert line.elements[0].text == "A"
        assert line.elements[1].text == "B"

    def test_empty_line(self) -> None:
        line = ResolvedLine(elements=[])
        assert line.elements == []


# ---------------------------------------------------------------------------
# ResolvedRecording
# ---------------------------------------------------------------------------

class TestResolvedRecording:
    def test_construction(self) -> None:
        rec = _rec("/a.mkv", show="My Show")
        lines = [_line(_elem("line1")), _line(_elem("line2"))]
        rr = ResolvedRecording(recording=rec, lines=lines)
        assert rr.recording is rec
        assert len(rr.lines) == 2

    def test_preserves_recording_reference(self) -> None:
        rec = _rec("/a.mkv")
        rr = ResolvedRecording(recording=rec, lines=[])
        assert rr.recording.source_path == "/a.mkv"


# ---------------------------------------------------------------------------
# ResolvedShowSection
# ---------------------------------------------------------------------------

class TestResolvedShowSection:
    def test_construction(self) -> None:
        rec = _rec("/a.mkv")
        header = [_line(_elem("Munich '97"))]
        recordings = [ResolvedRecording(recording=rec, lines=[])]
        section = ResolvedShowSection(
            show_name="Munich '97",
            header_lines=header,
            recordings=recordings,
        )
        assert section.show_name == "Munich '97"
        assert len(section.header_lines) == 1
        assert len(section.recordings) == 1

    def test_empty_header(self) -> None:
        section = ResolvedShowSection(
            show_name="Test",
            header_lines=[],
            recordings=[],
        )
        assert section.header_lines == []
        assert section.recordings == []


# ---------------------------------------------------------------------------
# ResolvedExportDocument
# ---------------------------------------------------------------------------

class TestResolvedExportDocument:
    def test_construction(self) -> None:
        layout = LayoutModel(name="L", show_header=[], recording_body=[])
        doc = ResolvedExportDocument(
            layout=layout,
            show_sections=[],
            separator="\u2500",
        )
        assert doc.layout is layout
        assert doc.show_sections == []
        assert doc.separator == "\u2500"

    def test_separator_none(self) -> None:
        layout = LayoutModel(name="L", show_header=[], recording_body=[], separator=None)
        doc = ResolvedExportDocument(layout=layout, show_sections=[], separator=None)
        assert doc.separator is None

    def test_layout_preserved(self) -> None:
        layout = LayoutModel(name="My Layout", show_header=[], recording_body=[])
        doc = ResolvedExportDocument(layout=layout, show_sections=[], separator="-")
        assert doc.layout.name == "My Layout"
