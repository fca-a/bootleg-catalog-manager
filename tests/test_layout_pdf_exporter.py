"""Tests for the layout-based PDF exporter (L8)."""

from __future__ import annotations

import os
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
