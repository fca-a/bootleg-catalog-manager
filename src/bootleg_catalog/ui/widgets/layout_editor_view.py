"""Layout editor view — the Layout Designer's page-like text editors.

The two syntax-highlighted editors of the Layout Designer, dressed so
the editing surface reads as a *document* instead of two colored text
areas:

- :class:`LayoutEditorView` is a QPlainTextEdit dressed as a sheet of
  paper: the system sans-serif font at 11pt, dark text on a white
  page (viewport), generous A4-like viewport margins, no border and no
  focus rect.  The dialog wraps the editors in a shared ``layoutPage``
  QFrame (white, hairline border, soft drop shadow) that floats on a
  grey desk.
- :class:`LayoutSyntaxHighlighter` renders ``{Technical}`` and
  ``[Archival]`` tokens as near-chips: monospace family, DemiBold,
  105% letter spacing and a soft background fill.  QTextCharFormat
  cannot round corners, so the "chip" feel comes from the background
  plus the spacing — never from a larger font size (tokens must stay
  exactly as tall as the surrounding text).
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPalette,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextFormat,
)
from PySide6.QtWidgets import QFrame, QPlainTextEdit, QWidget

from bootleg_catalog.core.field_registry import is_archival, is_technical

_DESK_COLOR = "#F3F4F6"  # the grey "desk" around the page
_PAGE_COLOR = "#FFFFFF"  # the page itself (viewport)
_TEXT_COLOR = "#111827"  # body text
_BODY_POINT_SIZE = 11

# A full {Name} or [Name] token; names are non-empty and contain no
# brace/bracket characters — the same shapes the layout parser accepts
# (empty {} / [] stay literal text and never match here).  Public
# so the dialog's status line scans with the same token definition.
FIELD_TOKEN_RE = QRegularExpression(r"\{[^{}]+\}|\[[^\[\]]+\]")


def classify_field_token(token: str) -> str:
    """Classify a full ``{Name}``/``[Name]`` token against the registry.

    Returns ``"valid"``, ``"wrong_category"`` (the name is registered,
    but only in the other category — the fix is the other delimiter
    pair) or ``"unknown"`` (not a registered field at all).  Names are
    looked up verbatim, exactly as the FieldResolver does — whitespace
    inside the delimiters is significant.
    """
    if token[:1] == "{":
        name = token[1:-1]
        if is_technical(name):
            return "valid"
        return "wrong_category" if is_archival(name) else "unknown"
    # Bracket token.
    name = token[1:-1]
    if is_archival(name):
        return "valid"
    return "wrong_category" if is_technical(name) else "unknown"


def is_screenshot_token(token: str) -> bool:
    """True when *token* is a ``[Screenshot:…]`` / ``{Screenshot:…}`` token.

    Screenshot tokens are never field references whatever
    their exact form — out-of-range or whitespace variants are literal
    text — so the wrong-category/unknown rules and the validation
    status line both skip them.
    """
    return token.startswith(("[Screenshot:", "{Screenshot:"))


class LayoutSyntaxHighlighter(QSyntaxHighlighter):
    """Near-chip highlighting for ``{technical}``, ``[archival]`` and
    ``[Screenshot:N]`` tokens.

    Tokens keep the body text's size (no ``fontPointSize`` is ever
    set): the chip must be the same height as the surrounding text.
    Each field token is colored by registry validity: the blue/green
    chips stay for valid references, wrong-category tokens go amber and
    unknown-field tokens red (both italic) — the chip shape (family,
    weight, letter spacing) is identical in every state.  A violet chip
    marks ``[Screenshot:N]``, which is never classified by
    the registry rules.  Grey control markers cover ``==``
    and ``[mark=…]``/``[/mark]``/``[color=…]``/``[/color]`` — the text
    between them is NOT tinted with the marker colour (one
    QTextCharFormat per range cannot tint a run without also overriding
    the chip backgrounds).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        mono_family = QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont,
        ).family()

        def _token_format(
            foreground: str, background: str, *, italic: bool = False,
        ) -> QTextCharFormat:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(foreground))
            fmt.setBackground(QColor(background))
            fmt.setFontFamilies([mono_family])
            fmt.setFontWeight(QFont.Weight.DemiBold)
            if italic:
                fmt.setFontItalic(True)
            fmt.setProperty(
                QTextFormat.Property.FontLetterSpacingType,
                QFont.SpacingType.PercentageSpacing.value,
            )
            fmt.setProperty(QTextFormat.Property.FontLetterSpacing, 105.0)
            return fmt

        tech_fmt = _token_format("#1E40AF", "#DBEAFE")  # dark blue chip
        arch_fmt = _token_format("#065F46", "#D1FAE5")  # dark green chip
        shot_fmt = _token_format("#7C3AED", "#EDE9FE")  # violet chip
        self._wrong_format = _token_format(
            "#92400E", "#FEF3C7", italic=True,
        )  # amber: right name, wrong delimiter pair
        self._unknown_format = _token_format(
            "#991B1B", "#FEE2E2", italic=True,
        )  # red: not a registered field

        # (regex, chip format, mode).  The screenshot rule comes FIRST
        # so "[Screenshot:3]" is never also fed to the archival rule,
        # which would flag it as an unknown field.  The count is only a
        # screenshot token when it is in range 1-20; any
        # other "Screenshot:…" form is literal text, so neither chip
        # rule may claim it.
        self._rules: list[tuple[QRegularExpression, QTextCharFormat, str]] = [
            (
                QRegularExpression(r"\[Screenshot:(?:[1-9]|1\d|20)\]"),
                shot_fmt,
                "screenshot",
            ),
            (QRegularExpression(r"\{[^{}]+\}"), tech_fmt, "field"),
            (QRegularExpression(r"\[[^\[\]]+\]"), arch_fmt, "field"),
        ]

        # Bold/italic markers are *control* characters, not
        # content: grey, no chip background, body font untouched.  The
        # alternation matches "**" before "*", and matches inside a
        # field token are skipped so a chip is never broken up.  "=="
        # and the bracketed [mark=…]/[/mark]/[color=…]
        # [/color] forms get the same treatment.
        self._marker_format = QTextCharFormat()
        self._marker_format.setForeground(QColor("#6B7280"))
        self._marker_re = QRegularExpression(r"\*\*|\*|==")
        # The bracketed style markers are claimed BEFORE the field rules
        # below: the archival regex would read "[mark=#FF0000]" as an
        # unknown field named "mark=#FF0000" and chip it red.
        self._style_marker_re = QRegularExpression(
            r"\[mark=#[0-9A-Fa-f]{6}\]|\[/mark\]"
            r"|\[color=#[0-9A-Fa-f]{6}\]|\[/color\]"
        )

    def highlightBlock(self, text: str) -> None:
        # Bracketed style markers first, so the field rules
        # below never chip them as unknown fields.
        control_ranges: list[tuple[int, int]] = []
        screenshot_ranges: list[tuple[int, int]] = []
        field_ranges: list[tuple[int, int]] = []
        it = self._style_marker_re.globalMatch(text)
        while it.hasNext():
            match = it.next()
            start, length = match.capturedStart(), match.capturedLength()
            control_ranges.append((start, length))
            self.setFormat(start, length, self._marker_format)

        for regex, chip_fmt, mode in self._rules:
            it = regex.globalMatch(text)
            while it.hasNext():
                match = it.next()
                start, length = match.capturedStart(), match.capturedLength()
                if mode == "screenshot":
                    screenshot_ranges.append((start, length))
                    self.setFormat(start, length, chip_fmt)
                    continue
                if any(
                    start < ss + sl and ss < start + length
                    for ss, sl in screenshot_ranges
                ):
                    continue  # inside a [Screenshot:N] chip
                if any(
                    start < cs + cl and cs < start + length
                    for cs, cl in control_ranges
                ):
                    continue  # inside a [mark=…]/[color=…] marker
                field_ranges.append((start, length))
                if is_screenshot_token(match.captured()):
                    # Malformed screenshot variant: literal text, so no
                    # chip and no registry verdict.  Kept in
                    # field_ranges only so markers inside it stay plain.
                    continue
                kind = classify_field_token(match.captured())
                if kind == "valid":
                    fmt = chip_fmt
                elif kind == "wrong_category":
                    fmt = self._wrong_format
                else:
                    fmt = self._unknown_format
                self.setFormat(start, length, fmt)

        it = self._marker_re.globalMatch(text)
        while it.hasNext():
            match = it.next()
            start, length = match.capturedStart(), match.capturedLength()
            if any(
                start < fs + fl and fs < start + length
                for fs, fl in field_ranges + screenshot_ranges + control_ranges
            ):
                continue  # inside a {…}/[…] chip — leave the chip intact
            self.setFormat(start, length, self._marker_format)


