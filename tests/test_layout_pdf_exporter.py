"""Tests for the layout-based PDF exporter (L8)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bootleg_catalog.core.field_resolver import ResolvedElement, ResolvedLine
from bootleg_catalog.core.layout_pdf_exporter import (
    LayoutPDFExportError,
    export_layout_pdf,
    resolve_logo_slot,
    resolve_text_slot,
)
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FooterConfig,
    HeaderConfig,
    LayoutElement,
    LayoutLine,
    LayoutModel,
    LogoSlot,
    PageConfig,
    ScreenshotConfig,
    TextSlot,
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


def _make_small_png(path: Path, size: tuple[int, int] = (100, 50)) -> Path:
    """Create a minimal valid PNG file for logo tests (L15.7f.3)."""
    from PIL import Image
    img = Image.new("RGB", size, color=(10, 20, 30))
    img.save(path, "PNG")
    return path


def _make_long_recording(num_lines: int, **rec_overrides) -> ResolvedRecording:
    """A recording whose body renders many lines (forces page breaks).

    Each resolved line becomes one 5mm-high multi_cell row; ~53 rows fit
    on an A4 page with 15mm margins, so 120 lines span 3 pages.
    """
    lines = [
        ResolvedLine(elements=[ResolvedElement(text=f"Line {i:03d}")])
        for i in range(num_lines)
    ]
    return _make_resolved_recording(lines=lines, **rec_overrides)


def _export_capture_pdf(
    document: ResolvedExportDocument,
    out_path: Path,
    **kwargs,
) -> tuple[Path, "object"]:
    """Run export_layout_pdf, returning (path, the _LayoutPDF instance).

    The exporter's header/footer counters live on the FPDF subclass it
    builds internally; this helper patches the module-level name so tests
    can introspect the instance after the export finishes.
    """
    import bootleg_catalog.core.layout_pdf_exporter as exporter_mod

    captured: dict = {}
    real_cls = exporter_mod._LayoutPDF

    def _factory(*args, **ctor_kwargs):
        instance = real_cls(*args, **ctor_kwargs)
        captured["pdf"] = instance
        return instance

    with patch.object(exporter_mod, "_LayoutPDF", side_effect=_factory):
        result = exporter_mod.export_layout_pdf(document, out_path, **kwargs)
    return result, captured["pdf"]


def _export_with_method_spies(
    document: ResolvedExportDocument,
    out_path: Path,
    *method_names: str,
    **export_kwargs,
) -> dict[str, list[tuple]]:
    """Export with call-through spies on the named ``_LayoutPDF`` methods.

    Same instance-spy pattern as
    ``TestScreenshotVisualConfig._export_with_spy``, lifted to module
    level and given export kwargs, for the bug-fix tests: the factory
    patch shadows each method on the freshly constructed instance with
    a recorder that still calls through, so every ``set_font`` /
    ``cell`` / ``multi_cell`` made during the export lands in the
    returned mapping as ``(args, kwargs)``.
    """
    import bootleg_catalog.core.layout_pdf_exporter as exporter_mod

    calls: dict[str, list[tuple]] = {name: [] for name in method_names}
    real_cls = exporter_mod._LayoutPDF

    def _factory(*args, **ctor_kwargs):
        instance = real_cls(*args, **ctor_kwargs)
        for name in method_names:
            original = getattr(instance, name)

            def spy(*m_args, _name=name, _original=original, **m_kwargs):
                calls[_name].append((m_args, m_kwargs))
                return _original(*m_args, **m_kwargs)

            setattr(instance, name, spy)
        return instance

    with patch.object(exporter_mod, "_LayoutPDF", side_effect=_factory):
        exporter_mod.export_layout_pdf(document, out_path, **export_kwargs)
    return calls


def _captured_texts(calls: dict[str, list[tuple]]) -> list[str]:
    """Every text argument drawn through the spied ``cell``/``multi_cell``."""
    texts: list[str] = []
    for name in ("cell", "multi_cell"):
        for args, kwargs in calls.get(name, []):
            if len(args) > 2 and isinstance(args[2], str):
                texts.append(args[2])
            text_kwarg = kwargs.get("text")
            if isinstance(text_kwarg, str):
                texts.append(text_kwarg)
    return texts


def _make_two_section_document(
    separator: str | None,
    **layout_overrides,
) -> ResolvedExportDocument:
    """A document with two show sections.

    The separator only draws *between* sections (``section_idx > 0``),
    so a single-section document would never exercise it.
    """
    sections = [
        ResolvedShowSection(
            show_name=f"Show {i}",
            header_lines=[
                ResolvedLine(elements=[ResolvedElement(text=f"Show {i}")])
            ],
            recordings=[
                _make_resolved_recording(source_path=f"C:/media/show{i}.mkv")
            ],
        )
        for i in (1, 2)
    ]
    return ResolvedExportDocument(
        layout=_make_layout(**layout_overrides),
        show_sections=sections,
        separator=separator,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLayoutPDFExport:
    def test_empty_document(self, tmp_path: Path) -> None:
        """Empty document (no sections) creates a valid PDF."""
        layout = _make_layout()
        doc = ResolvedExportDocument(
            layout=layout,
            show_sections=[],
            separator=None,
        )
        out = tmp_path / "empty.pdf"
        result = export_layout_pdf(doc, out)
        assert result == out.resolve()
        assert out.exists()
        assert out.stat().st_size > 0

    def test_one_show_no_recordings(self, tmp_path: Path) -> None:
        """One show section with zero recordings still renders the header."""
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
        out = tmp_path / "empty_show.pdf"
        result = export_layout_pdf(doc, out)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_one_show_one_recording(self, tmp_path: Path) -> None:
        """One show with one recording produces a non-empty PDF."""
        doc = _make_document()
        out = tmp_path / "one_show.pdf"
        result = export_layout_pdf(doc, out)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_two_shows(self, tmp_path: Path) -> None:
        """Two show sections are both rendered."""
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
        out = tmp_path / "two_shows.pdf"
        result = export_layout_pdf(doc, out)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_two_shows_no_forced_page_break(self, tmp_path: Path) -> None:
        """Two short shows that fit one page call add_page only once.

        Bug: the section loop called ``add_page()`` unconditionally
        before every section after the first, forcing each show onto a
        fresh page.  Sections must flow on the current page; only
        fpdf's auto page break may add pages.
        """
        doc = _make_two_section_document("\u2500")
        out = tmp_path / "two_shows_one_page.pdf"

        calls = _export_with_method_spies(doc, out, "add_page")

        assert len(calls["add_page"]) == 1  # the initial page only

    def test_recording_with_multiple_lines_and_styles(self, tmp_path: Path) -> None:
        """Multiple lines with mixed styles render without error."""
        lines = [
            ResolvedLine(elements=[
                ResolvedElement(text="Title: ", style=ElementStyle(bold=True)),
                ResolvedElement(text="My Bootleg"),
            ]),
            ResolvedLine(elements=[
                ResolvedElement(text="Duration: ", style=ElementStyle(italic=True)),
                ResolvedElement(text="2:00:00"),
            ]),
            ResolvedLine(elements=[
                ResolvedElement(
                    text="IMPORTANT",
                    style=ElementStyle(font_size=14, bold=True, color="#FF0000"),
                ),
            ]),
        ]
        rec = _make_resolved_recording(lines=lines)
        doc = _make_document(recordings=[rec])
        out = tmp_path / "styled.pdf"
        result = export_layout_pdf(doc, out)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_output_path_is_absolute(self, tmp_path: Path) -> None:
        """Returned path is always absolute."""
        doc = _make_document()
        out = tmp_path / "abs.pdf"
        result = export_layout_pdf(doc, out)
        assert result.is_absolute()

    # -- Screenshots --

    def test_no_screenshots_when_none_passed(self, tmp_path: Path) -> None:
        """screenshot_paths_by_recording=None → no screenshots rendered."""
        doc = _make_document()
        out = tmp_path / "no_screenshots.pdf"
        result = export_layout_pdf(doc, out)
        assert result.exists()

    def test_one_screenshot(self, tmp_path: Path) -> None:
        """One screenshot is rendered."""
        img = _make_small_jpeg(tmp_path / "shot.jpg")
        rec = _make_resolved_recording(source_path="C:/media/concert.mkv")
        doc = _make_document(recordings=[rec])
        screenshots = {"C:/media/concert.mkv": [str(img)]}
        out = tmp_path / "one_shot.pdf"
        result = export_layout_pdf(
            doc, out, screenshot_paths_by_recording=screenshots,
        )
        assert result.exists()
        assert result.stat().st_size > 0

    def test_three_screenshots_grid(self, tmp_path: Path) -> None:
        """Three screenshots with grid layout render."""
        imgs = [_make_small_jpeg(tmp_path / f"shot{i}.jpg") for i in range(3)]
        rec = _make_resolved_recording(source_path="C:/media/concert.mkv")
        doc = _make_document(recordings=[rec])
        screenshots = {"C:/media/concert.mkv": [str(p) for p in imgs]}
        out = tmp_path / "three_shots.pdf"
        result = export_layout_pdf(
            doc, out, screenshot_paths_by_recording=screenshots,
        )
        assert result.exists()

    def test_screenshot_missing_raises(self, tmp_path: Path) -> None:
        """Missing screenshot path raises LayoutPDFExportError."""
        rec = _make_resolved_recording(source_path="C:/media/concert.mkv")
        doc = _make_document(recordings=[rec])
        screenshots = {"C:/media/concert.mkv": ["/nonexistent/shot.jpg"]}
        out = tmp_path / "missing_shot.pdf"
        with pytest.raises(LayoutPDFExportError, match="not found"):
            export_layout_pdf(doc, out, screenshot_paths_by_recording=screenshots)
        assert not out.exists()

    def test_corrupt_screenshot_raises_layout_pdf_error(self, tmp_path: Path) -> None:
        """Bug A: an existing-but-unreadable image surfaces as
        LayoutPDFExportError (with the file named), never a raw PIL error
        that would escape every handler."""
        corrupt = tmp_path / "capa.jpg"
        corrupt.write_text("this is not a real jpeg")
        rec = _make_resolved_recording(source_path="C:/media/concert.mkv")
        doc = _make_document(recordings=[rec])
        screenshots = {"C:/media/concert.mkv": [str(corrupt)]}
        out = tmp_path / "corrupt_shot.pdf"
        with pytest.raises(LayoutPDFExportError) as excinfo:
            export_layout_pdf(doc, out, screenshot_paths_by_recording=screenshots)
        message = str(excinfo.value)
        assert "not a valid image" in message
        assert str(corrupt) in message
        assert not out.exists()

    def test_source_screenshots_not_modified(self, tmp_path: Path) -> None:
        """Source screenshot files are not modified during export."""
        img = _make_small_jpeg(tmp_path / "shot.jpg")
        original_bytes = img.read_bytes()
        rec = _make_resolved_recording(source_path="C:/media/concert.mkv")
        doc = _make_document(recordings=[rec])
        screenshots = {"C:/media/concert.mkv": [str(img)]}
        out = tmp_path / "verify.pdf"
        export_layout_pdf(doc, out, screenshot_paths_by_recording=screenshots)
        assert img.read_bytes() == original_bytes

    # -- Destination already exists --

    def test_output_already_exists_raises(self, tmp_path: Path) -> None:
        """Output path already exists → LayoutPDFExportError, no overwrite."""
        out = tmp_path / "existing.pdf"
        out.write_bytes(b"original")
        doc = _make_document()
        with pytest.raises(LayoutPDFExportError, match="already exists"):
            export_layout_pdf(doc, out)
        assert out.read_bytes() == b"original"

    # -- Write failure cleanup --

    def test_write_failure_cleans_temp(self, tmp_path: Path) -> None:
        """If pdf.output fails, temp file is cleaned up, no dest file."""
        doc = _make_document()
        out = tmp_path / "fail.pdf"
        with patch("fpdf.FPDF.output", side_effect=RuntimeError("write failed")):
            with pytest.raises(LayoutPDFExportError, match="Failed to generate"):
                export_layout_pdf(doc, out)
        assert not out.exists()

    # -- Independence checks --

    def test_no_pyside6_import(self) -> None:
        """Module must not import PySide6."""
        import bootleg_catalog.core.layout_pdf_exporter as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "PySide6" not in content

    def test_no_sqlite_import(self) -> None:
        """Module must not import sqlite3."""
        import bootleg_catalog.core.layout_pdf_exporter as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "sqlite3" not in content

    def test_no_catalog_import(self) -> None:
        """Module must not import Catalog or CatalogRepository."""
        import bootleg_catalog.core.layout_pdf_exporter as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "from bootleg_catalog.core.catalog" not in content
        assert "from bootleg_catalog.database" not in content
        assert "import catalog" not in content
        assert "import CatalogRepository" not in content

    # -- Non-latin-1 text validation --

    def test_non_latin1_text_raises(self, tmp_path: Path) -> None:
        """ResolvedElement text with non-latin-1 chars raises error."""
        lines = [ResolvedLine(elements=[ResolvedElement(text="\u6771\u4eac 1997")])]
        rec = _make_resolved_recording(lines=lines)
        doc = _make_document(recordings=[rec])
        out = tmp_path / "unicode.pdf"
        with pytest.raises(LayoutPDFExportError, match="non-latin-1"):
            export_layout_pdf(doc, out)
        assert not out.exists()

    def test_latin1_compatible_text_succeeds(self, tmp_path: Path) -> None:
        """Latin-1 compatible text (e.g. München, Zürich) exports fine."""
        lines = [
            ResolvedLine(elements=[ResolvedElement(text="M\u00fcnchen")]),
            ResolvedLine(elements=[ResolvedElement(text="Z\u00fcrich")]),
        ]
        rec = _make_resolved_recording(lines=lines)
        doc = _make_document(recordings=[rec])
        out = tmp_path / "latin1.pdf"
        result = export_layout_pdf(doc, out)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_separator_non_latin1_still_succeeds(self, tmp_path: Path) -> None:
        """Separator outside latin-1 is replaced with ASCII, export succeeds."""
        doc = _make_document()
        # Default separator is \u2500 (─), which is non-latin-1.
        # This should NOT cause a failure — separators are handled separately.
        out = tmp_path / "sep.pdf"
        result = export_layout_pdf(doc, out)
        assert result.exists()

    def test_error_message_contains_sample(self, tmp_path: Path) -> None:
        """Error message includes at least one offending text sample."""
        lines = [ResolvedLine(elements=[ResolvedElement(text="\u6771\u4eac \u30c8\u30ad\u30aa")])]
        rec = _make_resolved_recording(lines=lines)
        doc = _make_document(recordings=[rec])
        out = tmp_path / "sample.pdf"
        with pytest.raises(LayoutPDFExportError) as exc_info:
            export_layout_pdf(doc, out)
        msg = str(exc_info.value)
        assert "\u6771\u4eac" in msg  # Contains the offending text

    def test_non_latin1_in_header_raises(self, tmp_path: Path) -> None:
        """Non-latin-1 in show header also triggers validation."""
        section = ResolvedShowSection(
            show_name="\u6771\u4eac",
            header_lines=[ResolvedLine(elements=[ResolvedElement(text="\u6771\u4eac")])],
            recordings=[_make_resolved_recording()],
        )
        layout = _make_layout()
        doc = ResolvedExportDocument(
            layout=layout,
            show_sections=[section],
            separator=None,
        )
        out = tmp_path / "header.pdf"
        with pytest.raises(LayoutPDFExportError, match="non-latin-1"):
            export_layout_pdf(doc, out)
        assert not out.exists()

    def test_encoding_validated_before_screenshots(self, tmp_path: Path) -> None:
        """Encoding error takes priority over missing screenshot error."""
        lines = [ResolvedLine(elements=[ResolvedElement(text="\u6771\u4eac")])]
        rec = _make_resolved_recording(lines=lines)
        doc = _make_document(recordings=[rec])
        screenshots = {"C:/media/concert.mkv": ["/nonexistent/shot.jpg"]}
        out = tmp_path / "priority.pdf"
        # Encoding error should be raised, not screenshot error
        with pytest.raises(LayoutPDFExportError, match="non-latin-1"):
            export_layout_pdf(doc, out, screenshot_paths_by_recording=screenshots)


# ---------------------------------------------------------------------------
# Page geometry (L15.7c)
# ---------------------------------------------------------------------------

class TestPDFPageGeometry:
    def test_letter_landscape_page_size(self, tmp_path: Path) -> None:
        """Letter landscape exports a landscape Letter mediabox (L15.7c).

        The exporter now builds a module-level _LayoutPDF subclass, so the
        old ``patch("fpdf.FPDF", ...)`` capture no longer intercepts; the
        instance is captured via _export_capture_pdf instead.  The
        assertions themselves are unchanged: the writer's recorded page
        size and the produced file's MediaBox must both be landscape Letter.
        """
        import re

        doc = _make_document(
            page_config=PageConfig(page_size="Letter", orientation="landscape"),
        )
        out = tmp_path / "letter_landscape.pdf"
        result, pdf = _export_capture_pdf(doc, out)

        assert result.exists()
        # The writer was handed a landscape Letter page format (mm).
        assert pdf.w == pytest.approx(279.0)
        assert pdf.h == pytest.approx(216.0)

        # The produced file's MediaBox agrees: landscape, Letter in points.
        media_box = re.search(
            rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]",
            result.read_bytes(),
        )
        assert media_box is not None
        width_pt = float(media_box.group(1))
        height_pt = float(media_box.group(2))
        assert width_pt > height_pt  # landscape
        assert abs(width_pt - 279.0 / 25.4 * 72) < 1.0
        assert abs(height_pt - 216.0 / 25.4 * 72) < 1.0


# ---------------------------------------------------------------------------
# Show-section header rendering (L15.8.2)
# ---------------------------------------------------------------------------

class TestShowHeaderRendering:
    def test_show_header_text_reaches_pdf(self, tmp_path: Path) -> None:
        """The section's resolved header_lines text is written to the PDF.

        L15.8.2: the user reported the Show Header section rendering
        unresolved markers/empties in the exported PDF while the body
        rendered fine.  This pins the exporter's actual output: every
        rendered multi_cell/cell string is recorded via a call-through
        spy on the captured instance.
        """
        import bootleg_catalog.core.layout_pdf_exporter as exporter_mod

        rec = _make_resolved_recording(
            lines=[ResolvedLine(elements=[ResolvedElement(text="BODYMARKER8")])],
        )
        section = ResolvedShowSection(
            show_name="SHOWMARKER7",
            header_lines=[
                ResolvedLine(elements=[ResolvedElement(text="SHOWMARKER7")]),
                ResolvedLine(elements=[ResolvedElement(text="HDR2010")]),
            ],
            recordings=[rec],
        )
        doc = ResolvedExportDocument(
            layout=_make_layout(),
            show_sections=[section],
            separator="─",
        )

        calls: list[str] = []
        real_cls = exporter_mod._LayoutPDF

        def _factory(*args, **ctor_kwargs):
            instance = real_cls(*args, **ctor_kwargs)
            for method_name in ("multi_cell", "cell"):
                original = getattr(instance, method_name)

                def spy(*m_args, _original=original, **m_kwargs):
                    calls.append(
                        " ".join(str(a) for a in m_args)
                        + " "
                        + " ".join(str(v) for v in m_kwargs.values())
                    )
                    return _original(*m_args, **m_kwargs)

                setattr(instance, method_name, spy)
            return instance

        out = tmp_path / "show_header.pdf"
        with patch.object(exporter_mod, "_LayoutPDF", side_effect=_factory):
            result = exporter_mod.export_layout_pdf(doc, out)

        assert result.exists()
        joined = "\n".join(calls)
        # The show-header lines reach the PDF output...
        assert "SHOWMARKER7" in joined
        assert "HDR2010" in joined
        # ...alongside the body lines.
        assert "BODYMARKER8" in joined


# ---------------------------------------------------------------------------
# Per-element style rendering (L15.8.4)
# ---------------------------------------------------------------------------

class TestElementStyleRendering:
    """A ResolvedElement's bold/italic style reaches the fpdf font.

    Spy choice: a call-through instance spy on ``set_font`` installed
    via the module-level ``_LayoutPDF`` factory patch (the same pattern
    as :class:`TestShowHeaderRendering`).  ``_apply_style`` calls
    ``set_font(family, style, size)`` positionally, so the fpdf style
    string is the second positional arg.
    """

    def _export_with_set_font_spy(
        self, doc: ResolvedExportDocument, out_path: Path,
    ) -> list[tuple]:
        import bootleg_catalog.core.layout_pdf_exporter as exporter_mod

        calls: list[tuple] = []
        real_cls = exporter_mod._LayoutPDF

        def _factory(*args, **ctor_kwargs):
            instance = real_cls(*args, **ctor_kwargs)
            original = instance.set_font

            def spy(*m_args, **m_kwargs):
                calls.append((m_args, m_kwargs))
                return original(*m_args, **m_kwargs)

            instance.set_font = spy
            return instance

        with patch.object(exporter_mod, "_LayoutPDF", side_effect=_factory):
            exporter_mod.export_layout_pdf(doc, out_path)
        return calls

    def test_bold_element_uses_fpdf_bold_style(self, tmp_path: Path) -> None:
        """A bold body element is drawn with fpdf style "B"."""
        rec = _make_resolved_recording(
            lines=[ResolvedLine(elements=[
                ResolvedElement(text="BOLDWORD", style=ElementStyle(bold=True)),
                ResolvedElement(text=" and plain"),
            ])],
        )
        doc = _make_document(recordings=[rec])

        calls = self._export_with_set_font_spy(doc, tmp_path / "bold.pdf")

        bold_calls = [
            args for args, _ in calls if len(args) >= 2 and args[1] == "B"
        ]
        assert bold_calls, "expected set_font(..., 'B', ...) for the bold element"

    def test_italic_element_uses_fpdf_italic_style(self, tmp_path: Path) -> None:
        """An italic body element is drawn with fpdf style "I"."""
        rec = _make_resolved_recording(
            lines=[ResolvedLine(elements=[
                ResolvedElement(text="ITALWORD", style=ElementStyle(italic=True)),
                ResolvedElement(text=" and plain"),
            ])],
        )
        doc = _make_document(recordings=[rec])

        calls = self._export_with_set_font_spy(doc, tmp_path / "italic.pdf")

        italic_calls = [
            args for args, _ in calls if len(args) >= 2 and args[1] == "I"
        ]
        assert italic_calls, "expected set_font(..., 'I', ...) for the italic element"


class TestHighlightAndColourRendering:
    """Foreground colour and highlight render in the PDF (L15.8.6b).

    Spy choice: the same call-through instance-spy pattern as
    :class:`TestElementStyleRendering` — a ``_LayoutPDF`` factory patch
    attaching spies to the freshly built instance — generalised to any
    method name.
    """

    def _export_with_method_spy(
        self, doc: ResolvedExportDocument, out_path: Path, *method_names: str,
    ) -> dict[str, list[tuple]]:
        import bootleg_catalog.core.layout_pdf_exporter as exporter_mod

        calls: dict[str, list[tuple]] = {name: [] for name in method_names}
        real_cls = exporter_mod._LayoutPDF

        def _factory(*args, **ctor_kwargs):
            instance = real_cls(*args, **ctor_kwargs)
            for name in method_names:
                original = getattr(instance, name)

                def spy(*m_args, _name=name, _original=original, **m_kwargs):
                    calls[_name].append((m_args, m_kwargs))
                    return _original(*m_args, **m_kwargs)

                setattr(instance, name, spy)
            return instance

        with patch.object(exporter_mod, "_LayoutPDF", side_effect=_factory):
            exporter_mod.export_layout_pdf(doc, out_path)
        return calls

    def test_coloured_element_sets_text_color(self, tmp_path: Path) -> None:
        """An element with color=#FF0000 → set_text_color(255, 0, 0).

        The element is the resolved form the parser produces for a
        recording_body containing ``[color=#FF0000]red[/color]`` (the
        parser side is covered by L15.8.6a in test_layout_text.py).
        """
        rec = _make_resolved_recording(
            lines=[ResolvedLine(elements=[
                ResolvedElement(text="red", style=ElementStyle(color="#FF0000")),
            ])],
        )
        doc = _make_document(recordings=[rec])
        out_path = tmp_path / "coloured.pdf"

        calls = self._export_with_method_spy(doc, out_path, "set_text_color")

        assert out_path.exists()
        assert (
            (255, 0, 0),
            {},
        ) in calls["set_text_color"], "expected set_text_color(255, 0, 0)"

    def test_highlighted_element_draws_filled_rect(self, tmp_path: Path) -> None:
        """An element with highlight_color=#FFFF00 → a style="F" fill rect.

        The element is the resolved form the parser produces for a
        recording_body containing ``==highlighted==`` (parser side:
        L15.8.6a).
        """
        rec = _make_resolved_recording(
            lines=[ResolvedLine(elements=[
                ResolvedElement(
                    text="hl", style=ElementStyle(highlight_color="#FFFF00"),
                ),
            ])],
        )
        doc = _make_document(recordings=[rec])
        out_path = tmp_path / "highlighted.pdf"

        calls = self._export_with_method_spy(doc, out_path, "rect")

        assert out_path.exists()
        fill_calls = [
            args for args, kwargs in calls["rect"]
            if kwargs.get("style") == "F"
        ]
        assert fill_calls, 'expected a rect(..., style="F") fill call'


# ---------------------------------------------------------------------------
# Header / footer rendering (L15.7d)
# ---------------------------------------------------------------------------

class TestInlineScreenshotRendering:
    """Inline [Screenshot:N] blocks draw images (L15.8.5).

    Spy choice: the exporter's own ``_LayoutPDF`` subclass records every
    image path it draws in ``_image_draws`` (through an ``image()``
    override installed on the subclass), so the test reads that list off
    the instance captured by ``_export_capture_pdf`` instead of parsing
    the produced PDF bytes.
    """

    def _screenshot_document(
        self,
        paths: list[str],
        n: int = 2,
    ) -> ResolvedExportDocument:
        """A document whose recording body is a resolved [Screenshot:N]."""
        from bootleg_catalog.core.field_resolver import FieldResolver
        from bootleg_catalog.core.layout_text import parse_layout_lines

        body = parse_layout_lines(f"[Screenshot:{n}]")
        rec = _make_recording(screenshot_paths=list(paths))
        resolver = FieldResolver()
        resolved_lines = [resolver.resolve_line(line, rec)[0] for line in body]
        recording = ResolvedRecording(recording=rec, lines=resolved_lines)
        return _make_document(recordings=[recording], recording_body=body)

    def test_screenshot_token_draws_images(
        self, tmp_path: Path
    ) -> None:
        """[Screenshot:2] with 2 real PNGs produces a PDF with 2 draws."""
        paths = [_make_small_png(tmp_path / f"shot{i}.png") for i in range(2)]
        doc = self._screenshot_document([str(p) for p in paths], n=2)

        out, pdf = _export_capture_pdf(doc, tmp_path / "shots.pdf")

        assert out.exists()
        assert len(pdf._image_draws) == 2

    def test_screenshot_token_with_no_paths_draws_nothing(
        self, tmp_path: Path
    ) -> None:
        """[Screenshot:2] with 0 PNGs produces a PDF with 0 image draws."""
        doc = self._screenshot_document([], n=2)

        out, pdf = _export_capture_pdf(doc, tmp_path / "noshots.pdf")

        assert out.exists()
        assert pdf._image_draws == []


class TestScreenshotVisualConfig:
    """L15.9.3: spacing/border from ScreenshotConfig reach the PDF.

    Spy choice: the same call-through instance-spy pattern as
    :class:`TestHighlightAndColourRendering` — a ``_LayoutPDF`` factory
    patch recording the ``image``/``rect`` calls with their arguments.
    """

    def _screenshot_document(
        self,
        paths: list[str],
        n: int = 2,
        **config_overrides,
    ) -> ResolvedExportDocument:
        """A document whose recording body is a resolved [Screenshot:N]."""
        from bootleg_catalog.core.field_resolver import FieldResolver
        from bootleg_catalog.core.layout_text import parse_layout_lines

        body = parse_layout_lines(f"[Screenshot:{n}]")
        rec = _make_recording(screenshot_paths=list(paths))
        resolver = FieldResolver()
        resolved_lines = [resolver.resolve_line(line, rec)[0] for line in body]
        recording = ResolvedRecording(recording=rec, lines=resolved_lines)
        return _make_document(
            recordings=[recording],
            recording_body=body,
            screenshot_config=ScreenshotConfig(**config_overrides),
        )

    def _export_with_spy(
        self, doc: ResolvedExportDocument, out_path: Path, *method_names: str,
    ) -> dict[str, list[tuple]]:
        import bootleg_catalog.core.layout_pdf_exporter as exporter_mod

        calls: dict[str, list[tuple]] = {name: [] for name in method_names}
        real_cls = exporter_mod._LayoutPDF

        def _factory(*args, **ctor_kwargs):
            instance = real_cls(*args, **ctor_kwargs)
            for name in method_names:
                original = getattr(instance, name)

                def spy(*m_args, _name=name, _original=original, **m_kwargs):
                    calls[_name].append((m_args, m_kwargs))
                    return _original(*m_args, **m_kwargs)

                setattr(instance, name, spy)
            return instance

        with patch.object(exporter_mod, "_LayoutPDF", side_effect=_factory):
            exporter_mod.export_layout_pdf(doc, out_path)
        return calls

    def test_spacing_px_separates_images(self, tmp_path: Path) -> None:
        """spacing_px=10 puts the 2nd image at least one image+gap away.

        Both PNGs are tall, so the height bound is binding and each
        scaled width sits strictly below the cell width — the gap
        between draw positions is then exactly the configured spacing.
        """
        paths = [
            str(_make_small_png(tmp_path / f"shot{i}.png", size=(64, 160)))
            for i in range(2)
        ]
        doc = self._screenshot_document(paths, n=2, spacing_px=10)
        out_path = tmp_path / "spacing.pdf"

        calls = self._export_with_spy(doc, out_path, "image")

        draws = [kwargs for _, kwargs in calls["image"]]
        assert len(draws) == 2
        first, second = draws
        gap_mm = 10 * 25.4 / 96.0
        assert second["x"] >= first["x"] + first["w"] + gap_mm

    def test_border_width_px_draws_stroke_rects(self, tmp_path: Path) -> None:
        """border_width_px=2 strokes a rect behind each image."""
        paths = [_make_small_png(tmp_path / f"shot{i}.png") for i in range(2)]
        doc = self._screenshot_document(paths, n=2, border_width_px=2)
        out_path = tmp_path / "bordered.pdf"

        calls = self._export_with_spy(doc, out_path, "rect")

        stroke_calls = [
            kwargs for _, kwargs in calls["rect"]
            if "D" in str(kwargs.get("style", ""))
        ]
        assert len(stroke_calls) >= 2


class TestBackgroundColours:
    """Configurable page/header/screenshot backgrounds reach the PDF.

    Same call-through instance-spy pattern as
    :class:`TestScreenshotVisualConfig`: a ``_LayoutPDF`` factory patch
    recording ``rect``/``set_fill_color`` (and ``image``) with their
    arguments.
    """

    @staticmethod
    def _screenshot_document(
        paths: list[str], **config_overrides,
    ) -> ResolvedExportDocument:
        """A document whose recording body is a resolved [Screenshot:2]."""
        from bootleg_catalog.core.field_resolver import FieldResolver
        from bootleg_catalog.core.layout_text import parse_layout_lines

        body = parse_layout_lines("[Screenshot:2]")
        rec = _make_recording(screenshot_paths=list(paths))
        resolver = FieldResolver()
        resolved = [resolver.resolve_line(line, rec)[0] for line in body]
        recording = ResolvedRecording(recording=rec, lines=resolved)
        return _make_document(
            recordings=[recording],
            recording_body=body,
            screenshot_config=ScreenshotConfig(**config_overrides),
        )

    @staticmethod
    def _filled_rects(calls: dict[str, list[tuple]]) -> list[tuple]:
        """Positional args of every filled (style="F") rect drawn."""
        return [
            args for args, kwargs in calls["rect"]
            if kwargs.get("style") == "F"
        ]

    @staticmethod
    def _fill_colours(calls: dict[str, list[tuple]]) -> list[tuple]:
        return [args for args, _ in calls["set_fill_color"]]

    def test_page_background_fills_whole_page(self, tmp_path: Path) -> None:
        """page_background_color fills (0, 0, w, h) with that colour."""
        doc = _make_document(
            page_config=PageConfig(page_background_color="#112233"),
        )
        calls = _export_with_method_spies(
            doc, tmp_path / "page-bg.pdf", "rect", "set_fill_color",
        )
        # 0x11, 0x22, 0x33 — the configured colour reached set_fill_color.
        assert (17, 34, 51) in self._fill_colours(calls)
        # ...and a filled rect was drawn from the page origin, full size.
        assert any(
            args[:2] == (0, 0) and args[2] > 0 and args[3] > 0
            for args in self._filled_rects(calls)
        )

    def test_header_background_fills_header_band(self, tmp_path: Path) -> None:
        """header_background_color fills (0, 0, w, top margin)."""
        doc = _make_document(
            page_config=PageConfig(header_background_color="#112233"),
        )
        calls = _export_with_method_spies(
            doc, tmp_path / "header-bg.pdf", "rect", "set_fill_color",
        )
        assert (17, 34, 51) in self._fill_colours(calls)
        # The band spans the full width, from y=0 down to the top margin
        # (15.0 mm by default).
        assert any(
            args[:2] == (0, 0) and abs(args[3] - 15.0) < 1e-6
            for args in self._filled_rects(calls)
        )

    def test_screenshot_background_fills_image_box(self, tmp_path: Path) -> None:
        """screenshot_config.background_color fills behind each image."""
        paths = [_make_small_png(tmp_path / f"shot{i}.png") for i in range(2)]
        doc = self._screenshot_document(paths, background_color="#445566")
        calls = _export_with_method_spies(
            doc, tmp_path / "shot-bg.pdf", "rect", "set_fill_color", "image",
        )
        # 0x44, 0x55, 0x66 — the configured colour reached set_fill_color.
        assert (68, 85, 102) in self._fill_colours(calls)
        # Both images drew, and each has a filled rect behind it.
        assert len([kwargs for _, kwargs in calls["image"]]) == 2
        assert len(self._filled_rects(calls)) >= 2


class TestResolveTextSlot:
    def test_resolves_markers_and_plain_text(self) -> None:
        """{Field}/[Field] markers resolve; plain text passes through."""
        rec = _make_recording(year=1995)
        slot = TextSlot(text="[Year] ({Duration}) literal")
        assert resolve_text_slot(slot, rec) == "1995 (2:00:00) literal"

    def test_missing_field_resolves_to_empty_string(self) -> None:
        """An unregistered field resolves to empty, not an error."""
        rec = _make_recording()
        slot = TextSlot(text="A[NoSuchField]B")
        assert resolve_text_slot(slot, rec) == "AB"


class TestPDFHeaderFooter:
    def test_header_resolves_markers_against_recording(
        self, tmp_path: Path,
    ) -> None:
        """A header center slot with [Year] resolves against the recording.

        "Alegría" (í = U+00ED) is latin-1, so validation passes; the
        resolved text must reach the PDF writer via the FPDF subclass.
        """
        header = HeaderConfig(
            enabled=True,
            scope="all",
            center=TextSlot(text="Alegr\u00eda / [Year]"),
        )
        rec = _make_resolved_recording(year=2010)
        doc = _make_document(recordings=[rec], header=header)
        out = tmp_path / "header_resolve.pdf"
        result, pdf = _export_capture_pdf(doc, out)

        assert result.exists()
        assert pdf._header_draws >= 1
        assert pdf._header_visible_draws == 1
        assert pdf._header_last_texts[1] == "Alegr\u00eda / 2010"

    def test_header_scope_first_draws_only_on_first_page(
        self, tmp_path: Path,
    ) -> None:
        """scope="first": fpdf calls header() per page but only page 1 paints."""
        header = HeaderConfig(
            enabled=True,
            scope="first",
            center=TextSlot(text="First Page Only"),
        )
        rec = _make_long_recording(num_lines=120)
        doc = _make_document(recordings=[rec], header=header)
        out = tmp_path / "header_first.pdf"
        _, pdf = _export_capture_pdf(doc, out)

        assert pdf.page == 3  # the fixture really spans 3 pages
        assert pdf._header_draws >= 3  # header() is called per page
        assert pdf._header_visible_draws == 1

    def test_header_scope_all_draws_on_every_page(
        self, tmp_path: Path,
    ) -> None:
        """scope="all": the header paints on each of the 3 pages."""
        header = HeaderConfig(
            enabled=True,
            scope="all",
            center=TextSlot(text="Every Page"),
        )
        rec = _make_long_recording(num_lines=120)
        doc = _make_document(recordings=[rec], header=header)
        out = tmp_path / "header_all.pdf"
        _, pdf = _export_capture_pdf(doc, out)

        assert pdf.page == 3
        assert pdf._header_visible_draws == 3

    def test_footer_draws_on_every_page(self, tmp_path: Path) -> None:
        """An enabled footer draws once per page (footers have no scope)."""
        footer = FooterConfig(
            enabled=True,
            center=TextSlot(text="Bootleg Catalog [Year]"),
        )
        rec = _make_long_recording(num_lines=120, year=1999)
        doc = _make_document(recordings=[rec], footer=footer)
        out = tmp_path / "footer_all.pdf"
        _, pdf = _export_capture_pdf(doc, out)

        assert pdf.page == 3
        assert pdf._footer_visible_draws == 3
        assert pdf._footer_last_texts[1] == "Bootleg Catalog 1999"

    def test_disabled_header_draws_nothing(self, tmp_path: Path) -> None:
        """enabled=False on the header → no visible draws."""
        header = HeaderConfig(
            enabled=False,
            scope="all",
            center=TextSlot(text="Never Shown"),
        )
        doc = _make_document(header=header)
        out = tmp_path / "header_disabled.pdf"
        _, pdf = _export_capture_pdf(doc, out)

        assert pdf._header_visible_draws == 0
        assert pdf._header_last_texts is None

    def test_document_without_recordings_skips_header_footer(
        self, tmp_path: Path,
    ) -> None:
        """No recordings → no resolution context → no draws, no crash."""
        header = HeaderConfig(
            enabled=True, scope="all", center=TextSlot(text="[Show]"),
        )
        footer = FooterConfig(
            enabled=True, center=TextSlot(text="[Duration]"),
        )
        layout = _make_layout(header=header, footer=footer)
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
        out = tmp_path / "no_recordings.pdf"
        result, pdf = _export_capture_pdf(doc, out)

        assert result.exists()
        assert pdf._header_visible_draws == 0
        assert pdf._footer_visible_draws == 0


class TestPDFLogoSlots:
    """L15.7f.3: LogoSlots resolve against app_data_dir()/layouts/."""

    def test_header_logo_slot_draws_and_counts_visible(
        self, tmp_path: Path,
    ) -> None:
        """A present logo resolves to its path and paints a visible row."""
        layouts = tmp_path / "layouts"
        layouts.mkdir()
        _make_small_png(layouts / "test_logo.png")
        header = HeaderConfig(
            enabled=True,
            center=LogoSlot(width_mm=15, logo_file="test_logo.png"),
        )
        doc = _make_document(header=header)
        out = tmp_path / "header_logo.pdf"

        with patch(
            "bootleg_catalog.core.layout_pdf_exporter.app_data_dir",
            return_value=tmp_path,
        ):
            # The resolver anchors on app_data_dir()/layouts/, not cwd.
            assert resolve_logo_slot(header.center) == layouts / "test_logo.png"
            result, pdf = _export_capture_pdf(doc, out)

        assert result.exists()
        assert pdf._header_visible_draws == 1
        # A logo carries no text: introspection records None for it.
        assert pdf._header_last_texts == (None, None, None)

    def test_header_missing_logo_file_draws_nothing(
        self, tmp_path: Path,
    ) -> None:
        """A logo_file absent from disk → the row draws nothing, no error."""
        header = HeaderConfig(
            enabled=True,
            center=LogoSlot(width_mm=15, logo_file="missing.png"),
        )
        doc = _make_document(header=header)
        out = tmp_path / "header_logo_missing.pdf"

        with patch(
            "bootleg_catalog.core.layout_pdf_exporter.app_data_dir",
            return_value=tmp_path,
        ):
            result, pdf = _export_capture_pdf(doc, out)

        assert result.exists()
        assert pdf._header_visible_draws == 0
        assert pdf._header_last_texts is None

    def test_resolve_logo_slot_absent_and_missing(self, tmp_path: Path) -> None:
        """logo_file=None → None; a file absent from layouts/ → None."""
        assert resolve_logo_slot(LogoSlot(logo_file=None)) is None
        with patch(
            "bootleg_catalog.core.layout_pdf_exporter.app_data_dir",
            return_value=tmp_path,
        ):
            assert resolve_logo_slot(LogoSlot(logo_file="missing.png")) is None


class TestFontResolution:
    """L15.9.1: PageConfig fonts resolve to TTF files, or fall back.

    The resolver's fallback contract: Helvetica is the fpdf built-in
    (never a TTF on disk) so it must return None rather than scan or
    raise; an unresolvable family exports anyway, with a warning.
    """

    def test_helvetica_body_font_exports(self, tmp_path: Path) -> None:
        """body_font="Helvetica" (the existing default) still exports.

        Pins the built-in path against the new resolver: no scan, no
        warning machinery in the way — exactly the pre-L15.9.1
        behaviour.
        """
        doc = _make_document(page_config=PageConfig(body_font="Helvetica"))
        out = tmp_path / "helvetica_body.pdf"

        result = export_layout_pdf(doc, out)

        assert result.exists()
        assert result.stat().st_size > 0

    def test_missing_body_font_warns_and_falls_back(
        self, tmp_path: Path,
    ) -> None:
        """An uninstalled family exports via Helvetica, with a warning."""
        import bootleg_catalog.core.layout_pdf_exporter as exporter_mod

        doc = _make_document(
            page_config=PageConfig(body_font="ThisFontDoesNotExist")
        )
        out = tmp_path / "missing_font_body.pdf"

        with patch.object(exporter_mod, "get_logger") as mock_logger:
            result = export_layout_pdf(doc, out)

        assert result.exists()
        warning = mock_logger.return_value.warning
        assert warning.called, "expected a fallback warning on the logger"
        # The message names the font the user asked for.
        assert "ThisFontDoesNotExist" in warning.call_args[0][0]

    def test_resolve_font_helvetica_returns_none(self) -> None:
        """_resolve_font("Helvetica") is None — never an exception.

        Helvetica is an fpdf built-in core font, not a TTF file on
        disk, so there is nothing to resolve.
        """
        from bootleg_catalog.core.layout_pdf_exporter import _resolve_font

        assert _resolve_font("Helvetica") is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows fonts")
    def test_resolve_font_finds_windows_ttf(self) -> None:
        """A real system family ("Arial", a .ttf) resolves to its file."""
        from bootleg_catalog.core.layout_pdf_exporter import _resolve_font

        resolved = _resolve_font("Arial")

        assert resolved is not None
        path, family = resolved
        assert path.suffix.lower() == ".ttf"
        assert path.is_file()
        assert family == "Arial"


# ---------------------------------------------------------------------------
# .ttc collections and the per-user font directory
# ---------------------------------------------------------------------------

class TestTTCFontResolution:
    """.ttc files resolve from the per-user dir first, then the system.

    fpdf2's ``add_font`` accepts a ``.ttc`` collection path directly,
    so a match returns the collection file itself — no extraction.
    Both directories are patched to tmp trees so the assertions can
    tell which copy won; a machine without any ``.ttc`` skips.
    """

    @staticmethod
    def _real_ttcs() -> list[Path]:
        """Collections actually installed in the system font dir."""
        windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        return sorted(windir.glob("*.ttc")) if windir.is_dir() else []

    @staticmethod
    def _first_subfont_family(ttc: Path) -> str | None:
        """The first subfont's name-table family (ID 16, else ID 1)."""
        from fontTools.ttLib import TTCollection

        collection = TTCollection(str(ttc), lazy=True)
        try:
            name_table = collection.fonts[0]["name"]
            family = name_table.getDebugName(16) or name_table.getDebugName(1)
            return str(family) if family else None
        finally:
            for font in collection.fonts:
                try:
                    font.close()
                except Exception:
                    pass

    @staticmethod
    def _make_shadow_ttc(
        base: Path, sub_dir: Path, subfamily: str, source: Path,
    ) -> Path:
        """Save *source*'s first subfont as a one-face .ttc under base/sub_dir.

        The subfont's names are rewritten to the fixed family
        "TtcShadowTest" with a distinguishing subfamily, so the two
        directories offer the same family with different content.
        """
        from fontTools.ttLib import TTCollection

        source_collection = TTCollection(str(source), lazy=True)
        font = source_collection.fonts[0]
        for name_id, value in (
            (1, "TtcShadowTest"), (16, "TtcShadowTest"), (2, subfamily),
        ):
            font["name"].setName(value, name_id, 3, 1, 0x409)
        built = TTCollection()
        built.fonts = [font]
        target_dir = base / sub_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / "shadow.ttc"
        built.save(str(dest))
        return dest

    def test_ttc_in_per_user_dir_is_found(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """A .ttc under the patched per-user dir resolves to that copy."""
        from bootleg_catalog.core.layout_pdf_exporter import (
            _FONT_CACHE,
            _resolve_font,
        )

        ttcs = self._real_ttcs()
        if not ttcs:
            pytest.skip("no .ttc available")
        family = self._first_subfont_family(ttcs[0])
        if not family:
            pytest.skip("no .ttc available")

        user_dir = tmp_path / "Microsoft" / "Windows" / "Fonts"
        user_dir.mkdir(parents=True)
        copied = Path(shutil.copy(ttcs[0], user_dir / ttcs[0].name))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        _FONT_CACHE.clear()

        resolved = _resolve_font(family)

        assert resolved is not None
        path, resolved_family = resolved
        # The per-user copy wins over the identical system original —
        # proving the second search directory is actually consulted.
        assert path == copied
        assert resolved_family == family

    def test_ttc_in_per_user_dir_shadows_system_dir(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """Same family in both patched dirs → the per-user .ttc wins."""
        from bootleg_catalog.core.layout_pdf_exporter import (
            _FONT_CACHE,
            _resolve_font,
        )

        ttcs = self._real_ttcs()
        if not ttcs:
            pytest.skip("no .ttc available")
        try:
            local_ttc = self._make_shadow_ttc(
                tmp_path / "localappdata",
                Path("Microsoft") / "Windows" / "Fonts",
                "Local",
                ttcs[0],
            )
            system_ttc = self._make_shadow_ttc(
                tmp_path / "windir", Path("Fonts"), "System", ttcs[0],
            )
        except Exception:
            pytest.skip("cannot build .ttc fixture")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
        monkeypatch.setenv("WINDIR", str(tmp_path / "windir"))
        _FONT_CACHE.clear()

        resolved = _resolve_font("TtcShadowTest")

        assert resolved is not None
        path, family = resolved
        # The per-user directory is searched first, so its collection
        # is the one that reaches the exporter.
        assert path == local_ttc
        assert path != system_ttc
        assert family == "TtcShadowTest"


class TestVariableFontResolution:
    """Variable fonts (fvar) match by instance name and pin to static.

    fpdf2 embeds a variable font at its default axis values (verified
    empirically: ``add_font`` accepts the file but draws the default
    weight — wght 200 for Manrope, 400 for Google Sans), so the
    resolver matches the base family or the ``"{base} {instance}"``
    composite against the ``fvar`` named instances and pins the axes
    to a static ``.ttf``.  Both real fonts below are per-user installs
    in ``%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts``; a machine without
    them skips.  The ``_resolve_font("Helvetica") is None`` regression
    guard already lives in :class:`TestFontResolution`.
    """

    @staticmethod
    def _user_font(name: str) -> Path:
        return (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Microsoft" / "Windows" / "Fonts" / name
        )

    def test_manrope_resolves_to_pinned_static(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """A bare "Manrope" pins its Regular instance (wght 400).

        The file's default axis is wght=200 (ExtraLight), so only the
        raw file would embed the wrong weight; name-ID 16 already says
        "Manrope", but the fix must still instance it to Regular.
        """
        real = self._user_font("Manrope-VariableFont_wght.ttf")
        if not real.is_file():
            pytest.skip("Manrope not installed")
        user_dir = tmp_path / "Microsoft" / "Windows" / "Fonts"
        user_dir.mkdir(parents=True)
        copied = Path(shutil.copy(real, user_dir / real.name))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        from bootleg_catalog.core.layout_pdf_exporter import (
            _FONT_CACHE,
            _resolve_font,
        )
        _FONT_CACHE.clear()

        resolved = _resolve_font("Manrope")

        assert resolved is not None
        path, family = resolved
        assert isinstance(path, Path)
        assert path.exists()
        # Pinned to a static Regular face, not the raw variable file
        # whose default axis is ExtraLight.
        assert path != copied
        assert path.suffix.lower() == ".ttf"
        assert family == "Manrope"

    def test_google_sans_medium_resolves_via_instance(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """"Google Sans Medium" matches the fvar "Medium" instance.

        The name table lists only "Google Sans"/"Regular" (no name-ID
        16/17), so "Medium" is invisible to name matching; it exists
        solely as an fvar named instance (wght 500).  The resolver
        matches the composite and pins wght=500 to a static .ttf.
        """
        real = self._user_font("GoogleSans-VariableFont_GRAD,opsz,wght.ttf")
        if not real.is_file():
            pytest.skip("Google Sans not installed")
        user_dir = tmp_path / "Microsoft" / "Windows" / "Fonts"
        user_dir.mkdir(parents=True)
        copied = Path(shutil.copy(real, user_dir / real.name))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        from bootleg_catalog.core.layout_pdf_exporter import (
            _FONT_CACHE,
            _resolve_font,
        )
        _FONT_CACHE.clear()

        resolved = _resolve_font("Google Sans Medium")

        assert resolved is not None
        path, family = resolved
        assert isinstance(path, Path)
        assert path.exists()
        assert path != copied  # pinned static, not the raw variable file
        assert family == "Google Sans Medium"
        # The result is fully static (all axes pinned), proving the
        # wght=500 instance was baked in rather than left at default.
        from fontTools.ttLib import TTFont

        assert "fvar" not in TTFont(str(path))


# ---------------------------------------------------------------------------
# Bug-fix tests (E/F/G, v1.0 user testing)
# ---------------------------------------------------------------------------

class TestScreenshotSinglePass:
    """Bug E: [Screenshot:N] renders exactly once — no trailing duplicate.

    The export is driven both ways screenshots can reach it: the token
    resolves from ``recording.screenshot_paths`` while the export
    window also passes ``screenshot_paths_by_recording``.  A second,
    per-recording pass keyed on that map (the pre-token rendering
    path) draws every recording's images again after its body.

    Spy: ``_LayoutPDF.image``'s built-in ``_image_draws`` recorder,
    read off the instance captured by ``_export_capture_pdf``.
    """

    def test_screenshots_drawn_once_at_token_position(
        self, tmp_path: Path,
    ) -> None:
        """2 screenshots at [Screenshot:2] draw 2 images, not 4."""
        from bootleg_catalog.core.field_resolver import FieldResolver
        from bootleg_catalog.core.layout_text import parse_layout_lines

        paths = [
            str(_make_small_png(tmp_path / f"shot{i}.png")) for i in range(2)
        ]
        body = parse_layout_lines("[Screenshot:2]")
        rec = _make_recording(screenshot_paths=paths)
        resolver = FieldResolver()
        resolved_lines = [resolver.resolve_line(line, rec)[0] for line in body]
        recording = ResolvedRecording(recording=rec, lines=resolved_lines)
        doc = _make_document(recordings=[recording], recording_body=body)

        _, pdf = _export_capture_pdf(
            doc,
            tmp_path / "single_pass.pdf",
            screenshot_paths_by_recording={rec.source_path: paths},
        )

        assert len(pdf._image_draws) == 2


class TestSeparatorFallback:
    """Bug F: the separator draws as characters the font can render.

    fpdf substitutes ``?`` for glyphs the active font lacks — and the
    old pre-pass did the same substitution itself via ``latin-1
    errors="replace"`` — so a U+2500 separator printed as a run of
    question marks.  The separator only draws between show sections,
    so these documents carry two.
    """

    def test_box_separator_never_draws_question_marks(
        self, tmp_path: Path,
    ) -> None:
        """U+2500 with the default body font draws no "??" run."""
        doc = _make_two_section_document("\u2500")
        out = tmp_path / "box_separator.pdf"

        calls = _export_with_method_spies(doc, out, "cell", "multi_cell")

        assert out.exists()
        drawn = _captured_texts(calls)
        # The separator line itself must be on the page (>= 50 chars),
        # so the "??" assertion cannot pass vacuously.
        separator_draws = [t for t in drawn if len(t) >= 50]
        assert separator_draws, "expected the separator line to be drawn"
        assert not any(t.startswith("??") for t in drawn)
        assert all("?" not in t for t in separator_draws)

    def test_default_layout_uses_ascii_separator(self) -> None:
        """default_layout() seeds "-", not the U+2500 box char (F fix 2)."""
        from bootleg_catalog.core.layout_manager import default_layout

        assert default_layout().separator == "-"


class TestCustomFontApplication:
    """Bug G: resolved PageConfig fonts reach every text draw.

    Spy: the module-level call-through instance-spy factory recording
    ``set_font`` with its arguments.  "Arial" resolves from
    ``%WINDIR%\\Fonts`` (arial.ttf) through the existing resolver, so a
    family list containing "Arial" proves the draw used the registered
    custom family rather than the Helvetica class default.
    """

    @staticmethod
    def _families(calls: dict[str, list[tuple]]) -> list[str]:
        families: list[str] = []
        for args, kwargs in calls.get("set_font", []):
            if args:
                families.append(args[0])
            elif "family" in kwargs:
                families.append(kwargs["family"])
        return families

    def test_body_text_uses_custom_body_font(self, tmp_path: Path) -> None:
        """Body draws call set_font("Arial", ...) once the font resolves."""
        doc = _make_document(page_config=PageConfig(body_font="Arial"))
        out = tmp_path / "body_custom_font.pdf"

        calls = _export_with_method_spies(doc, out, "set_font")

        assert out.exists()
        assert "Arial" in self._families(calls)

    def test_header_band_uses_custom_header_font(self, tmp_path: Path) -> None:
        """The header band draws with header_font, not the body default."""
        header = HeaderConfig(
            enabled=True, scope="all", center=TextSlot(text="Header Chrome"),
        )
        doc = _make_document(
            header=header,
            page_config=PageConfig(header_font="Arial"),
        )
        out = tmp_path / "header_custom_font.pdf"

        calls = _export_with_method_spies(doc, out, "set_font")

        assert out.exists()
        # body_font stays at its "Helvetica" default, so any "Arial"
        # set_font call can only have come from the header band.
        assert "Arial" in self._families(calls)

    def test_custom_font_with_box_separator(self, tmp_path: Path) -> None:
        """Bugs F+G together: custom family applied, separator stays "??"-free."""
        doc = _make_two_section_document(
            "\u2500",
            page_config=PageConfig(body_font="Arial"),
        )
        out = tmp_path / "font_and_separator.pdf"

        calls = _export_with_method_spies(
            doc, out, "set_font", "cell", "multi_cell",
        )

        assert out.exists()
        assert "Arial" in self._families(calls)
        # Everything drawn into the content stream — every resolved
        # fixture text plus the separator — is free of "?", so the
        # fallback cannot reintroduce the question marks.
        assert not any("?" in t for t in _captured_texts(calls))
