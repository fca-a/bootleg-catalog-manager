"""Layout-based PDF exporter.

Consumes a :class:`ResolvedExportDocument` and produces a PDF file.

Public API
----------
- :func:`export_layout_pdf` — the single entry point.
- :func:`resolve_text_slot` — resolves ``{Field}``/``[Field]`` markers
  in a header/footer :class:`~bootleg_catalog.models.layout_model.TextSlot`.
- :func:`resolve_logo_slot` — maps a header/footer
  :class:`~bootleg_catalog.models.layout_model.LogoSlot` to an existing
  image path under ``app_data_dir()/layouts/``, or ``None``.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

from fpdf import FPDF

try:
    # Optional accelerator: reads a font's real family name from its
    # name table.  Never a hard dependency — the exporter falls back to
    # a filename heuristic when it is absent.
    from fontTools.ttLib import TTFont as _TTFont
except ImportError:  # pragma: no cover - depends on the environment
    _TTFont = None

from bootleg_catalog.core.field_resolver import FieldResolver, ResolvedLine
from bootleg_catalog.core.layout_service import (
    GridCell,
    ScreenshotLayout,
    compute_grid,
)
from bootleg_catalog.core.layout_text import parse_layout_lines
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    FooterConfig,
    HeaderConfig,
    LogoSlot,
    PageConfig,
    TextSlot,
)
from bootleg_catalog.models.resolved_export import ResolvedExportDocument
from bootleg_catalog.utils.logging import get_logger
from bootleg_catalog.utils.paths import app_data_dir


class LayoutPDFExportError(Exception):
    """Raised when the layout PDF export fails."""


# ---------------------------------------------------------------------------
# Header / footer text resolution
# ---------------------------------------------------------------------------

def resolve_text_slot(slot: TextSlot, recording: ExportRecording) -> str:
    """Resolve ``{Field}``/``[Field]`` markers in ``slot.text``.

    Returns the plain string.  Missing fields resolve to an empty
    string (consistent with the rest of the resolver).

    Marker parsing is delegated to the canonical layout parser
    (:func:`bootleg_catalog.core.layout_text.parse_layout_lines`) and
    field lookup to :class:`FieldResolver` — no marker parsing is
    re-implemented here.  This helper lives in the PDF exporter; the
    preview render imports it from here.
    """
    resolver = FieldResolver()
    resolved_lines: list[str] = []
    for line in parse_layout_lines(slot.text):
        resolved, _errors = resolver.resolve_line(line, recording)
        resolved_lines.append("".join(elem.text for elem in resolved.elements))
    return "\n".join(resolved_lines)


def resolve_logo_slot(slot: LogoSlot) -> Path | None:
    """Return the absolute path to the logo image.

    ``None`` when ``slot.logo_file`` is unset, or when the file does not
    exist under ``app_data_dir()/layouts/``.  A stale reference is never
    an error and never raises — the render layer simply skips it, the
    same policy as missing referenced screenshots.
    """
    if not slot.logo_file:
        return None
    path = app_data_dir() / "layouts" / slot.logo_file
    if not path.is_file():
        return None
    return path


class _SlotDraw(NamedTuple):
    """A pre-resolved header/footer slot.

    The text has already had its markers replaced; ``font_size`` and
    ``bold`` ride along so the drawing code never touches the model.
    """
    text: str
    font_size: int
    bold: bool


class _LogoDraw(NamedTuple):
    """A pre-resolved header/footer logo slot.

    ``path`` is an absolute path that EXISTS on disk (resolution via
    :func:`resolve_logo_slot` already dropped missing files); the
    natural pixel size is read at draw time.  ``width_mm`` rides along
    so the drawing code never touches the model.
    """
    path: Path
    width_mm: float


_ResolvedSlot = _SlotDraw | _LogoDraw | None


def _resolve_slot_row(
    config: HeaderConfig | FooterConfig | None,
    recording: ExportRecording | None,
) -> tuple[_ResolvedSlot, _ResolvedSlot, _ResolvedSlot] | None:
    """Pre-resolve a header/footer config's three slots (left, center, right).

    Returns ``None`` when the config is absent, disabled, or there is
    no recording to resolve against (documents without recordings skip
    header/footer entirely).  Slots that are ``None`` — or whose text
    resolves to an empty string — stay ``None`` so nothing is drawn.
    A :class:`LogoSlot` resolves to :class:`_LogoDraw` only when its
    file exists (missing logos become ``None``: nothing drawn, and a
    row whose only slot was a missing logo stays invisible).
    """
    if config is None or not config.enabled or recording is None:
        return None
    resolved: list[_ResolvedSlot] = []
    for raw in (config.left, config.center, config.right):
        if raw is None:
            resolved.append(None)
            continue
        if isinstance(raw, LogoSlot):
            path = resolve_logo_slot(raw)
            resolved.append(
                _LogoDraw(path=path, width_mm=raw.width_mm)
                if path is not None
                else None
            )
            continue
        if not raw.text:
            resolved.append(None)
            continue
        text = resolve_text_slot(raw, recording)
        if not text:
            resolved.append(None)
            continue
        resolved.append(
            _SlotDraw(text=text, font_size=raw.font_size, bold=raw.bold)
        )
    if not any(r is not None for r in resolved):
        return None
    return (resolved[0], resolved[1], resolved[2])


# ---------------------------------------------------------------------------
# FPDF subclass with header/footer hooks
# ---------------------------------------------------------------------------

class _LayoutPDF(FPDF):
    """FPDF subclass that paints the configured header/footer rows.

    The slot tuples are PRE-RESOLVED (markers already replaced by
    concrete text) by :func:`export_layout_pdf`; this class never
    touches the field resolver.

    fpdf2 calls :meth:`header` at the start of every ``add_page()``
    (after the cursor has been reset to the top-left margin, and
    WITHOUT restoring it afterwards — both hooks therefore save and
    restore the cursor) and :meth:`footer` when a page ends (on the
    next ``add_page()`` or at ``output()``).  Both hooks fire once per
    page regardless of configuration, so the counters come in pairs:

    - ``_header_draws`` / ``_footer_draws`` — hook invocations.
    - ``_header_visible_draws`` / ``_footer_visible_draws`` —
      invocations that actually painted a row (0 when disabled/absent,
      1 for a ``scope="first"`` header, every page otherwise).
    - ``_header_last_texts`` / ``_footer_last_texts`` — the resolved
      ``(left, center, right)`` texts most recently painted, for
      exact-text introspection in tests.

    ``header_font`` / ``body_font`` are the families
    :func:`export_layout_pdf` resolved and registered for this document.
    The class-level defaults keep direct construction (and any
    ``header()``/``footer()`` call during ``__init__``) on the fpdf
    built-in until they are assigned.
    """

    body_font = "Helvetica"
    header_font = "Helvetica"

    def __init__(
        self,
        *,
        header_texts: tuple[_ResolvedSlot, _ResolvedSlot, _ResolvedSlot] | None,
        footer_texts: tuple[_ResolvedSlot, _ResolvedSlot, _ResolvedSlot] | None,
        header_scope: str,
        page_config: PageConfig,
    ) -> None:
        # Counters exist before super().__init__() because fpdf may call
        # header()/footer() during construction on some code paths.
        self._header_draws = 0
        self._footer_draws = 0
        self._header_visible_draws = 0
        self._footer_visible_draws = 0
        self._header_last_texts: tuple[str | None, str | None, str | None] | None = None
        self._footer_last_texts: tuple[str | None, str | None, str | None] | None = None
        # Every image drawn by this PDF, in order.  Exposed so
        # tests can count inline [Screenshot:N] draws without parsing the
        # produced file; logo slots land here too.
        self._image_draws: list[str] = []

        self.header_texts = header_texts
        self.footer_texts = footer_texts
        self.header_scope = header_scope
        self.header_texts_any = any(s is not None for s in (header_texts or ()))
        self.footer_texts_any = any(s is not None for s in (footer_texts or ()))

        margin_top, margin_right, margin_bottom, margin_left = (
            page_config.effective_margins_mm()
        )
        self._margin_top_mm = margin_top
        self._margin_right_mm = margin_right
        self._margin_bottom_mm = margin_bottom
        self._margin_left_mm = margin_left

        # width_mm/height_mm already encode the orientation (derived from
        # page_size + orientation), so a fixed "P" keeps the tuple as-is.
        super().__init__(
            orientation="P",
            unit="mm",
            format=(page_config.width_mm, page_config.height_mm),
        )
        self.set_margins(margin_left, margin_top, margin_right)
        # Auto page breaks stop at the bottom margin; add_page() restarts
        # each new page at the top margin.
        self.set_auto_page_break(auto=True, margin=margin_bottom)

    def image(self, *args, **kwargs):  # noqa: D102 (fpdf override)
        """Record every drawn image path, then draw it.

        Lets tests count inline screenshot draws via
        :attr:`_image_draws` without touching fpdf internals.
        """
        if args:
            self._image_draws.append(str(args[0]))
        return super().image(*args, **kwargs)

    def header(self) -> None:
        self._header_draws += 1
        if not self.header_texts_any:
            return
        if self.header_scope == "first" and self.page_no() > 1:
            return
        self._header_visible_draws += 1
        # Logo slots carry no text; only TextSlots contribute here.
        self._header_last_texts = tuple(
            slot.text if isinstance(slot, _SlotDraw) else None
            for slot in self.header_texts
        )
        self._draw_slot_row(
            self.header_texts, y_center_mm=self._margin_top_mm / 2
        )

    def footer(self) -> None:
        self._footer_draws += 1
        if not self.footer_texts_any:
            return
        self._footer_visible_draws += 1
        self._footer_last_texts = tuple(
            slot.text if isinstance(slot, _SlotDraw) else None
            for slot in self.footer_texts
        )
        self._draw_slot_row(
            self.footer_texts,
            y_center_mm=self.h - self._margin_bottom_mm / 2,
        )

    def _draw_slot_row(
        self,
        slots: tuple[_ResolvedSlot, _ResolvedSlot, _ResolvedSlot],
        *,
        y_center_mm: float,
    ) -> None:
        """Draw the (left, center, right) slots as one aligned row.

        All three slots share one full-usable-width cell band so their
        anchors land exactly where the page geometry puts them: left
        edge at ``margin_left``, centre at the page centre, right edge
        at ``page_width - margin_right``.  (fpdf aligns text within the
        cell, so a full-width cell with the right alignment IS the
        intended "x = ..." anchor.)  The row is vertically centred on
        *y_center_mm* — top margin /2 for headers, page bottom minus
        bottom margin /2 for footers.

        Slots may be :class:`_SlotDraw` (painted as text) or
        :class:`_LogoDraw` (painted as an image by
        :meth:`_draw_logo_slot`); both happen inside this row's cursor
        save/restore: fpdf2 does not restore it after calling
        ``header()``, so without this the body would start wherever the
        header row ended.
        """
        usable_w = self.w - self._margin_left_mm - self._margin_right_mm
        saved_x, saved_y = self.get_x(), self.get_y()
        for slot, align in ((slots[0], "L"), (slots[1], "C"), (slots[2], "R")):
            if slot is None:
                continue
            if isinstance(slot, _LogoDraw):
                self._draw_logo_slot(slot, align=align, y_center_mm=y_center_mm)
                continue
            line_h_mm = slot.font_size * 0.352778  # pt → mm
            # Only the family name changes (resolved header font);
            # the bold modifier logic stays as-is.
            self.set_font(self.header_font, "B" if slot.bold else "", slot.font_size)
            self.set_xy(self._margin_left_mm, y_center_mm - line_h_mm / 2)
            self.cell(usable_w, line_h_mm, slot.text, align=align)
        self.set_xy(saved_x, saved_y)

    def _draw_logo_slot(
        self, slot: _LogoDraw, *, align: str, y_center_mm: float
    ) -> None:
        """Paint a resolved logo image centred on *y_center_mm*.

        The natural pixel size is read by opening the image with PIL —
        nothing more.  ``height_mm`` (width × img_h / img_w) positions
        the y anchor; the drawn extent needs no explicit ``h=`` because
        fpdf2 computes the other dimension from the given width while
        preserving the aspect ratio (its documented "one explicit
        dimension" rule — the very ratio used for ``height_mm``).

        x anchors: ``margin_left`` for left, page centre for centre,
        ``page_width - margin_right - width`` for right.  An unreadable
        image is skipped silently — a logo must never fail the export.
        """
        from PIL import Image

        try:
            with Image.open(slot.path) as img:
                img_w, img_h = img.size
            if img_w <= 0 or img_h <= 0:
                return
            height_mm = slot.width_mm * (img_h / img_w)
            if align == "L":
                x = self._margin_left_mm
            elif align == "R":
                x = self.w - self._margin_right_mm - slot.width_mm
            else:
                x = (self.w - slot.width_mm) / 2
            y = y_center_mm - height_mm / 2
            self.image(str(slot.path), x=x, y=y, w=slot.width_mm)
        except Exception:
            return


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_screenshots(
    screenshot_paths_by_recording: dict[str, list[str]],
) -> None:
    """Raise LayoutPDFExportError if any screenshot file is missing."""
    missing: list[str] = []
    for paths in screenshot_paths_by_recording.values():
        for p in paths:
            if not Path(p).is_file():
                missing.append(p)
    if missing:
        names = "\n".join(f"  {p}" for p in missing)
        raise LayoutPDFExportError(
            f"Screenshot file(s) not found ({len(missing)}):\n{names}"
        )


def _validate_destination(path: Path) -> None:
    """Raise LayoutPDFExportError if destination already exists."""
    if path.exists():
        raise LayoutPDFExportError(
            f"File already exists: {path}\n"
            "Delete it first or choose a different name."
        )


def _validate_text_encoding(
    document: ResolvedExportDocument,
    extra_texts: list[tuple[str, str]] | None = None,
) -> None:
    """Raise LayoutPDFExportError if any user text contains non-latin-1 chars.

    Built-in PDF fonts (Helvetica, etc.) only support latin-1 (0x00–0xFF).
    Text outside this range would be silently replaced with ``?``, which
    breaks consistency with Preview and XLSX.  Instead we fail early with
    a clear message showing up to 3 offending examples.

    *extra_texts* carries already-resolved header/footer slot strings
    (text, location) so page-chrome text obeys the same policy as the
    body.
    """
    _MAX_CODEPOINT = 0xFF
    examples: list[str] = []
    _MAX_EXAMPLES = 3

    def _check_text(text: str, location: str) -> None:
        nonlocal examples
        if len(examples) >= _MAX_EXAMPLES:
            return
        for ch in text:
            if ord(ch) > _MAX_CODEPOINT:
                snippet = text[:40]
                examples.append(f'  "{snippet}"  ({location})')
                break

    for text, location in extra_texts or []:
        _check_text(text, location)

    for section in document.show_sections:
        loc_show = f'show "{section.show_name}"'
        for line in section.header_lines:
            for elem in line.elements:
                _check_text(elem.text, f'{loc_show}, header')
        for resolved_rec in section.recordings:
            loc_rec = (
                f'{loc_show}, recording '
                f'"{Path(resolved_rec.recording.source_path).name}"'
            )
            for line in resolved_rec.lines:
                for elem in line.elements:
                    _check_text(elem.text, loc_rec)

    if examples:
        joined = "\n".join(examples)
        suffix = ""
        if len(examples) == _MAX_EXAMPLES:
            suffix = "\n  ...and possibly more."
        raise LayoutPDFExportError(
            "PDF export cannot render non-latin-1 characters. "
            "The following text could not be rendered:\n"
            f"{joined}{suffix}\n"
            "Consider simplifying the text or exporting to XLSX instead."
        )


# ---------------------------------------------------------------------------
# Image helpers (kept local to this exporter)
# These are duplicated rather than shared so each module stays
# self-contained with no cross-module coupling.
# ---------------------------------------------------------------------------

def _image_dimensions(path: Path) -> tuple[int, int]:
    """Return (width, height) in pixels of an image file.

    Raises LayoutPDFExportError for unreadable/corrupt images — the
    exporter's contract is to never leak raw PIL errors to callers
    (a leaked UnidentifiedImageError would otherwise escape every
    handler and die silently in the Qt slot).
    """
    from PIL import Image

    try:
        with Image.open(path) as img:
            return img.size
    except Exception as exc:
        raise LayoutPDFExportError(
            f"Screenshot file is not a valid image: {path}\n{exc}"
        ) from exc


def _fit_image(
    img_w: int, img_h: int, max_w: float, max_h: float
) -> tuple[float, float]:
    """Scale image to fit within max_w × max_h, preserving aspect ratio."""
    if img_w <= 0 or img_h <= 0:
        return max_w, max_h
    scale = min(max_w / img_w, max_h / img_h)
    if scale >= 1.0:
        return float(img_w), float(img_h)
    return img_w * scale, img_h * scale


def _compute_row_max_heights(
    cells: list[GridCell],
    cell_w: float,
    max_h: float,
) -> dict[int, float]:
    """Compute the maximum scaled height per row across all cells."""
    row_heights: dict[int, float] = {}
    for cell in cells:
        img_w, img_h = _image_dimensions(Path(cell.screenshot_path))
        _, scaled_h = _fit_image(img_w, img_h, cell_w, max_h)
        prev = row_heights.get(cell.row, 0.0)
        if scaled_h > prev:
            row_heights[cell.row] = scaled_h
    return row_heights


# ---------------------------------------------------------------------------
# Element rendering
# ---------------------------------------------------------------------------

# A "#RRGGBB" string, the only colour form the exporter accepts.
# Named colours and 3-digit hex are deliberately not
# parsed — anything else falls back to black / no highlight.
_HEX6_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _hex_to_rgb(value) -> tuple[int, int, int] | None:
    """Parse "#RRGGBB" into an (r, g, b) int triple; None when invalid."""
    if not isinstance(value, str) or _HEX6_RE.match(value) is None:
        return None
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


def _apply_style(pdf, style, page_config) -> None:
    """Apply an ElementStyle to the PDF context.

    The font family is the body font
    :func:`export_layout_pdf` resolved and registered on *pdf*
    (``PageConfig.body_font`` when it could be embedded, Helvetica
    otherwise); *page_config* only covers direct calls with a bare
    FPDF.  The default size is 10 pt.  The foreground colour is set
    from ``style.color`` when it is a valid "#RRGGBB" and reset to
    black otherwise — the reset is what keeps an uncoloured run from
    inheriting the previous one.
    """
    family = getattr(pdf, "body_font", None) or getattr(
        page_config, "body_font", "Helvetica"
    )
    size = 10

    if style is not None:
        if style.font_size is not None:
            size = style.font_size

        weight = "B" if style.bold else ""
        if style.italic:
            # fpdf2 uses "BI" for bold-italic
            weight = f"{weight}I".strip() or "I"
        pdf.set_font(family, weight, size)

        rgb = _hex_to_rgb(getattr(style, "color", None))
        if rgb is None:
            pdf.set_text_color(0, 0, 0)
        else:
            pdf.set_text_color(*rgb)
    else:
        pdf.set_font(family, "", size)
        pdf.set_text_color(0, 0, 0)


def _style_runs(line) -> list[tuple[str, object]]:
    """Group a ResolvedLine's elements into same-style runs.

    Consecutive elements sharing the same ``(bold, italic, color,
    highlight_color)`` stamp become one ``(text, style)`` run — the
    colour fields are part of the key so a run never merges across
    a colour boundary.  Within a run the last non-None style wins
    (mirroring the line's dominant-style rule at run granularity).
    An unstyled line yields a single run.
    """
    def key(style) -> tuple:
        if style is None:
            return (False, False, None, None)

        def colour(value):
            return value if _hex_to_rgb(value) is not None else None

        return (
            bool(style.bold),
            bool(style.italic),
            colour(getattr(style, "color", None)),
            colour(getattr(style, "highlight_color", None)),
        )

    runs: list[tuple[str, object]] = []
    for elem in line.elements:
        flags = key(elem.style)
        if runs:
            prev_text, prev_style = runs[-1]
            if key(prev_style) == flags:
                runs[-1] = (
                    prev_text + elem.text,
                    elem.style if elem.style is not None else prev_style,
                )
                continue
        runs.append((elem.text, elem.style))
    return runs or [("", None)]


def _split_screenshot_segments(line) -> list[tuple[str, object]]:
    """Split a resolved line into text/screenshot segments.

    Returns ``("text", ResolvedLine)`` and ``("screenshot",
    ResolvedElement)`` items in order; a line without screenshot
    elements yields exactly one text segment.  Duplicated from the
    preview widget rather than shared, matching this exporter's
    self-contained-helpers convention.
    """
    segments: list[tuple[str, object]] = []
    text_elems = []
    for elem in line.elements:
        if getattr(elem, "kind", "text") == "screenshot":
            if text_elems:
                segments.append(("text", ResolvedLine(elements=text_elems)))
                text_elems = []
            segments.append(("screenshot", elem))
        else:
            text_elems.append(elem)
    if text_elems:
        segments.append(("text", ResolvedLine(elements=text_elems)))
    return segments


def _render_resolved_line(
    pdf,
    line,
    usable_w: float,
    page_config,
    recording=None,
    screenshot_config=None,
) -> None:
    """Render a ResolvedLine, expanding any inline [Screenshot:N].

    A line containing screenshot elements is split into text
    runs and screenshot blocks; the text runs render as before and each
    screenshot block lays out images from *recording*'s
    ``screenshot_paths``.  Both context arguments are optional — header
    lines pass none, and a screenshot block without them is skipped.
    """
    segments = _split_screenshot_segments(line)
    if len(segments) == 1 and segments[0][0] == "text":
        _render_text_line(pdf, segments[0][1], usable_w, page_config)
        return
    for kind, payload in segments:
        if kind == "text":
            _render_text_line(pdf, payload, usable_w, page_config)
        else:
            _render_screenshot_block(
                pdf, payload, usable_w, page_config, recording, screenshot_config,
            )


def _render_text_line(
    pdf,
    line,
    usable_w: float,
    page_config,
) -> None:
    """Render a text-only ResolvedLine (concatenated text) as a cell.

    Assumes text has already been validated for latin-1 compatibility
    by :func:`_validate_text_encoding`.

    Elements carry per-element bold/italic styles.  A line
    whose runs share one style (the common case, including all plain
    lines) draws as a single ``multi_cell`` with that font.  A
    mixed-style line draws its runs INLINE on one row via
    measured ``cell`` calls, switching the fpdf font style per run
    (``_apply_style`` resets to the layout default for unstyled runs).
    The per-run wrap is a row-level approximation: an over-long run
    moves to the next row whole rather than splitting mid-run.
    Each run is drawn by :func:`_draw_element_with_style`,
    which also paints the ``highlight_color`` rect behind the run and
    sets the ``color`` foreground.
    """
    runs = _style_runs(line)
    line_h_mm = 5
    if len(runs) == 1:
        text, style = runs[0]
        _draw_element_with_style(
            pdf, text, style, pdf.l_margin, pdf.get_y(), line_h_mm,
            page_config, width_mm=usable_w, wrap=True,
        )
        return

    max_x = pdf.l_margin + usable_w
    x = pdf.l_margin
    y = pdf.get_y()
    for text, style in runs:
        if not text:
            continue
        # The font must be current before the run can be measured.
        _apply_style(pdf, style, page_config)
        run_w = pdf.get_string_width(text)
        if x + run_w > max_x and x > pdf.l_margin:
            x = pdf.l_margin
            y += line_h_mm
        _draw_element_with_style(
            pdf, text, style, x, y, line_h_mm, page_config, width_mm=run_w,
        )
        x += run_w
    pdf.set_xy(pdf.l_margin, y + line_h_mm)
    pdf.set_text_color(0, 0, 0)


def _draw_highlight_rect(
    pdf,
    x: float,
    y: float,
    width_mm: float,
    height_mm: float,
    highlight_color,
) -> bool:
    """Paint a filled rect behind a run.

    The cursor is saved and restored around the draw, so the rect can
    never shift the drawing position.  An invalid colour or a
    non-positive size draws nothing and returns False.
    """
    rgb = _hex_to_rgb(highlight_color)
    if rgb is None or width_mm <= 0 or height_mm <= 0:
        return False
    saved_x, saved_y = pdf.get_x(), pdf.get_y()
    pdf.set_fill_color(*rgb)
    pdf.rect(x, y, width_mm, height_mm, style="F")
    pdf.set_xy(saved_x, saved_y)
    return True


def _draw_element_with_style(
    pdf,
    text: str,
    style,
    x: float,
    y: float,
    line_h_mm: float,
    page_config,
    *,
    width_mm: float,
    wrap: bool = False,
) -> None:
    """Draw one run at (x, y): foreground colour, then highlight, then text.

    Order matters: the font/foreground is applied first so
    the run can be measured, the highlight rect is painted behind the
    measured width, the cursor is put back to (x, y), and only then is
    the text written — the rect is therefore always behind the text and
    never moves it.  ``wrap`` draws through ``multi_cell`` (the
    plain-line path); otherwise an inline ``cell`` of *width_mm* is
    used.  The text colour is reset to black once the run is on the
    page, so no later run can inherit it.
    """
    _apply_style(pdf, style, page_config)
    highlight = (
        getattr(style, "highlight_color", None) if style is not None else None
    )
    if highlight is not None:
        # Tight to the measured text; a wrapping run is capped at the
        # line width (its later lines stay unhighlighted).
        rect_w = min(pdf.get_string_width(text), width_mm)
        _draw_highlight_rect(pdf, x, y, rect_w, line_h_mm, highlight)
    pdf.set_xy(x, y)
    if wrap:
        pdf.multi_cell(width_mm, line_h_mm, text, new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.cell(width_mm, line_h_mm, text)
    pdf.set_text_color(0, 0, 0)


_PX_TO_MM = 0.264583  # px -> mm at 96 DPI
_PDF_SHOT_GRID_COLUMNS = 3  # ScreenshotConfig carries no photos_per_row


def _shot_spacing_mm(screenshot_config) -> float:
    """Configured inter-image gap in mm (L15.9.3)."""
    return screenshot_config.spacing_px * 25.4 / 96.0


def _shot_block_x(
    screenshot_config, margin_left: float, usable_w: float, block_w: float,
) -> float:
    """Left edge of a screenshot block per the configured alignment."""
    align = getattr(screenshot_config, "align", "left")
    if align == "center":
        return margin_left + (usable_w - block_w) / 2.0
    if align == "right":
        return margin_left + usable_w - block_w
    return margin_left


def _draw_shot_border(
    pdf, screenshot_config, x: float, y: float, img_w: float, img_h: float,
) -> None:
    """Stroke a rectangle behind one image when a border is configured.

    fpdf2 cannot draw rounded rectangles, so ``corner_radius_px`` is
    ignored here — the PDF always draws a square rect.
    """
    border_mm = screenshot_config.border_width_px * 25.4 / 96.0
    if border_mm <= 0:
        return
    rgb = _hex_to_rgb(screenshot_config.border_color) or (204, 204, 204)
    pdf.set_draw_color(*rgb)
    pdf.set_line_width(border_mm)
    pdf.rect(x, y, img_w, img_h, style="D")
    # Leave the default draw state for anything that follows.
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.2)


def _render_screenshot_block(
    pdf,
    elem,
    usable_w: float,
    page_config,
    recording,
    screenshot_config,
) -> None:
    """Draw an inline [Screenshot:N] block.

    Lays out up to N of *recording*'s screenshot paths in the same
    row/grid the preview uses, scaled from
    :class:`ScreenshotConfig`'s pixel sizes (converted to mm at 96 DPI)
    with the aspect ratio preserved.  Fewer paths than N draw only what
    exists (missing ones are silently skipped, with no placeholder
    text); zero paths skip the block entirely without advancing the
    cursor.  The cursor is restored to the left margin after the block.

    L15.9.3: the gap between images comes from ``spacing_px``, a
    ``border_width_px > 0`` strokes a rectangle behind every image, and
    ``align`` shifts the whole block within the content column.  fpdf2
    cannot draw rounded rectangles, so ``corner_radius_px`` falls back
    to a square rect here.
    """
    if recording is None or screenshot_config is None:
        return
    n = int(elem.content)
    if n <= 0:
        return

    existing = [
        p for p in list(recording.screenshot_paths)[:n] if Path(p).is_file()
    ]
    if not existing:
        return

    max_w_mm = max(1.0, screenshot_config.max_width_px * _PX_TO_MM)
    max_h_mm = max(1.0, screenshot_config.max_height_px * _PX_TO_MM)
    spacing_mm = _shot_spacing_mm(screenshot_config)
    cols = (
        _PDF_SHOT_GRID_COLUMNS
        if screenshot_config.layout == "grid"
        else len(existing)
    )
    cols = max(1, min(cols, len(existing)))
    cell_w = min(
        max_w_mm,
        (usable_w - spacing_mm * (cols - 1)) / cols,
    )

    margin_top, margin_right, margin_bottom, margin_left = (
        page_config.effective_margins_mm()
    )
    page_h = page_config.height_mm
    bottom_limit = page_h - margin_bottom

    saved_x, saved_y = pdf.get_x(), pdf.get_y()
    final_y = saved_y
    for row_start in range(0, len(existing), cols):
        chunk = existing[row_start:row_start + cols]
        sizes: list[tuple[str, float, float]] = []
        for path in chunk:
            try:
                img_w, img_h = _image_dimensions(Path(path))
            except LayoutPDFExportError:
                continue  # unreadable image never fails the export
            scale = min(cell_w / img_w, max_h_mm / img_h, 1.0) \
                if img_w > 0 and img_h > 0 else 1.0
            sizes.append((path, img_w * scale, img_h * scale))
        if not sizes:
            continue

        row_h = max(sh for _, _, sh in sizes)
        if pdf.get_y() + row_h + spacing_mm > bottom_limit:
            pdf.add_page()
        y = pdf.get_y()
        # The row occupies its full cell width for alignment purposes.
        block_w = len(sizes) * cell_w + (len(sizes) - 1) * spacing_mm
        origin_x = _shot_block_x(screenshot_config, margin_left, usable_w, block_w)
        for col, (path, sw, sh) in enumerate(sizes):
            x = origin_x + col * (cell_w + spacing_mm)
            _draw_shot_border(pdf, screenshot_config, x, y, sw, sh)
            pdf.image(path, x=x, y=y, w=sw)
        final_y = y + row_h + spacing_mm
        pdf.set_y(final_y)

    # Restore the cursor to the left margin below the block so any text
    # that follows starts where a normal line would.
    pdf.set_xy(margin_left, final_y if final_y > saved_y else saved_y)


# ---------------------------------------------------------------------------
# Font resolution
# ---------------------------------------------------------------------------

# family name → (ttf path, family) or None.  Resolved AND unresolved
# lookups are cached so repeated exports in one session do not re-scan
# the font directories.
_FONT_CACHE: dict[str, tuple[Path, str] | None] = {}


def _font_search_dirs() -> tuple[tuple[Path, bool], ...]:
    """(directory, recursive) pairs scanned for ``*.ttf`` on this OS.

    Windows keeps its fonts flat in ``%WINDIR%\\Fonts``; macOS splits
    them between ``/Library/Fonts`` (admin-installed) and
    ``/System/Library/Fonts`` (system), both walked recursively to
    catch subdirectories; Linux distributions install under
    ``/usr/share/fonts`` in per-package trees, also recursive.
    """
    if sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        return ((windir / "Fonts", False),)
    if sys.platform == "darwin":
        return (
            (Path("/Library/Fonts"), True),
            (Path("/System/Library/Fonts"), True),
        )
    return ((Path("/usr/share/fonts"), True),)


def _read_tt_names(path: Path) -> tuple[str | None, str | None]:
    """``(family, subfamily)`` from *path*'s name table via fontTools.

    Family prefers name ID 16 (typographic family) then 1 (legacy);
    subfamily is name ID 2.  Any parse failure yields ``(None, None)``.
    """
    font = None
    try:
        font = _TTFont(str(path), lazy=True)
        name_table = font["name"]
        family = name_table.getDebugName(16) or name_table.getDebugName(1)
        subfamily = name_table.getDebugName(2)
        return (str(family) if family else None,
                str(subfamily) if subfamily else None)
    except Exception:
        return None, None
    finally:
        if font is not None:
            try:
                font.close()
            except Exception:
                pass


def _match_family(files: list[Path], family: str) -> Path | None:
    """The best ``*.ttf`` among *files* for *family*.

    With fontTools available the internal name table is authoritative:
    the first file whose family matches case-insensitively wins, with a
    ``Regular`` subfamily preferred when several faces share the family
    (so an embedded bold file is not mistaken for the regular face).
    Without fontTools the filename heuristic runs — three passes, in
    order: exact ``{family}.ttf``, exact ``{family with spaces
    removed}.ttf``, then the ``{family}*`` glob prefix.  Documented
    limitation: a heuristic match trusts the filename, so abbreviated
    vendor names (Windows' ``cour.ttf`` for "Courier New") only hit via
    the prefix pass.
    """
    target = family.lower()
    if _TTFont is not None:
        first = None
        for path in files:
            internal, subfamily = _read_tt_names(path)
            if internal is None or internal.lower() != target:
                continue
            if (subfamily or "").lower() == "regular":
                return path
            if first is None:
                first = path
        return first

    tight = target.replace(" ", "")
    for path in files:
        if path.stem.lower() == target:
            return path
    for path in files:
        if path.stem.lower() == tight:
            return path
    for path in files:
        if path.stem.lower().startswith(target):
            return path
    return None


def _resolve_font(family_name: str) -> tuple[Path, str] | None:
    """Return ``(ttf_path, family_name_for_fpdf)`` if the font can be
    found on this system, or ``None`` if it cannot.

    Callers must fall back to Helvetica on ``None``.  ``"Helvetica"``
    itself never resolves: it is an fpdf built-in core font, never a
    TTF file on disk.  Results (including misses) are cached per
    family name.
    """
    if not family_name or family_name.lower() == "helvetica":
        return None
    if family_name in _FONT_CACHE:
        return _FONT_CACHE[family_name]

    result: tuple[Path, str] | None = None
    for directory, recursive in _font_search_dirs():
        if not directory.is_dir():
            continue
        pattern = "**/*.ttf" if recursive else "*.ttf"
        files = sorted(p for p in directory.glob(pattern) if p.is_file())
        match = _match_family(files, family_name)
        if match is not None:
            result = (match, family_name)
            break

    _FONT_CACHE[family_name] = result
    return result


def _register_font(pdf, family_name: str, resolved, role: str) -> str:
    """Register *resolved* on *pdf*; return the family to draw with.

    Helvetica when the family is the fpdf built-in, when the system has
    no matching TTF (logged — the user picked a font this machine does
    not have), or when fpdf2 rejects the file.  The two roles (body,
    header) resolve independently.
    """
    if resolved is None:
        if family_name and family_name.lower() != "helvetica":
            get_logger().warning(
                f'Font "{family_name}" for {role} text was not found on '
                "this system; falling back to Helvetica."
            )
        return "Helvetica"
    path, family = resolved
    try:
        # fpdf2 keys fonts by family+style and raises on an unknown
        # key, so the one resolved file must be registered under every
        # style the exporter's set_font() calls use ("B"/"I" modifiers
        # stay unchanged).  No separate bold/italic files are
        # available, so each key embeds the same face.
        for style in ("", "B", "I", "BI"):
            pdf.add_font(family, style, str(path))
    except Exception:
        get_logger().warning(
            f'Font "{family_name}" for {role} text could not be loaded '
            f"from {path}; falling back to Helvetica."
        )
        return "Helvetica"
    return family


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------

def export_layout_pdf(
    document: ResolvedExportDocument,
    output_path: str | Path,
    *,
    screenshot_paths_by_recording: dict[str, list[str]] | None = None,
) -> Path:
    """Generate a PDF from a fully-resolved export document.

    Parameters
    ----------
    document:
        The resolved document (already resolved by LayoutResolver).
    output_path:
        Destination for the PDF file.
    screenshot_paths_by_recording:
        Optional mapping of ``recording.source_path`` → list of
        screenshot file paths.  When ``None``, no screenshots are
        rendered and no filesystem access occurs.

    Returns
    -------
    Path
        Absolute path of the created PDF.

    Raises
    ------
    LayoutPDFExportError
        If validation fails or PDF generation errors.
    """
    output = Path(output_path)

    # --- Resolve header/footer ---
    # Resolution context: the first recording of the first non-empty
    # show section.  Documents without recordings skip both entirely.
    header_cfg = document.layout.header
    footer_cfg = document.layout.footer
    context_recording: ExportRecording | None = None
    for section in document.show_sections:
        if section.recordings:
            context_recording = section.recordings[0].recording
            break
    header_texts = _resolve_slot_row(header_cfg, context_recording)
    footer_texts = _resolve_slot_row(footer_cfg, context_recording)
    header_scope = header_cfg.scope if header_cfg is not None else "all"

    # Latin-1 validation covers resolved TEXT only — logos are binary
    # and are not validated.
    extra_texts = [
        (slot.text, f"page {label}")
        for label, row in (("header", header_texts), ("footer", footer_texts))
        for slot in (row or ())
        if isinstance(slot, _SlotDraw)
    ]

    # --- Validate ---
    _validate_destination(output)
    _validate_text_encoding(document, extra_texts=extra_texts)
    if screenshot_paths_by_recording:
        _validate_screenshots(screenshot_paths_by_recording)

    page_config = document.layout.page_config
    screenshot_config = document.layout.screenshot_config
    separator = document.separator

    margin_top, margin_right, margin_bottom, margin_left = (
        page_config.effective_margins_mm()
    )
    page_w = page_config.width_mm
    page_h = page_config.height_mm
    usable_w = page_w - margin_left - margin_right

    # --- Create PDF ---
    # Fonts resolve BEFORE the instance exists (fpdf needs file paths,
    # not family names); registration happens on the instance, before
    # the first add_page() can trigger a header()/footer() draw.
    body_font_file = _resolve_font(page_config.body_font)
    header_font_file = _resolve_font(page_config.header_font)

    # _LayoutPDF sets margins, auto page break, and the page format
    # itself from page_config, and paints the resolved header/footer
    # rows via fpdf's header()/footer() hooks.
    pdf = _LayoutPDF(
        header_texts=header_texts,
        footer_texts=footer_texts,
        header_scope=header_scope,
        page_config=page_config,
    )
    pdf.body_font = _register_font(
        pdf, page_config.body_font, body_font_file, "body"
    )
    pdf.header_font = _register_font(
        pdf, page_config.header_font, header_font_file, "header"
    )
    pdf.add_page()

    for section_idx, section in enumerate(document.show_sections):
        # Page break between sections (not before the first)
        if section_idx > 0:
            # Separator line — use only latin-1 safe characters
            if separator:
                pdf.ln(4)
                _apply_style(pdf, None, page_config)
                safe_sep = separator.encode("latin-1", errors="replace").decode("latin-1")
                pdf.cell(
                    usable_w, 6,
                    safe_sep * 60,
                    new_x="LMARGIN", new_y="NEXT",
                )
                pdf.ln(2)

            pdf.add_page()

        # --- Section bookmark/outline ---
        try:
            pdf.start_section(name=section.show_name, level=0)
        except AttributeError:
            # fpdf2 < 5.x may not have start_section
            pass

        # --- Header lines ---
        for line in section.header_lines:
            _render_resolved_line(pdf, line, usable_w, page_config)
        pdf.ln(2)

        # --- Recordings ---
        for rec_idx, resolved_rec in enumerate(section.recordings):
            # Inter-recording spacing
            if rec_idx > 0:
                pdf.ln(4)

            # Render recording body lines
            for line in resolved_rec.lines:
                _render_resolved_line(
                    pdf, line, usable_w, page_config,
                    recording=resolved_rec.recording,
                    screenshot_config=screenshot_config,
                )

            # --- Screenshots ---
            if (
                screenshot_paths_by_recording is not None
                and screenshot_config.enabled
            ):
                src_path = resolved_rec.recording.source_path
                screenshots = screenshot_paths_by_recording.get(src_path, [])
                if screenshots:
                    pdf.ln(2)
                    _render_screenshots(
                        pdf,
                        screenshots,
                        usable_w,
                        page_w,
                        page_h,
                        margin_left,
                        margin_bottom,
                        screenshot_config,
                    )

    # --- Write to temp then move ---
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".pdf", dir=str(output.parent),
        )
        try:
            os.close(tmp_fd)
            pdf.output(tmp_path)
            Path(tmp_path).replace(output)
        except Exception:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
            raise
    except LayoutPDFExportError:
        raise
    except Exception as exc:
        raise LayoutPDFExportError(
            f"Failed to generate PDF: {exc}"
        ) from exc

    return output.resolve()


# ---------------------------------------------------------------------------
# Screenshot rendering
# ---------------------------------------------------------------------------

def _render_screenshots(
    pdf,
    screenshot_paths: list[str],
    usable_w: float,
    page_w: float,
    page_h: float,
    margin_left: float,
    margin_bottom: float,
    screenshot_config,
) -> None:
    """Render a list of screenshot images in a grid/row.

    L15.9.3: the gap comes from ``spacing_px``, a ``border_width_px >
    0`` strokes a rectangle behind every image, and ``align`` shifts
    the block within the content column.  fpdf2 cannot draw rounded
    rectangles, so ``corner_radius_px`` falls back to a square rect.
    """
    # Build grid cells
    photos_per_row = (
        3 if screenshot_config.layout == "grid" else len(screenshot_paths)
    )
    layout = ScreenshotLayout(photos_per_row=photos_per_row)
    cells = compute_grid(screenshot_paths, layout)
    if not cells:
        return

    spacing = _shot_spacing_mm(screenshot_config)  # mm between images
    cols = photos_per_row
    cell_w = (usable_w - spacing * (cols - 1)) / cols
    max_h = screenshot_config.max_height_px * 0.264583  # px → mm @ 96 dpi

    # Pre-compute the tallest scaled image per row
    row_heights = _compute_row_max_heights(cells, cell_w, max_h)

    for cell in cells:
        img_path = Path(cell.screenshot_path)
        img_w, img_h = _image_dimensions(img_path)
        scaled_w, scaled_h = _fit_image(img_w, img_h, cell_w, max_h)

        row_h = row_heights[cell.row]
        needed = row_h + spacing

        # Page break check
        if pdf.get_y() + needed > page_h - margin_bottom:
            pdf.add_page()

        # The last grid row may be partial; align against what it holds.
        row_cells = [c for c in cells if c.row == cell.row]
        block_w = len(row_cells) * cell_w + (len(row_cells) - 1) * spacing
        origin_x = _shot_block_x(screenshot_config, margin_left, usable_w, block_w)
        x = origin_x + cell.col * (cell_w + spacing)
        y = pdf.get_y()

        _draw_shot_border(pdf, screenshot_config, x, y, scaled_w, scaled_h)
        pdf.image(str(img_path), x=x, y=y, w=scaled_w, h=scaled_h)

        # After last column in row, advance Y
        if cell.col == cols - 1 or cell == cells[-1]:
            pdf.set_y(y + row_h + spacing)