class LayoutEditorView(QPlainTextEdit):
    """A QPlainTextEdit dressed as a page for the Layout Designer.

    Carries its own :class:`LayoutSyntaxHighlighter`, exposed as
    ``highlighter``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # A4-like text margins inside the page; no border, no focus rect.
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setViewportMargins(56, 40, 56, 40)

        # Palette: white page (viewport), dark text, grey desk ring
        # showing in the margin area between viewport and frame edge.
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(_PAGE_COLOR))
        palette.setColor(QPalette.ColorRole.Text, QColor(_TEXT_COLOR))
        palette.setColor(QPalette.ColorRole.Window, QColor(_DESK_COLOR))
        self.setPalette(palette)
        self.viewport().setPalette(palette)

        # Stylesheet: desk-grey widget fill and no focus outline.
        self.setStyleSheet(
            "QPlainTextEdit {"
            f"  background-color: {_DESK_COLOR};"
            f"  color: {_TEXT_COLOR};"
            "  border: none;"
            "}"
            "QPlainTextEdit:focus { outline: none; }"
        )

        # Sans-serif system UI font; the tokens share this size.
        font = self.font()
        font.setPointSize(_BODY_POINT_SIZE)
        self.setFont(font)

        self.highlighter = LayoutSyntaxHighlighter(self.document())
