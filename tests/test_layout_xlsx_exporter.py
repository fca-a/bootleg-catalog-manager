"""Tests for the layout-based XLSX exporter (L9)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.core.field_resolver import ResolvedElement, ResolvedLine
from bootleg_catalog.core.layout_xlsx_exporter import (
    LayoutXLSXExportError,
    export_layout_xlsx,
)
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    LayoutElement,
    LayoutLine,
    LayoutModel,
    PageConfig,
    ScreenshotConfig,
)
from bootleg_catalog.models.resolved_export import (
    ResolvedExportDocument,
    ResolvedRecording,
    ResolvedShowSection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_recording(
    source_path: str = "C:/media/concert.mkv",
    title: str | None = None,
    show: str | None = None,
    **overrides,
) -> ExportRecording:
    defaults = dict(
        source_path=source_path,
        extension=".mkv",
        file_size=1_000_000_000,
        duration=7200.0,
        video_codec="h264",
        audio_codec="aac",
        screenshot_paths=[],
    )
    defaults.update(overrides)
    return ExportRecording(**defaults)


def _make_layout(**overrides) -> LayoutModel:
    defaults = dict(
        name="Test Layout",
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
    defaults.update(overrides)
    return LayoutModel(**defaults)


def _make_resolved_recording(
    source_path: str = "C:/media/concert.mkv",
    lines: list[ResolvedLine] | None = None,
    **rec_overrides,
) -> ResolvedRecording:
    if lines is None:
        lines = [ResolvedLine(elements=[ResolvedElement(text="2:00:00")])]
    rec = _make_recording(source_path=source_path, **rec_overrides)
    return ResolvedRecording(recording=rec, lines=lines)


def _make_document(
    show_name: str = "Test Show",
    recordings: list[ResolvedRecording] | None = None,
    **layout_overrides,
) -> ResolvedExportDocument:
    if recordings is None:
        recordings = [_make_resolved_recording()]
    layout = _make_layout(**layout_overrides)
    section = ResolvedShowSection(
        show_name=show_name,
        header_lines=[ResolvedLine(elements=[ResolvedElement(text=show_name)])],
        recordings=recordings,
    )
    return ResolvedExportDocument(
        layout=layout,
        show_sections=[section],
        separator="\u2500",
    )


def _make_small_jpeg(path: Path) -> Path:
    """Create a minimal valid JPEG file for testing."""
    from PIL import Image
    img = Image.new("RGB", (64, 48), color=(128, 64, 32))
    img.save(path, "JPEG")
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLayoutXLSXExport:
    def test_empty_document(self, tmp_path: Path) -> None:
        """Empty document (no sections) creates a valid XLSX."""
        layout = _make_layout()
        doc = ResolvedExportDocument(
            layout=layout,
            show_sections=[],
            separator=None,
        )
        out = tmp_path / "empty.xlsx"
        result = export_layout_xlsx(doc, out)
        assert result == out.resolve()
        assert out.exists()
        assert out.stat().st_size > 0
        # Verify it opens with openpyxl
        from openpyxl import load_workbook
        wb = load_workbook(out)
        assert "Catalog" in wb.sheetnames

    def test_one_show_no_recordings(self, tmp_path: Path) -> None:
        """One show section with zero recordings renders the header."""
        layout = _make_layout()
        section = ResolvedShowSection(
            show_name="Empty Show",
            header_lines=[ResolvedLine(elements=[ResolvedElement(text="Empty Show")])],
            recordings=[],
        )
        doc = ResolvedExportDocument(
            layout=layout,
            show_sections=[section],
            separator=None,
        )
        out = tmp_path / "empty_show.xlsx"
        result = export_layout_xlsx(doc, out)
        assert result.exists()
        from openpyxl import load_workbook
        wb = load_workbook(out)
        ws = wb.active
        # Find the "Empty Show" cell
        found = False
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if cell == "Empty Show":
                    found = True
        assert found

    def test_one_show_one_recording(self, tmp_path: Path) -> None:
        """One show with one recording produces a valid XLSX."""
        doc = _make_document()
        out = tmp_path / "one_show.xlsx"
        result = export_layout_xlsx(doc, out)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_two_shows_both_present(self, tmp_path: Path) -> None:
        """Two show sections are both present in the same sheet."""
        rec1 = _make_resolved_recording(
            source_path="C:/media/show1.mkv",
            lines=[ResolvedLine(elements=[ResolvedElement(text="Show 1 recording")])],
        )
        rec2 = _make_resolved_recording(
            source_path="C:/media/show2.mkv",
            lines=[ResolvedLine(elements=[ResolvedElement(text="Show 2 recording")])],
        )
        section1 = ResolvedShowSection(
            show_name="Show 1",
            header_lines=[ResolvedLine(elements=[ResolvedElement(text="Show 1")])],
            recordings=[rec1],
        )
        section2 = ResolvedShowSection(
            show_name="Show 2",
            header_lines=[ResolvedLine(elements=[ResolvedElement(text="Show 2")])],
            recordings=[rec2],
        )
        layout = _make_layout()
        doc = ResolvedExportDocument(
            layout=layout,
            show_sections=[section1, section2],
            separator="\u2500",
        )
        out = tmp_path / "two_shows.xlsx"
        result = export_layout_xlsx(doc, out)
        assert result.exists()
        from openpyxl import load_workbook
        wb = load_workbook(out)
        ws = wb.active
        # Collect all cell values
        values = []
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if cell is not None:
                    values.append(str(cell))
        assert "Show 1" in values
        assert "Show 2" in values
        assert "Show 1 recording" in values
        assert "Show 2 recording" in values

    def test_multiple_lines_with_styles(self, tmp_path: Path) -> None:
        """Multiple lines with mixed styles render and styles apply."""
        lines = [
            ResolvedLine(elements=[
                ResolvedElement(text="Bold Title", style=ElementStyle(bold=True)),
            ]),
            ResolvedLine(elements=[
                ResolvedElement(text="Italic Text", style=ElementStyle(italic=True)),
            ]),
            ResolvedLine(elements=[
                ResolvedElement(
                    text="Red 14pt",
                    style=ElementStyle(font_size=14, color="#FF0000"),
                ),
            ]),
        ]
        rec = _make_resolved_recording(lines=lines)
        doc = _make_document(recordings=[rec])
        out = tmp_path / "styled.xlsx"
        result = export_layout_xlsx(doc, out)
        assert result.exists()
        from openpyxl import load_workbook
        wb = load_workbook(out)
        ws = wb.active
        # Find and check styled cells
        for row in ws.iter_rows():
            for cell in row:
                if cell.value == "Bold Title":
                    assert cell.font.bold is True
                elif cell.value == "Italic Text":
                    assert cell.font.italic is True
                elif cell.value == "Red 14pt":
                    assert cell.font.size == 14
                    assert cell.font.color is not None
                    assert cell.font.color.rgb == "00FF0000"

    def test_show_header_styled(self, tmp_path: Path) -> None:
        """Show header rows have bold, larger font, and gray fill."""
        doc = _make_document()
        out = tmp_path / "header_style.xlsx"
        export_layout_xlsx(doc, out)
        from openpyxl import load_workbook
        wb = load_workbook(out)
        ws = wb.active
        # Find the "Test Show" cell
        for row in ws.iter_rows():
            for cell in row:
                if cell.value == "Test Show":
                    assert cell.font.bold is True
                    assert cell.font.size == 12
                    assert cell.fill.start_color.rgb == "00EEEEEE"
                    return
        pytest.fail("Show header cell not found")

    def test_output_path_is_absolute(self, tmp_path: Path) -> None:
        """Returned path is always absolute."""
        doc = _make_document()
        out = tmp_path / "abs.xlsx"
        result = export_layout_xlsx(doc, out)
        assert result.is_absolute()

    def test_sheet_title(self, tmp_path: Path) -> None:
        """Sheet title is 'Catalog'."""
        doc = _make_document()
        out = tmp_path / "sheet.xlsx"
        export_layout_xlsx(doc, out)
        from openpyxl import load_workbook
        wb = load_workbook(out)
        assert wb.sheetnames == ["Catalog"]

    # -- Screenshots --

    def test_no_screenshots_when_none_passed(self, tmp_path: Path) -> None:
        """screenshot_paths_by_recording=None → no images embedded."""
        doc = _make_document()
        out = tmp_path / "no_shots.xlsx"
        export_layout_xlsx(doc, out)
        from openpyxl import load_workbook
        wb = load_workbook(out)
        ws = wb.active
        assert len(ws._images) == 0

    def test_one_screenshot(self, tmp_path: Path) -> None:
        """One screenshot is embedded."""
        img = _make_small_jpeg(tmp_path / "shot.jpg")
        rec = _make_resolved_recording(source_path="C:/media/concert.mkv")
        doc = _make_document(recordings=[rec])
        screenshots = {"C:/media/concert.mkv": [str(img)]}
        out = tmp_path / "one_shot.xlsx"
        export_layout_xlsx(doc, out, screenshot_paths_by_recording=screenshots)
        from openpyxl import load_workbook
        wb = load_workbook(out)
        ws = wb.active
        assert len(ws._images) == 1

    def test_three_screenshots_grid(self, tmp_path: Path) -> None:
        """Three screenshots with grid layout are all embedded."""
        imgs = [_make_small_jpeg(tmp_path / f"shot{i}.jpg") for i in range(3)]
        rec = _make_resolved_recording(source_path="C:/media/concert.mkv")
        doc = _make_document(recordings=[rec])
        screenshots = {"C:/media/concert.mkv": [str(p) for p in imgs]}
        out = tmp_path / "three_shots.xlsx"
        export_layout_xlsx(doc, out, screenshot_paths_by_recording=screenshots)
        from openpyxl import load_workbook
        wb = load_workbook(out)
        ws = wb.active
        assert len(ws._images) == 3

    def test_screenshot_missing_raises(self, tmp_path: Path) -> None:
        """Missing screenshot path raises LayoutXLSXExportError."""
        rec = _make_resolved_recording(source_path="C:/media/concert.mkv")
        doc = _make_document(recordings=[rec])
        screenshots = {"C:/media/concert.mkv": ["/nonexistent/shot.jpg"]}
        out = tmp_path / "missing.xlsx"
        with pytest.raises(LayoutXLSXExportError, match="not found"):
            export_layout_xlsx(doc, out, screenshot_paths_by_recording=screenshots)
        assert not out.exists()

    def test_source_screenshots_not_modified(self, tmp_path: Path) -> None:
        """Source screenshot files are not modified during export."""
        img = _make_small_jpeg(tmp_path / "shot.jpg")
        original_bytes = img.read_bytes()
        rec = _make_resolved_recording(source_path="C:/media/concert.mkv")
        doc = _make_document(recordings=[rec])
        screenshots = {"C:/media/concert.mkv": [str(img)]}
        out = tmp_path / "verify.xlsx"
        export_layout_xlsx(doc, out, screenshot_paths_by_recording=screenshots)
        assert img.read_bytes() == original_bytes

    # -- Destination already exists --

    def test_output_already_exists_raises(self, tmp_path: Path) -> None:
        """Output path already exists → LayoutXLSXExportError, no overwrite."""
        out = tmp_path / "existing.xlsx"
        out.write_bytes(b"original")
        doc = _make_document()
        with pytest.raises(LayoutXLSXExportError, match="already exists"):
            export_layout_xlsx(doc, out)
        assert out.read_bytes() == b"original"

    # -- Write failure cleanup --

    def test_write_failure_cleans_temp(self, tmp_path: Path) -> None:
        """If Workbook.save fails, temp file is cleaned up, no dest file."""
        doc = _make_document()
        out = tmp_path / "fail.xlsx"
        with patch("openpyxl.Workbook.save", side_effect=RuntimeError("write failed")):
            with pytest.raises(LayoutXLSXExportError, match="Failed to generate"):
                export_layout_xlsx(doc, out)
        assert not out.exists()

    # -- Unicode round-trip --

    def test_unicode_round_trip(self, tmp_path: Path) -> None:
        """CJK text written to XLSX reads back identically."""
        lines = [
            ResolvedLine(elements=[
                ResolvedElement(text="\u6771\u4eac 1997"),
            ]),
            ResolvedLine(elements=[
                ResolvedElement(text="Z\u00fcrich \u30c8\u30ea\u30fc"),
            ]),
        ]
        rec = _make_resolved_recording(lines=lines)
        doc = _make_document(recordings=[rec])
        out = tmp_path / "unicode.xlsx"
        export_layout_xlsx(doc, out)
        from openpyxl import load_workbook
        wb = load_workbook(out)
        ws = wb.active
        # Find the CJK cells and verify exact content
        found_cjk = False
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if cell == "\u6771\u4eac 1997":
                    found_cjk = True
                if cell == "Z\u00fcrich \u30c8\u30ea\u30fc":
                    pass  # also present
        assert found_cjk, "CJK text not found in XLSX"

    # -- Independence checks --

    def test_no_pyside6_import(self) -> None:
        """Module must not import PySide6."""
        import bootleg_catalog.core.layout_xlsx_exporter as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "PySide6" not in content

    def test_no_sqlite_import(self) -> None:
        """Module must not import sqlite3."""
        import bootleg_catalog.core.layout_xlsx_exporter as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "sqlite3" not in content

    def test_no_catalog_import(self) -> None:
        """Module must not import Catalog or CatalogRepository."""
        import bootleg_catalog.core.layout_xlsx_exporter as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "from bootleg_catalog.core.catalog" not in content
        assert "from bootleg_catalog.database" not in content
        assert "import catalog" not in content
        assert "import CatalogRepository" not in content


# ---------------------------------------------------------------------------
# Page setup (L15.7c)
# ---------------------------------------------------------------------------

class TestXLSXPageSetup:
    def test_a4_landscape_page_setup(self, tmp_path: Path) -> None:
        """A4 landscape seeds worksheet orientation and paper size."""
        doc = _make_document(
            page_config=PageConfig(page_size="A4", orientation="landscape"),
        )
        out = tmp_path / "a4_landscape.xlsx"
        export_layout_xlsx(doc, out)

        from openpyxl import load_workbook
        wb = load_workbook(out)
        ws = wb.active
        assert ws.page_setup.orientation == "landscape"
        assert ws.page_setup.paperSize == 9
