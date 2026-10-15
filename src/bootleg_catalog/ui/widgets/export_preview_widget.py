"""Preview widget for the Export Preview window.

Renders a :class:`ResolvedExportDocument` as Qt widgets on a white
"page" frame sized from the configured physical page geometry
(:class:`PageConfig`), wrapped in a grey desk — a visual stand-in for
the exported PDF.  A zoom control offers Fit Width / 100% / Custom
scales, and content that would overflow a page is wrapped onto
additional stacked page frames with a visible page-break marker.

Configured page headers/footers are drawn as small text bands
inside each page frame's top/bottom margin area.  Inline
[Screenshot:N] elements render as pixmaps (or grey placeholder rows
when the files are missing).

Pagination here is a deliberate *visual approximation*: per-item
heights are estimated from font metrics, so the preview is NOT exact
WYSIWYG with the PDF/XLSX exporters, which paginate for real.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bootleg_catalog.core.field_resolver import ResolvedElement, ResolvedLine
from bootleg_catalog.core.layout_pdf_exporter import (
    resolve_logo_slot,
    resolve_text_slot,
)
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FooterConfig,
    HeaderConfig,
    LogoSlot,
    PageConfig,
    ScreenshotConfig,
)
from bootleg_catalog.models.resolved_export import (
    ResolvedExportDocument,
    ResolvedRecording,
)


# ---------------------------------------------------------------------------
# Physical-to-pixel mapping (96 DPI)
# ---------------------------------------------------------------------------

_DPI = 96
_MM_PER_INCH = 25.4

# Named page sizes in millimetres (width x height, portrait).
_PAGE_SIZES_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "Letter": (216.0, 279.0),
    "A3": (297.0, 420.0),
}


def page_size_px(page_size: str, orientation: str) -> tuple[int, int]:
    """Return ``(width, height)`` in px of *page_size* at 96 DPI.

    Recognised sizes are the keys of the millimetre table: ``"A4"``
    (794x1123 px), ``"Letter"`` (816x1054 px) and ``"A3"``
    (1123x1587 px).  *orientation* swaps the axes for ``"landscape"``;
    anything else renders portrait.  Values are rounded from the mm
    dimensions at 96 DPI (e.g. A4 210x297 mm).
    """
    width_mm, height_mm = _PAGE_SIZES_MM[page_size]
    width = round(width_mm / _MM_PER_INCH * _DPI)
    height = round(height_mm / _MM_PER_INCH * _DPI)
    if orientation.lower() == "landscape":
        return height, width
    return width, height


def _mm_to_px(mm: float) -> float:
    """Convert millimetres to px at 96 DPI."""
    return mm / _MM_PER_INCH * _DPI


def _estimate_height_px(text: str, font: QFont, content_width_px: int) -> int:
    """Rough rendered height of *text* in *font* wrapped to the content width.

    Approximation for pagination only: a ceil division of the text's
    advance width by the content width estimates the wrapped row count.
    """
    metrics = QFontMetrics(font)
    advance = metrics.horizontalAdvance(text) or 1
    rows = max(1, (advance + content_width_px - 1) // max(1, content_width_px))
    return rows * metrics.height() + 2


# ---------------------------------------------------------------------------
# Inline [Screenshot:N] blocks
# ---------------------------------------------------------------------------

_SCREENSHOT_PLACEHOLDER_COLOR = "#9CA3AF"
# Configs predating photos_per_row get the fixed three-column default.
_SCREENSHOT_GRID_COLUMNS = 3


def _split_screenshot_segments(line: ResolvedLine) -> list[tuple[str, object]]:
    """Split a resolved line into renderable segments.

    Returns ``("text", ResolvedLine)`` and ``("screenshot",
    ResolvedElement)`` items in order.  A line with no screenshot
    elements yields exactly one text segment, so the common case takes
    the unchanged single-label path.
    """
    segments: list[tuple[str, object]] = []
    text_elems: list[ResolvedElement] = []
    for elem in line.elements:
        if elem.kind == "screenshot":
            if text_elems:
                segments.append(("text", ResolvedLine(elements=text_elems)))
                text_elems = []
            segments.append(("screenshot", elem))
        else:
            text_elems.append(elem)
    if text_elems:
        segments.append(("text", ResolvedLine(elements=text_elems)))
    return segments


def _fit_pixmap_dims(pix_w: int, pix_h: int, max_w: int, max_h: int) -> tuple[int, int]:
    """Dimensions of a *pix_w* x *pix_h* image fitted into max_w x max_h."""
    if pix_w <= 0 or pix_h <= 0:
        return max(1, max_w), max(1, max_h)
    ratio = min(max_w / pix_w, max_h / pix_h, 1.0)
    return max(1, round(pix_w * ratio)), max(1, round(pix_h * ratio))


# ---------------------------------------------------------------------------
# Visual constants (page metaphor)
# ---------------------------------------------------------------------------

_DESK_COLOR = "#F3F4F6"
_PAGE_BORDER_COLOR = "#E5E7EB"
_DESK_MARGIN_PX = 24          # grey desk padding around the page column
_FIT_WIDTH_INSET_PX = 8       # Fit Width leaves a sliver of desk at each side
_NARROW_WIDTH_PX = 900        # below this, show the "maximize" hint
_MIN_SCALE = 0.25
_MAX_SCALE = 4.0
_AUTO_ZOOM_MIN_SCALE = 0.5   # below this, Fit Width is unreadably small
_CUSTOM_MIN_PCT = 25
_CUSTOM_MAX_PCT = 200
_CUSTOM_STEP_PCT = 5
_RECORDING_GAP_PX = 4         # spacer between recordings (at 100%)
_ROW_GAP_PX = 4               # accounted below each flowed content item
_CHROME_TEXT_COLOR = "#374151"  # header/footer band text (darker than body grey)
_CHROME_MISSING_COLOR = "#9CA3AF"  # "missing logo" placeholder
_CHROME_MISSING_FONT_PT = 8.0      # placeholder size, scaled like other chrome

ZOOM_FIT_WIDTH = "Fit Width"
ZOOM_100 = "100%"
ZOOM_CUSTOM = "Custom\u2026"  # "Custom…"


# ---------------------------------------------------------------------------
# Font/color helpers
# ---------------------------------------------------------------------------

def _make_qfont(
    style: ElementStyle | None,
    fallback_size: int = 10,
    scale: float = 1.0,
) -> QFont:
    """Convert an ElementStyle to a QFont, scaled for the preview zoom."""
    font = QFont()
    base_size = float(style.font_size) if style and style.font_size else float(fallback_size)
    font.setPointSizeF(base_size * scale)
    font.setBold(style.bold if style else False)
    font.setItalic(style.italic if style else False)
    return font


_HEX6_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _valid_hex(value) -> str | None:
    """Return *value* when it is a "#RRGGBB" string, else None.

    Only the six-digit form is accepted — named colours and 3-digit
    hex are deliberately not parsed.
    """
    if isinstance(value, str) and _HEX6_RE.match(value) is not None:
        return value
    return None


def _make_qcolor(style: ElementStyle | None) -> QColor | None:
    """Extract a QColor from an ElementStyle, or None if no color."""
    if style is None:
        return None
    hex_color = _valid_hex(style.color)
    if hex_color is None:
        return None
    return QColor(hex_color)


def _line_font(line: ResolvedLine, fallback_size: int = 10, scale: float = 1.0) -> QFont:
    """The dominant (last styled) font used to render *line*."""
    last_style = None
    for elem in line.elements:
        if elem.style is not None:
            last_style = elem.style
    return _make_qfont(last_style, fallback_size=fallback_size, scale=scale)


def _render_line(
    parent: QWidget,
    line: ResolvedLine,
    *,
    font: QFont | None = None,
    scale: float = 1.0,
) -> QLabel:
    """Render a single ResolvedLine as a QLabel inside *parent*'s layout.

    When any element carries bold/italic, the line is drawn as
    rich text with ``<b>``/``<i>`` per element, so ``**bold** plain``
    bolds only the marked run.  Per-element foreground (``color``) and
    highlight (``highlight_color``) are drawn as spans.  Lines with
    no style at all keep the exact plain-text path.
    """
    text = "".join(elem.text for elem in line.elements)

    # Determine dominant style (last element with a style)
    last_style = None
    for elem in line.elements:
        if elem.style is not None:
            last_style = elem.style

    styled = [
        e for e in line.elements
        if e.style is not None and (
            e.style.bold or e.style.italic
            or _valid_hex(getattr(e.style, "color", None))
            or _valid_hex(getattr(e.style, "highlight_color", None))
        )
    ]
    if styled and last_style is not None and font is None:
        # The tags/spans carry the emphasis and colour; the base font
        # and the label-level stylesheet must not also carry them, or
        # the unmarked runs would inherit them.
        last_style = ElementStyle(
            font_size=last_style.font_size,
            bold=False,
            italic=False,
            color=None,
            highlight_color=None,
        )

    label = QLabel()
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    if font is not None:
        label.setFont(font)
    else:
        label.setFont(_make_qfont(last_style, scale=scale))

    if styled:
        parts: list[str] = []
        for elem in line.elements:
            frag = escape(elem.text)
            if elem.style is not None:
                # Nesting order, innermost → outermost: the
                # text, then <b>/<i>, then the foreground span, and the
                # highlight span is the OUTERMOST wrapper —
                #   <span style="background-color:#FFFF00;">
                #     <span style="color:#FF0000;"><b>text</b></span>
                #   </span>
                if elem.style.bold:
                    frag = f"<b>{frag}</b>"
                if elem.style.italic:
                    frag = f"<i>{frag}</i>"
                fg = _valid_hex(getattr(elem.style, "color", None))
                if fg:
                    frag = f'<span style="color:{fg};">{frag}</span>'
                bg = _valid_hex(getattr(elem.style, "highlight_color", None))
                if bg:
                    frag = f'<span style="background-color:{bg};">{frag}</span>'
            parts.append(frag)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText("".join(parts))
    else:
        label.setText(text)

    color = _make_qcolor(last_style)
    if color:
        label.setStyleSheet(f"color: {color.name()};")

    parent.layout().addWidget(label)  # type: ignore[union-attr]
    return label


# ---------------------------------------------------------------------------
# Header / footer band resolution
# ---------------------------------------------------------------------------

def _first_recording(doc: ResolvedExportDocument | None) -> ExportRecording | None:
    """The resolution context for header/footer slots.

    The first recording of the first non-empty show section; ``None``
    when the document has no recordings — header/footer are then
    skipped entirely, matching the PDF exporter.
    """
    if doc is None:
        return None
    for section in doc.show_sections:
        if section.recordings:
            return section.recordings[0].recording
    return None


# One resolved chrome slot, discriminated by the first
# element:
#   ("text", resolved_str, font_size, bold)  — TextSlot, markers resolved
#   ("logo", absolute_path | None, width_mm)  — LogoSlot (None = missing file)
_ChromeSlot = tuple
_ChromeRow = list[_ChromeSlot | None]


def _resolve_chrome_row(
    config: HeaderConfig | FooterConfig | None,
    recording: ExportRecording | None,
    prefix: str,
    pending_logos: dict[str, Path] | None = None,
) -> _ChromeRow | None:
    """Resolve a header/footer config's three slots against *recording*.

    Returns ``None`` when the config is absent, disabled, or there is
    no recording; empty text slots stay ``None``.  Mirrors the PDF
    exporter's rule, delegating marker resolution to its canonical
    :func:`resolve_text_slot` helper and logo resolution to
    :func:`resolve_logo_slot`.  A LogoSlot always resolves
    to a slot — ``("logo", None, width_mm)`` when its file is missing —
    so the band can show the "missing logo" placeholder.

    *prefix* (``"header"``/``"footer"``) plus the loop's deterministic
    side order give each slot its id.  When a LogoSlot's stored file is
    missing but its id is in *pending_logos* — a source image the
    designer chose but has not saved to the library yet —
    that pending path renders instead of the placeholder.  Disk always
    wins over pending.
    """
    if config is None or not config.enabled or recording is None:
        return None
    row: _ChromeRow = []
    for side, raw in zip(
        ("left", "center", "right"),
        (config.left, config.center, config.right),
    ):
        if raw is None:
            row.append(None)
            continue
        if isinstance(raw, LogoSlot):
            path = resolve_logo_slot(raw)
            if path is None and pending_logos:
                path = pending_logos.get(f"{prefix}.{side}")
            row.append(("logo", path, raw.width_mm))
            continue
        if not raw.text:
            row.append(None)
            continue
        text = resolve_text_slot(raw, recording)
        row.append(
            ("text", text, raw.font_size, raw.bold) if text else None
        )
    if not any(slot is not None for slot in row):
        return None
    return row


class PreviewWidget(QWidget):
    """Renders a :class:`ResolvedExportDocument` on a PDF-like page.

    The document is wrapped in one or more white "page" frames sized
    from the configured :class:`PageConfig` (96 DPI, so A4 portrait is
    794x1123 px at 100%), stacked vertically on a grey desk inside a
    scroll area.  A zoom combo (Fit Width / 100% / Custom…) controls
    the on-screen scale; content that would overflow a page is wrapped
    onto a new page frame with a "— Page N —" break marker between
    frames.  It is a readable visual approximation — not WYSIWYG with
    the exporters.  Screenshots are shown as text hints only.

    With *auto_zoom_fallback* the widget may drop the initial
    "Fit Width" mode for "100%" on the first :meth:`set_document` when
    Fit Width would render the page below 0.5x (narrow embedded
    panels, e.g. the Layout Designer's preview column).  An explicit
    zoom pick by the user disables the fallback for that instance.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        page_config: PageConfig | None = None,
        auto_zoom_fallback: bool = False,
    ) -> None:
        super().__init__(parent)
        self._document: ResolvedExportDocument | None = None
        self._page_config = page_config if page_config is not None else PageConfig()
        self._auto_zoom_fallback = auto_zoom_fallback
        self._auto_zoom_applied = False
        self._user_chose_zoom = False
        self._pages: list[QFrame] = []
        self._page: QFrame | None = None
        self._header_row: _ChromeRow | None = None
        self._footer_row: _ChromeRow | None = None
        self._header_scope = "all"
        # Inline [Screenshot:N] rendering context: the layout's
        # screenshot sizing, and the recording the current body line
        # belongs to (None for header lines, which have no recording).
        self._screenshot_config = ScreenshotConfig()
        self._active_recording: ExportRecording | None = None
        # Optional background fills (None = no fill), re-read from the
        # document's page config on every rebuild: the page frame, the
        # header/footer bands, the body content area.
        self._page_background: str | None = None
        self._header_background: str | None = None
        self._footer_background: str | None = None
        self._body_background: str | None = None
        # Slot id -> source image path chosen but not yet copied to the
        # library by LayoutManager.save().
        self._pending_logos: dict[str, Path] = {}

        self._build_chrome()
        self._rebuild()

    def set_document(self, document: ResolvedExportDocument) -> None:
        """Replace the displayed document and rebuild the view."""
        self._document = document
        self._apply_auto_zoom_fallback()
        self._rebuild()

    def set_pending_logos(self, pending: dict[str, Path] | None) -> None:
        """Set or clear the mapping of slot_id -> source image path that
        the designer holds before LayoutManager.save() copies them.
        When a LogoSlot has logo_file=None but a matching slot_id exists
        in this mapping, render the source path instead of the 'missing
        logo' placeholder.  Passing None or {} clears the mapping."""
        self._pending_logos = dict(pending) if pending else {}
        self._rebuild()

    def set_page_config(self, page_config: PageConfig) -> None:
        """Replace the page geometry and re-lay-out the current document."""
        self._page_config = page_config
        self._rebuild()

    # -- Chrome: toolbar, hint, scroll area, desk --------------------------

    def _build_chrome(self) -> None:
        """Build the fixed frame: zoom toolbar, hint, and scrolling desk."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _DESK_MARGIN_PX, _DESK_MARGIN_PX, _DESK_MARGIN_PX, _DESK_MARGIN_PX
        )
        layout.setSpacing(8)

        # --- Zoom toolbar ---
        toolbar = QHBoxLayout()
        self._zoom_combo = QComboBox(self)
        self._zoom_combo.setObjectName("zoomComboBox")
        self._zoom_combo.addItems([ZOOM_FIT_WIDTH, ZOOM_100, ZOOM_CUSTOM])
        self._zoom_combo.currentTextChanged.connect(self._on_zoom_changed)
        toolbar.addWidget(self._zoom_combo)

        self._zoom_spin = QSpinBox(self)
        self._zoom_spin.setObjectName("zoomSpin")
        self._zoom_spin.setRange(_CUSTOM_MIN_PCT, _CUSTOM_MAX_PCT)
        self._zoom_spin.setSingleStep(_CUSTOM_STEP_PCT)
        self._zoom_spin.setValue(100)
        self._zoom_spin.setSuffix("%")
        self._zoom_spin.valueChanged.connect(self._on_zoom_changed)
        self._zoom_spin.hide()  # only shown for "Custom…"
        toolbar.addWidget(self._zoom_spin)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # --- Narrow-window hint ---
        self._hint_label = QLabel(
            "Tip: maximize the window for a more accurate page preview.", self
        )
        self._hint_label.setObjectName("widthHint")
        self._hint_label.setStyleSheet("color: #6B7280; font-size: 11px;")
        layout.addWidget(self._hint_label)

        # --- Scrolling desk with the page column ---
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("pageScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {_DESK_COLOR}; border: none; }}"
        )
        self._scroll.viewport().setStyleSheet(f"background-color: {_DESK_COLOR};")

        self._desk = QWidget(self._scroll)
        self._desk.setStyleSheet(f"background-color: {_DESK_COLOR};")
        self._pages_box = QVBoxLayout(self._desk)
        self._pages_box.setContentsMargins(0, _DESK_MARGIN_PX, 0, _DESK_MARGIN_PX)
        self._pages_box.setSpacing(_DESK_MARGIN_PX)
        self._pages_box.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._scroll.setWidget(self._desk)

        layout.addWidget(self._scroll, 1)
        self._hint_label.setVisible(self.width() < _NARROW_WIDTH_PX)

    # -- Page geometry ------------------------------------------------------

    def _base_page_size_px(self) -> tuple[int, int]:
        """The configured page's 96-DPI pixel size at 100% zoom."""
        return (
            max(1, round(_mm_to_px(self._page_config.width_mm))),
            max(1, round(_mm_to_px(self._page_config.height_mm))),
        )

    def _current_scale(self) -> float:
        """The on-screen scale factor for the current zoom mode."""
        mode = self._zoom_combo.currentText()
        if mode == ZOOM_100:
            return 1.0
        if mode == ZOOM_CUSTOM:
            return self._zoom_spin.value() / 100.0
        # Fit Width: fill the scroll viewport minus a small desk inset.
        available = self._scroll.viewport().width() - _FIT_WIDTH_INSET_PX
        if available <= 0:
            return 1.0  # not laid out yet (hidden / offscreen)
        base_width, _ = self._base_page_size_px()
        return max(_MIN_SCALE, min(_MAX_SCALE, available / base_width))

    # -- Zoom --------------------------------------------------------------

    def _on_zoom_changed(self, *_: object) -> None:
        """Re-apply the zoom: toggle the spin box and re-render pages."""
        self._user_chose_zoom = True  # explicit pick disables auto-fallback
        self._zoom_spin.setVisible(self._zoom_combo.currentText() == ZOOM_CUSTOM)
        self._rebuild()

    def _apply_auto_zoom_fallback(self) -> None:
        """On the first document, drop Fit Width if it would render tiny.

        Evaluated once, on the first :meth:`set_document` call: if the
        widget was constructed with *auto_zoom_fallback* and Fit Width
        would scale the page below 0.5x at its current width, silently
        switch the mode to "100%" instead.  Never applies after the
        user has picked a zoom mode explicitly.
        """
        if self._auto_zoom_applied:
            return
        self._auto_zoom_applied = True
        if (
            not self._auto_zoom_fallback
            or self._user_chose_zoom
            or self._zoom_combo.currentText() != ZOOM_FIT_WIDTH
            or self._current_scale() >= _AUTO_ZOOM_MIN_SCALE
        ):
            return
        # Block signals: the switch is ours, not a user pick.
        self._zoom_combo.blockSignals(True)
        self._zoom_combo.setCurrentText(ZOOM_100)
        self._zoom_combo.blockSignals(False)

    # -- Rebuild / pagination ----------------------------------------------

    def _rebuild(self) -> None:
        """Clear the desk and re-render the document onto page frames."""
        # Detach each current page/marker from the desk hierarchy first,
        # then queue it for destruction. The desk's own layout is reused —
        # constructing a second QVBoxLayout would silently never attach
        # and every widget added to it would become an orphaned top-level
        # widget.
        while self._pages_box.count():
            item = self._pages_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            del item
        self._pages = []
        self._cursor = 0

        # Page geometry for the current zoom.
        scale = self._current_scale()
        base_width, base_height = self._base_page_size_px()
        self._page_w = max(1, round(base_width * scale))
        self._page_h = max(1, round(base_height * scale))
        margin_top, margin_right, margin_bottom, margin_left = (
            self._page_config.effective_margins_mm()
        )
        self._margin_top_px = round(_mm_to_px(margin_top) * scale)
        self._margin_right_px = round(_mm_to_px(margin_right) * scale)
        self._margin_bottom_px = round(_mm_to_px(margin_bottom) * scale)
        self._margin_left_px = round(_mm_to_px(margin_left) * scale)
        self._content_w = max(
            1, self._page_w - self._margin_left_px - self._margin_right_px
        )
        self._content_h = max(
            1, self._page_h - self._margin_top_px - self._margin_bottom_px
        )
        self._scale = scale

        # Header/footer bands: resolved once per document
        # against the first recording, then drawn on every applicable page.
        doc = self._document
        header_cfg = doc.layout.header if doc is not None else None
        footer_cfg = doc.layout.footer if doc is not None else None
        first_rec = _first_recording(doc)
        self._header_row = _resolve_chrome_row(
            header_cfg, first_rec, "header", self._pending_logos,
        )
        self._footer_row = _resolve_chrome_row(
            footer_cfg, first_rec, "footer", self._pending_logos,
        )
        self._header_scope = (
            header_cfg.scope if header_cfg is not None else "all"
        )
        self._screenshot_config = (
            doc.layout.screenshot_config if doc is not None else ScreenshotConfig()
        )
        self._active_recording = None

        # Background colours: the document's page config wins when one
        # is loaded (it carries the layout being previewed); a bare
        # widget falls back to its own page config.
        bg_config = (
            doc.layout.page_config if doc is not None else self._page_config
        )
        self._page_background = _valid_hex(bg_config.page_background_color)
        self._header_background = _valid_hex(bg_config.header_background_color)
        self._footer_background = _valid_hex(bg_config.footer_background_color)
        self._body_background = _valid_hex(bg_config.body_background_color)

        self._page = self._new_page()
        self._pages.append(self._page)

        if self._document is None or not self._document.show_sections:
            self._add_empty_state(self._page)
            return

        doc = self._document
        sep = doc.separator

        for section_idx, section in enumerate(doc.show_sections):
            # Separator between sections
            if section_idx > 0 and sep:
                self._flow_text(sep * 80, _make_qfont(None, scale=scale), color="#999999")

            # Show header lines; the last one reads as the section title.
            for line_idx, line in enumerate(section.header_lines):
                font = _make_qfont(None, fallback_size=13, scale=scale)
                if line_idx == len(section.header_lines) - 1:
                    font.setBold(True)
                self._flow_line(line, font)

            # Recordings
            for rec_idx, resolved_rec in enumerate(section.recordings):
                if rec_idx > 0:
                    self._flow_spacer(_RECORDING_GAP_PX)
                self._flow_recording(resolved_rec, scale)

        self._finish_page()

    def _flow_line(self, line: ResolvedLine, font: QFont | None) -> None:
        """Add a resolved line to the current page, breaking if needed.

        A line carrying [Screenshot:N] elements is split into
        text runs and screenshot blocks, each rendered in turn.
        """
        segments = _split_screenshot_segments(line)
        if len(segments) == 1 and segments[0][0] == "text":
            self._flow_text_line(segments[0][1], font)
            return
        for kind, payload in segments:
            if kind == "text":
                self._flow_text_line(payload, font)  # type: ignore[arg-type]
            else:
                self._flow_screenshot_element(payload)  # type: ignore[arg-type]

    def _flow_text_line(self, line: ResolvedLine, font: QFont | None) -> None:
        """Render a text-only resolved line as a single label."""
        text = "".join(elem.text for elem in line.elements)
        if font is None:
            font = _line_font(line, scale=self._scale)
        height = _estimate_height_px(text, font, self._content_w)
        self._ensure_room(height)
        _render_line(self._page, line, font=font)  # type: ignore[arg-type]
        self._cursor += height + _ROW_GAP_PX

    def _flow_screenshot_element(self, elem: ResolvedElement) -> None:
        """Render one inline [Screenshot:N] block.

        Shows up to N of the active recording's screenshot paths sized
        from :class:`ScreenshotConfig`, padding with grey "no
        screenshot" placeholders when the recording has fewer (or none).
        Each row advances the pagination cursor like any other item.
        """
        n = int(elem.content)
        config = self._screenshot_config
        paths = (
            list(self._active_recording.screenshot_paths)
            if self._active_recording is not None
            else []
        )
        cells: list[QPixmap | None] = []
        for path in paths[:n]:
            pix = QPixmap(path)
            cells.append(pix if not pix.isNull() else None)
        cells.extend([None] * (n - len(cells)))
        if not cells:
            return

        max_w = max(1, round(config.max_width_px * self._scale))
        max_h = max(1, round(config.max_height_px * self._scale))
        # Grid column count; configs predating photos_per_row
        # fall back to the documented default of 3.
        grid_cols = getattr(config, "photos_per_row", _SCREENSHOT_GRID_COLUMNS)
        cols = grid_cols if config.layout == "grid" else len(cells)
        cols = max(1, min(cols, len(cells)))
        # The configured gap (L15.9.3) is what the row layout uses, so
        # the cells are sized around it too.
        gap_px = config.spacing_px
        cell_w = max(
            1,
            min(
                max_w,
                (self._content_w - gap_px * (cols - 1)) // cols,
            ),
        )

        for row_start in range(0, len(cells), cols):
            chunk = cells[row_start:row_start + cols]
            scaled: list[QPixmap | None] = []
            heights: list[int] = []
            for pix in chunk:
                if pix is None:
                    scaled.append(None)
                    heights.append(max_h)
                    continue
                w, h = _fit_pixmap_dims(pix.width(), pix.height(), cell_w, max_h)
                scaled.append(
                    pix.scaled(
                        w, h,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                heights.append(h)
            row_h = max(1, max(heights))
            self._ensure_room(row_h + _ROW_GAP_PX)
            self._add_screenshot_row(scaled, cell_w, row_h)
            self._cursor += row_h + _ROW_GAP_PX

    def _add_screenshot_row(
        self,
        scaled: list[QPixmap | None],
        cell_w: int,
        row_h: int,
    ) -> None:
        """Add one row of pixmap/placeholder labels to the current page.

        L15.9.3: ``spacing_px`` becomes the row layout's gap, a
        ``border_width_px > 0`` wraps every cell in a QFrame whose
        stylesheet draws the border (rounded when ``corner_radius_px >
        0``), and ``align`` positions the row inside the page column by
        moving the stretch to the opposite edge.  The radius is only
        ever applied together with a border: QSS cannot clip a QLabel's
        pixmap, so a borderless rounded frame would paint nothing.
        """
        config = self._screenshot_config
        # A cell is wrapped when it needs chrome: a stroked border, a
        # configured background fill, or both.
        wrap_cells = config.border_width_px > 0 or _valid_hex(
            getattr(config, "background_color", None)
        ) is not None
        row = QWidget()
        row.setObjectName("screenshotRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(config.spacing_px)
        if config.align != "left":
            row_layout.addStretch()
        for pix in scaled:
            label = QLabel()
            if pix is not None and not pix.isNull():
                label.setPixmap(pix)
                label.setFixedSize(pix.width(), pix.height())
            else:
                label.setObjectName("screenshotPlaceholder")
                label.setText("no screenshot")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.setWordWrap(True)
                label.setStyleSheet(
                    f"color: {_SCREENSHOT_PLACEHOLDER_COLOR};"
                    f" border: 1px dashed {_SCREENSHOT_PLACEHOLDER_COLOR};"
                )
                label.setFixedSize(cell_w, row_h)
            if wrap_cells:
                row_layout.addWidget(self._wrap_screenshot_cell(label, config))
            else:
                row_layout.addWidget(label)
        if config.align != "right":
            row_layout.addStretch()
        self._page.layout().addWidget(row)  # type: ignore[union-attr]

    @staticmethod
    def _wrap_screenshot_cell(label: QLabel, config: ScreenshotConfig) -> QFrame:
        """Wrap *label* in a QFrame bordering/filling it per the config.

        The configured ``background_color`` (the fill behind the image)
        becomes the frame's background; without one the frame stays
        transparent, so a border-only cell looks unchanged.
        """
        frame = QFrame()
        frame.setObjectName("screenshotFrame")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)
        inner.addWidget(label)
        bg = (
            _valid_hex(getattr(config, "background_color", None))
            or "transparent"
        )
        style = (
            f"border: {config.border_width_px}px solid {config.border_color};"
            f" background-color: {bg};"
        )
        if config.corner_radius_px > 0:
            style += f" border-radius: {config.corner_radius_px}px;"
        frame.setStyleSheet(style)
        return frame

    def _flow_text(self, text: str, font: QFont, *, color: str | None = None) -> None:
        """Add a plain label to the current page, breaking if needed."""
        height = _estimate_height_px(text, font, self._content_w)
        self._ensure_room(height)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(font)
        if color:
            label.setStyleSheet(f"color: {color};")
        self._page.layout().addWidget(label)  # type: ignore[union-attr]
        self._cursor += height + _ROW_GAP_PX

    def _flow_spacer(self, height_px: int) -> None:
        """Add a fixed-height spacer to the current page, breaking if needed."""
        height = max(1, round(height_px * self._scale))
        self._ensure_room(height)
        spacer = QLabel("")
        spacer.setFixedHeight(height)
        self._page.layout().addWidget(spacer)  # type: ignore[union-attr]
        self._cursor += height + _ROW_GAP_PX

    def _flow_recording(self, resolved_rec: ResolvedRecording, scale: float) -> None:
        """Render a single recording's body lines.

        Screenshots render inline wherever the layout
        places a [Screenshot:N] token; there is no trailing count hint.
        """
        self._active_recording = resolved_rec.recording
        for line in resolved_rec.lines:
            self._flow_line(line, None)
        self._active_recording = None

    def _ensure_room(self, item_height: int) -> None:
        """Start a new page frame when *item_height* would overflow the page."""
        if self._cursor == 0 or self._cursor + item_height + _ROW_GAP_PX <= self._content_h:
            return
        # "— Page N —" marker in the middle of a rule, then a fresh page.
        self._pages_box.addWidget(self._make_page_break_marker(len(self._pages) + 1))
        self._finish_page()
        self._page = self._new_page()
        self._pages.append(self._page)
        self._cursor = 0

    def _finish_page(self) -> None:
        """Pad out the completed page's leftover height.

        Content stacks from the top of the page's content area; with
        nothing to absorb the surplus, the layout hands the entire
        remaining height to the content widgets themselves, so the
        line label grows to the full content height — and any
        background painted on it (the propagated desk colour behind a
        highlighted run) stretches with it.  The spacer is
        horizontally Fixed so it takes none of the labels' width and
        keeps the text wrapping unchanged; vertically it is Expanding,
        so every flowed item stays at the height Qt computed for its
        own text.
        """
        layout = self._page.layout() if self._page is not None else None
        if layout is not None:
            layout.addSpacerItem(
                QSpacerItem(0, 0, QSizePolicy.Fixed, QSizePolicy.Expanding)
            )

    def _new_page(self) -> QFrame:
        """Create one page frame at the current zoom size.

        The frame background is the configured page colour (white when
        none); a configured body colour additionally paints a
        content-column band behind the flowed content.
        """
        page = QFrame()
        page.setObjectName("previewPage")
        page_bg = self._page_background or "#FFFFFF"
        page.setStyleSheet(
            "#previewPage {"
            f"  background-color: {page_bg};"
            f"  border: 1px solid {_PAGE_BORDER_COLOR};"
            "}"
        )
        page.setFixedSize(self._page_w, self._page_h)
        shadow = QGraphicsDropShadowEffect(page)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 35))
        page.setGraphicsEffect(shadow)
        page_layout = QVBoxLayout(page)
        # Qt order is left, top, right, bottom.
        page_layout.setContentsMargins(
            self._margin_left_px,
            self._margin_top_px,
            self._margin_right_px,
            self._margin_bottom_px,
        )
        self._pages_box.addWidget(page)  # attach: reparents onto the desk

        # Header/footer bands sit in the margin area; the
        # header honours its scope ("first" = page 1 only).
        page_no = len(self._pages) + 1
        if self._header_row is not None and (
            self._header_scope == "all" or page_no == 1
        ):
            self._add_chrome_band(
                page, "previewHeader", self._header_row, at_top=True
            )
        if self._footer_row is not None:
            self._add_chrome_band(
                page, "previewFooter", self._footer_row, at_top=False
            )

        # Body area: an absolutely-positioned band spanning the content
        # column.  Created here, BEFORE any content label is added to
        # the page's layout, so the later labels stack on top of it.
        if self._body_background is not None:
            body_band = QWidget(page)
            body_band.setObjectName("previewBodyBand")
            body_band.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents
            )
            body_band.setGeometry(
                self._margin_left_px,
                self._margin_top_px,
                self._content_w,
                self._content_h,
            )
            body_band.setStyleSheet(
                f"background-color: {self._body_background};"
            )
        return page

    def _add_chrome_band(
        self,
        page: QFrame,
        object_name: str,
        slots: _ChromeRow,
        *,
        at_top: bool,
    ) -> None:
        """Draw a header/footer slot band inside *page*'s margin area.

        Absolutely positioned — not part of the page's content layout —
        centred on the margin midline (top margin for a header, bottom
        margin for a footer).  Slot anchors: left at the left margin,
        centre on the page centre, right ending at the right margin.

        Text slots become full-cell labels; logo slots
        become fixed-size pixmap labels placed with the same x/y math
        as the PDF, converted to pixels.  A logo whose file is missing
        or unreadable falls back to a small grey italic "missing logo"
        placeholder — rendering never raises and never blocks.
        """
        # item = ("text", label_text, font) | ("missing", label_text, font)
        #      | ("logo", pixmap)
        items: list[tuple | None] = []
        heights: list[int] = []
        for slot in slots:
            if slot is None:
                items.append(None)
                continue
            kind = slot[0]
            if kind == "text":
                _, text, font_size, bold = slot
                font = QFont()
                font.setPointSizeF(font_size * self._scale)
                font.setBold(bold)
                items.append(("text", text, font))
                heights.append(QFontMetrics(font).height())
                continue
            # ("logo", path | None, width_mm): scale to the target px
            # width (mm→px × zoom), aspect preserved.
            _, path, width_mm = slot
            target_w = max(1, round(_mm_to_px(width_mm) * self._scale))
            pixmap = QPixmap()
            if path is not None:
                try:
                    loaded = QPixmap(str(path))
                    if not loaded.isNull():
                        pixmap = loaded.scaledToWidth(
                            target_w,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                except Exception:
                    pixmap = QPixmap()      # unreadable → placeholder
            if not pixmap.isNull():
                items.append(("logo", pixmap))
                heights.append(pixmap.height())
                continue
            font = QFont()
            font.setItalic(True)
            font.setPointSizeF(_CHROME_MISSING_FONT_PT * self._scale)
            items.append(("missing", "missing logo", font))
            heights.append(QFontMetrics(font).height())
        if not heights:
            return

        band_h = max(heights) + 2
        if at_top:
            y = max(0, round(self._margin_top_px / 2 - band_h / 2))
        else:
            y = min(
                self._page_h - band_h,
                round(self._page_h - self._margin_bottom_px / 2 - band_h / 2),
            )

        band = QWidget(page)
        band.setObjectName(object_name)
        band.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        band.setGeometry(0, y, self._page_w, band_h)
        band_bg = (
            self._header_background if at_top else self._footer_background
        )
        if band_bg:
            band.setStyleSheet(f"background-color: {band_bg};")

        anchors = (
            (
                self._margin_left_px,
                max(1, self._page_w - self._margin_left_px),
                Qt.AlignmentFlag.AlignLeft,
            ),
            (0, self._page_w, Qt.AlignmentFlag.AlignHCenter),
            (
                0,
                max(1, self._page_w - self._margin_right_px),
                Qt.AlignmentFlag.AlignRight,
            ),
        )
        for index, (item, (x, w, alignment)) in enumerate(zip(items, anchors)):
            if item is None:
                continue
            kind = item[0]
            if kind == "logo":
                pixmap: QPixmap = item[1]
                pw, ph = pixmap.width(), pixmap.height()
                if index == 0:              # left: at the left margin
                    logo_x = self._margin_left_px
                elif index == 1:            # centre: on the page centre
                    logo_x = round((self._page_w - pw) / 2)
                else:                       # right: ends at the right margin
                    logo_x = self._page_w - self._margin_right_px - pw
                label = QLabel(band)
                label.setPixmap(pixmap)
                label.setFixedSize(pw, ph)
                # The band is centred on the margin midline, so centring
                # the label inside it IS the PDF's y = y_center − h/2.
                label.move(logo_x, (band_h - ph) // 2)
                continue
            text, font = item[1], item[2]
            label = QLabel(text, band)
            label.setFont(font)
            label.setAlignment(alignment | Qt.AlignmentFlag.AlignVCenter)
            color = (
                _CHROME_MISSING_COLOR
                if kind == "missing"
                else _CHROME_TEXT_COLOR
            )
            label.setStyleSheet(f"color: {color};")
            label.setGeometry(x, 0, w, band_h)

    @staticmethod
    def _make_page_break_marker(page_number: int) -> QWidget:
        """A horizontal rule interrupted by a centered '— Page N —' label."""
        row = QWidget()
        row.setObjectName("pageBreakMarker")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(48, 0, 48, 0)
        left = QFrame()
        left.setFixedHeight(1)
        left.setStyleSheet("background-color: #D1D5DB;")
        right = QFrame()
        right.setFixedHeight(1)
        right.setStyleSheet("background-color: #D1D5DB;")
        label = QLabel(f"\u2014 Page {page_number} \u2014")
        label.setStyleSheet("color: #9CA3AF; font-size: 9px;")
        layout.addWidget(left, 1)
        layout.addWidget(label)
        layout.addWidget(right, 1)
        return row

    def _add_empty_state(self, parent: QWidget) -> None:
        """Show a centered empty-state message inside *parent*'s page."""
        spacer_top = QLabel("")
        spacer_top.setFixedHeight(max(1, round(40 * self._scale)))
        parent.layout().addWidget(spacer_top)  # type: ignore[union-attr]

        msg = QLabel("No recordings yet. Click + Add Recording to start.")
        msg.setObjectName("emptyState")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        parent.layout().addWidget(msg)  # type: ignore[union-attr]

        spacer_bottom = QLabel("")
        spacer_bottom.setFixedHeight(max(1, round(40 * self._scale)))
        parent.layout().addWidget(spacer_bottom)  # type: ignore[union-attr]

    # -- Resize -------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        """Toggle the narrow-window hint and re-apply Fit Width zoom."""
        super().resizeEvent(event)
        if not hasattr(self, "_hint_label"):
            return  # fired mid-__init__ before the chrome was built
        self._hint_label.setVisible(self.width() < _NARROW_WIDTH_PX)
        # Fit Width scales with the available width; 100%/Custom do not.
        if self._zoom_combo.currentText() == ZOOM_FIT_WIDTH:
            self._rebuild()
