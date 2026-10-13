"""Tests for the layout text parser and formatter (L11.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bootleg_catalog.core.layout_text import format_layout_lines, parse_layout_lines
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FieldType,
    LayoutElement,
    LayoutLine,
)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParseLayoutLines:
    def test_empty_string(self) -> None:
        """Empty string → empty list."""
        assert parse_layout_lines("") == []

    def test_single_literal_text(self) -> None:
        """Single line of literal text → one line, one text element."""
        result = parse_layout_lines("Hello world")
        assert len(result) == 1
        assert len(result[0].elements) == 1
        elem = result[0].elements[0]
        assert elem.kind == "text"
        assert elem.content == "Hello world"

    def test_single_technical_field(self) -> None:
        """Single {Field} → one line, one TECHNICAL field element."""
        result = parse_layout_lines("{Duration}")
        assert len(result) == 1
        elem = result[0].elements[0]
        assert elem.kind == "field"
        assert elem.field_type == FieldType.TECHNICAL
        assert elem.content == "Duration"

    def test_single_archival_field(self) -> None:
        """Single [Field] → one line, one ARCHIVAL field element."""
        result = parse_layout_lines("[Show]")
        assert len(result) == 1
        elem = result[0].elements[0]
        assert elem.kind == "field"
        assert elem.field_type == FieldType.ARCHIVAL
        assert elem.content == "Show"

    def test_mixed_text_and_field(self) -> None:
        """Mixed text + field → correct element order and types."""
        result = parse_layout_lines("Format: {Extension} — [Source]")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 4
        assert elems[0].kind == "text" and elems[0].content == "Format: "
        assert elems[1].kind == "field" and elems[1].field_type == FieldType.TECHNICAL
        assert elems[1].content == "Extension"
        assert elems[2].kind == "text" and elems[2].content == " \u2014 "
        assert elems[3].kind == "field" and elems[3].field_type == FieldType.ARCHIVAL
        assert elems[3].content == "Source"

    def test_multiple_lines(self) -> None:
        """Multiple lines → correct number of LayoutLine objects."""
        result = parse_layout_lines("line1\nline2\nline3")
        assert len(result) == 3

    def test_empty_line(self) -> None:
        """Empty line → LayoutLine(elements=[])."""
        result = parse_layout_lines("before\n\nafter")
        assert len(result) == 3
        assert result[1].elements == []

    def test_crlf_normalized(self) -> None:
        """\\r\\n → treated as \\n."""
        result = parse_layout_lines("line1\r\nline2")
        assert len(result) == 2
        assert result[0].elements[0].content == "line1"
        assert result[1].elements[0].content == "line2"

    def test_unicode_preserved(self) -> None:
        """Unicode text preserved exactly."""
        result = parse_layout_lines("\u6771\u4eac 1997 \u2014 \u00e9\u00e8")
        assert len(result) == 1
        assert result[0].elements[0].content == "\u6771\u4eac 1997 \u2014 \u00e9\u00e8"

    def test_unclosed_brace_literal(self) -> None:
        """Unclosed { → treated as literal text."""
        result = parse_layout_lines("{Duration")
        assert len(result) == 1
        elem = result[0].elements[0]
        assert elem.kind == "text"
        assert elem.content == "{Duration"

    def test_unclosed_bracket_literal(self) -> None:
        """Unclosed [ → treated as literal text."""
        result = parse_layout_lines("[Show")
        assert len(result) == 1
        elem = result[0].elements[0]
        assert elem.kind == "text"
        assert elem.content == "[Show"

    def test_empty_braces_literal(self) -> None:
        """Empty {} → treated as literal text."""
        result = parse_layout_lines("{}")
        assert len(result) == 1
        elem = result[0].elements[0]
        assert elem.kind == "text"
        assert elem.content == "{}"

    def test_empty_brackets_literal(self) -> None:
        """Empty [] → treated as literal text."""
        result = parse_layout_lines("[]")
        assert len(result) == 1
        elem = result[0].elements[0]
        assert elem.kind == "text"
        assert elem.content == "[]"

    def test_nested_braces_literal(self) -> None:
        """Nested {a{b}c} → treated as literal text."""
        result = parse_layout_lines("{a{b}c}")
        assert len(result) == 1
        # Should produce literal text, not nested fields
        text = "".join(e.content for e in result[0].elements)
        assert text == "{a{b}c}"

    def test_adjacent_fields(self) -> None:
        """Adjacent fields {Duration}[Show] → two field elements."""
        result = parse_layout_lines("{Duration}[Show]")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 2
        assert elems[0].kind == "field" and elems[0].field_type == FieldType.TECHNICAL
        assert elems[1].kind == "field" and elems[1].field_type == FieldType.ARCHIVAL

    def test_multiple_fields_with_text_between(self) -> None:
        """Multiple fields with text between → correct splitting."""
        result = parse_layout_lines("{Duration} and [Show]")
        elems = result[0].elements
        assert len(elems) == 3
        assert elems[0].kind == "field"
        assert elems[1].kind == "text" and elems[1].content == " and "
        assert elems[2].kind == "field"

    def test_whitespace_only_line_preserved(self) -> None:
        """Whitespace-only line → text element with that whitespace."""
        result = parse_layout_lines("   ")
        assert len(result) == 1
        elem = result[0].elements[0]
        assert elem.kind == "text"
        assert elem.content == "   "

    def test_style_is_none(self) -> None:
        """Parsed elements always have style=None."""
        result = parse_layout_lines("{Duration}")
        assert result[0].elements[0].style is None

    def test_multiple_lines_mixed(self) -> None:
        """Multi-line with mix of fields, text, and empty lines."""
        text = "{Duration}\n\n[Show] hello"
        result = parse_layout_lines(text)
        assert len(result) == 3
        assert result[0].elements[0].field_type == FieldType.TECHNICAL
        assert result[1].elements == []
        assert result[2].elements[0].field_type == FieldType.ARCHIVAL
        assert result[2].elements[1].kind == "text"


# ---------------------------------------------------------------------------
# Formatter tests
# ---------------------------------------------------------------------------

class TestFormatLayoutLines:
    def test_empty_list(self) -> None:
        """Empty list → empty string."""
        assert format_layout_lines([]) == ""

    def test_one_text_element(self) -> None:
        """One line with one text element → exact content."""
        lines = [LayoutLine(elements=[LayoutElement(kind="text", content="Hello")])]
        assert format_layout_lines(lines) == "Hello"

    def test_technical_field(self) -> None:
        """One line with a TECHNICAL field → "{Name}"."""
        lines = [LayoutLine(elements=[
            LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL),
        ])]
        assert format_layout_lines(lines) == "{Duration}"

    def test_archival_field(self) -> None:
        """One line with an ARCHIVAL field → "[Name]"."""
        lines = [LayoutLine(elements=[
            LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL),
        ])]
        assert format_layout_lines(lines) == "[Show]"

    def test_multiple_lines(self) -> None:
        """Multiple lines → joined with \\n."""
        lines = [
            LayoutLine(elements=[LayoutElement(kind="text", content="a")]),
            LayoutLine(elements=[LayoutElement(kind="text", content="b")]),
        ]
        assert format_layout_lines(lines) == "a\nb"

    def test_unicode_preserved(self) -> None:
        """Unicode preserved in formatting."""
        lines = [LayoutLine(elements=[
            LayoutElement(kind="text", content="\u6771\u4eac 1997"),
        ])]
        assert format_layout_lines(lines) == "\u6771\u4eac 1997"

    def test_mixed_elements(self) -> None:
        """Mixed text and field elements concatenated."""
        lines = [LayoutLine(elements=[
            LayoutElement(kind="text", content="Format: "),
            LayoutElement(kind="field", content="Ext", field_type=FieldType.TECHNICAL),
            LayoutElement(kind="text", content=" \u2014 "),
            LayoutElement(kind="field", content="Src", field_type=FieldType.ARCHIVAL),
        ])]
        assert format_layout_lines(lines) == "Format: {Ext} \u2014 [Src]"

    def test_styles_serialised_as_markers(self) -> None:
        """Styled elements are formatted with ** markers (L15.8.4).

        The formatter used to silently drop styles (bold text round-
        tripped to plain); since L15.8.4 bold/italic are representable
        and are re-emitted as ``**``/``*``.  Non-bold/italic style
        attributes (font_size, colour) remain unrepresentable and are
        still ignored.
        """
        lines = [LayoutLine(elements=[
            LayoutElement(
                kind="text", content="Bold",
                style=ElementStyle(bold=True, font_size=14),
            ),
        ])]
        assert format_layout_lines(lines) == "**Bold**"

    def test_empty_line(self) -> None:
        """Empty LayoutLine → empty string for that line."""
        lines = [LayoutLine(elements=[])]
        assert format_layout_lines(lines) == ""

    def test_multiple_empty_lines(self) -> None:
        """Multiple empty lines → consecutive \\n."""
        lines = [
            LayoutLine(elements=[]),
            LayoutLine(elements=[]),
        ]
        assert format_layout_lines(lines) == "\n"


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_format_parse_text_only(self) -> None:
        """parse(format(lines)) == lines for text-only layout."""
        lines = [
            LayoutLine(elements=[LayoutElement(kind="text", content="Hello")]),
            LayoutLine(elements=[]),
            LayoutLine(elements=[LayoutElement(kind="text", content="World")]),
        ]
        formatted = format_layout_lines(lines)
        parsed = parse_layout_lines(formatted)
        assert parsed == lines

    def test_format_parse_fields(self) -> None:
        """parse(format(lines)) == lines for field layout."""
        lines = [
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL),
            ]),
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL),
            ]),
        ]
        formatted = format_layout_lines(lines)
        parsed = parse_layout_lines(formatted)
        assert parsed == lines

    def test_format_parse_mixed(self) -> None:
        """parse(format(lines)) == lines for mixed layout."""
        lines = [
            LayoutLine(elements=[
                LayoutElement(kind="text", content="Format: "),
                LayoutElement(kind="field", content="Ext", field_type=FieldType.TECHNICAL),
            ]),
            LayoutLine(elements=[]),
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL),
                LayoutElement(kind="text", content=" at "),
                LayoutElement(kind="field", content="Venue", field_type=FieldType.ARCHIVAL),
            ]),
        ]
        formatted = format_layout_lines(lines)
        parsed = parse_layout_lines(formatted)
        assert parsed == lines

    def test_parse_format_text(self) -> None:
        """format(parse(text)) == text for well-formed text."""
        text = "{Duration}\n[Show] hello\n\n{Extension}"
        parsed = parse_layout_lines(text)
        formatted = format_layout_lines(parsed)
        assert formatted == text

    def test_parse_format_unicode(self) -> None:
        """Round-trip preserves Unicode."""
        text = "\u6771\u4eac {Duration} \u2014 [Show]"
        parsed = parse_layout_lines(text)
        formatted = format_layout_lines(parsed)
        assert formatted == text

    def test_parse_format_preserves_whitespace(self) -> None:
        """Round-trip preserves literal whitespace."""
        text = "  {Duration}   [Show]  "
        parsed = parse_layout_lines(text)
        formatted = format_layout_lines(parsed)
        assert formatted == text

    def test_crlf_normalizes_to_lf(self) -> None:
        """\\r\\n normalises to \\n in round-trip."""
        text = "a\r\nb\r\nc"
        parsed = parse_layout_lines(text)
        formatted = format_layout_lines(parsed)
        assert formatted == "a\nb\nc"

    def test_round_trip_element_ordering(self) -> None:
        """Round-trip preserves element ordering."""
        lines = [
            LayoutLine(elements=[
                LayoutElement(kind="text", content="A"),
                LayoutElement(kind="field", content="X", field_type=FieldType.TECHNICAL),
                LayoutElement(kind="text", content="B"),
                LayoutElement(kind="field", content="Y", field_type=FieldType.ARCHIVAL),
                LayoutElement(kind="text", content="C"),
            ]),
        ]
        formatted = format_layout_lines(lines)
        parsed = parse_layout_lines(formatted)
        assert len(parsed[0].elements) == 5
        assert [e.kind for e in parsed[0].elements] == [
            "text", "field", "text", "field", "text",
        ]


# ---------------------------------------------------------------------------
# Bold / italic marker tests (L15.8.4)
# ---------------------------------------------------------------------------

class TestBoldItalicMarkers:
    def test_bold_marker_parses_to_bold_text_element(self) -> None:
        """`**bold**` → one text element, content "bold", style.bold True."""
        result = parse_layout_lines("**bold**")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 1
        elem = elems[0]
        assert elem.kind == "text"
        assert elem.content == "bold"
        assert elem.style is not None
        assert elem.style.bold is True
        assert elem.style.italic is False

    def test_italic_marker_parses_to_italic_text_element(self) -> None:
        """`*it*` → one text element, content "it", style.italic True."""
        result = parse_layout_lines("*it*")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 1
        elem = elems[0]
        assert elem.kind == "text"
        assert elem.content == "it"
        assert elem.style is not None
        assert elem.style.italic is True
        assert elem.style.bold is False

    def test_bold_marker_spanning_field(self) -> None:
        """`**Live in [Venue]**` → bold text run then bold field element."""
        result = parse_layout_lines("**Live in [Venue]**")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 2
        assert elems[0].kind == "text"
        assert elems[0].content == "Live in "
        assert elems[0].style is not None
        assert elems[0].style.bold is True
        assert elems[1].kind == "field"
        assert elems[1].field_type == FieldType.ARCHIVAL
        assert elems[1].content == "Venue"
        assert elems[1].style is not None
        assert elems[1].style.bold is True

    def test_styled_text_round_trips(self) -> None:
        """format(parse(t)) == t for well-formed bold/italic text."""
        text = "**bold** and *it*"
        assert format_layout_lines(parse_layout_lines(text)) == text

    def test_unclosed_marker_is_literal_and_round_trips(self) -> None:
        """`**unclosed` stays literal (markers included, style None)."""
        result = parse_layout_lines("**unclosed")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 1
        elem = elems[0]
        assert elem.kind == "text"
        assert elem.content == "**unclosed"
        assert elem.style is None
        assert format_layout_lines(result) == "**unclosed"

        # Documented round-trip limitation (L15.8.4): literal asterisks
        # adjacent to markers are NOT round-trippable.  `*a**b*` keeps
        # its first `*` (which opens a real italic run over "a") but the
        # trailing literal `**b*` is re-emitted verbatim, so the first
        # format(parse(t)) pass is already lossy — and it does not
        # converge, because each re-parse re-reads the literal stars as
        # markers again.  Pinning the actual first-pass result so any
        # future change to this corner is a visible, deliberate one.
        once = format_layout_lines(parse_layout_lines("*a**b*"))
        assert once == "*a***b*"


# ---------------------------------------------------------------------------
# Screenshot token tests (L15.8.5)
# ---------------------------------------------------------------------------

class TestScreenshotToken:
    def test_screenshot_token_parses(self) -> None:
        """`[Screenshot:3]` → one screenshot element, content "3"."""
        result = parse_layout_lines("[Screenshot:3]")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 1
        elem = elems[0]
        assert elem.kind == "screenshot"
        assert elem.content == "3"
        assert elem.field_type is None
        assert elem.style is None
        assert format_layout_lines(result) == "[Screenshot:3]"

    def test_invalid_screenshot_tokens_are_literal(self) -> None:
        """Out-of-range, whitespace, and curly variants stay literal text."""
        for text in ("[Screenshot:99]", "[Screenshot: 3]", "{Screenshot:3}"):
            result = parse_layout_lines(text)
            assert len(result) == 1, text
            elems = result[0].elements
            assert len(elems) == 1, text
            assert elems[0].kind == "text", text
            assert elems[0].content == text, text


# ---------------------------------------------------------------------------
# Highlight / colour marker tests (L15.8.6a)
# ---------------------------------------------------------------------------

class TestHighlightAndColourMarkers:
    def test_double_equals_sets_default_highlight(self) -> None:
        """`==text==` → one text element highlighted #FFFF00."""
        result = parse_layout_lines("==text==")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 1
        elem = elems[0]
        assert elem.kind == "text"
        assert elem.content == "text"
        assert elem.style is not None
        assert elem.style.highlight_color == "#FFFF00"
        assert elem.style.color is None
        assert elem.style.bold is False
        assert elem.style.italic is False
        assert format_layout_lines(result) == "==text=="

    def test_mark_opens_custom_highlight(self) -> None:
        """`[mark=#FF0000]text[/mark]` → highlight #FF0000."""
        result = parse_layout_lines("[mark=#FF0000]text[/mark]")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 1
        assert elems[0].style is not None
        assert elems[0].style.highlight_color == "#FF0000"
        assert format_layout_lines(result) == "[mark=#FF0000]text[/mark]"

    def test_color_opens_foreground_colour(self) -> None:
        """`[color=#00FF00]text[/color]` → foreground #00FF00."""
        result = parse_layout_lines("[color=#00FF00]text[/color]")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 1
        assert elems[0].style is not None
        assert elems[0].style.color == "#00FF00"
        assert elems[0].style.highlight_color is None
        assert format_layout_lines(result) == "[color=#00FF00]text[/color]"

    def test_bold_italic_over_colour_round_trips(self) -> None:
        """The canonical combined form is byte-stable through parse+format.

        Marker order is bold outside the colour run, italic inside it —
        exactly what the formatter re-emits.
        """
        text = "**[color=#FF0000]*bold red*[/color]**"
        parsed = parse_layout_lines(text)
        elem = parsed[0].elements[0]
        assert elem.style is not None
        assert elem.style.bold is True
        assert elem.style.italic is True
        assert elem.style.color == "#FF0000"
        assert format_layout_lines(parsed) == text

    def test_unclosed_marker_is_literal_and_round_trips(self) -> None:
        """`==unclosed` is literal text with no style, and is stable."""
        result = parse_layout_lines("==unclosed")
        assert len(result) == 1
        elems = result[0].elements
        assert len(elems) == 1
        assert elems[0].kind == "text"
        assert elems[0].content == "==unclosed"
        assert elems[0].style is None
        # Formatting re-emits it verbatim, and re-parsing stays literal.
        assert format_layout_lines(result) == "==unclosed"


# ---------------------------------------------------------------------------
# Independence tests
# ---------------------------------------------------------------------------

class TestIndependence:
    def test_no_pyside6_import(self) -> None:
        """Module must not import PySide6."""
        import bootleg_catalog.core.layout_text as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "PySide6" not in content

    def test_no_sqlite_import(self) -> None:
        """Module must not import sqlite3."""
        import bootleg_catalog.core.layout_text as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "sqlite3" not in content

    def test_no_catalog_import(self) -> None:
        """Module must not import Catalog or CatalogRepository."""
        import bootleg_catalog.core.layout_text as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "from bootleg_catalog.core.catalog" not in content
        assert "from bootleg_catalog.database" not in content
        assert "import catalog" not in content
        assert "import CatalogRepository" not in content
