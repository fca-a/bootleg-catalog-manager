"""Plain-text parser and formatter for layout lines.

Converts between :class:`LayoutLine` objects and a plain-text
representation that the Layout Designer lets the user edit.

Syntax
------
- ``{FieldName}`` → :attr:`FieldType.TECHNICAL` field.
- ``[FieldName]`` → :attr:`FieldType.ARCHIVAL` field.
- ``[Screenshot:N]`` → an inline screenshot block, N an
  integer 1-20.  Out-of-range N, whitespace inside the token, or the
  curly ``{Screenshot:N}`` are literal text.  Markers are atomic:
  ``**[Screenshot:3]**`` leaves the ``**`` as literal text.
- ``**text**`` → bold.  Markers may span elements:
  ``**Live in [Venue]**`` bolds both the text and the field.
- ``*text*`` → italic.  ``**`` is matched before ``*``, so
  ``***text***`` is bold-italic; there is no nesting and no escaping.
- ``==text==`` → highlight with the default colour ``#FFFF00``.
- ``[mark=#RRGGBB]text[/mark]`` → highlight with that colour.
  Opening and closing tokens must match exactly: a
  ``[mark=#RRGGBB]`` is closed only by ``[/mark]``, never by ``==``.
- ``[color=#RRGGBB]text[/color]`` → foreground colour.
- Literal text outside braces/brackets/markers is copied verbatim.

Rules
-----
- Fields are **not** validated against the FieldRegistry here.
  Validation happens later in :class:`FieldResolver`.
- Styles are represented in plain text — ``bold``/``italic``,
  ``highlight_color``, and ``color``.  ``font_size`` is not
  representable and is ignored by the formatter.
- Unclosed ``{`` or ``[`` is treated as literal text.
- Empty ``{}`` or ``[]`` is treated as literal text (empty field
  name is not valid).
- Nested braces like ``{a{b}c}`` are treated as literal text (no
  nesting support).  The nested-literal rule wins over everything:
  a nested line is emitted whole, markers included, unstyled.
- Unbalanced ``**`` / ``*`` markers are literal text: the suffix from
  the first unclosed marker onward becomes one literal, unstyled text
  element (the markers stay in the content), matching the unclosed-
  brace rule.
- Empty marker pairs (``****``, ``====``,
  ``[mark=#FF0000][/mark]``) are literal text.
- Highlight and colour markers are atomic and NOT nested with each
  other: in
  ``[mark=#FFFF00][color=#FF0000]x[/color][/mark]`` the outer wins and
  the inner becomes literal text.  A marker that would open a style
  that is already on is literal for the same reason, and a closing
  token with no matching opener (``[/mark]`` alone) is literal.  The
  ``**``/``*`` flags are the exception: they apply on top of
  highlight/colour freely.
- ``[mark=…]`` / ``[color=…]`` require a ``#RRGGBB`` value; a missing
  or malformed hex makes the token literal text.
- Documented round-trip limitation: literal asterisks adjacent to
  markers (e.g. ``*a**b*``) are NOT round-trippable.  The leading
  ``*`` opens a real italic run, but the unparseable tail is re-emitted
  verbatim, so the first ``format(parse(t))`` pass is already lossy —
  and repeated passes do not converge, since each re-parse reads the
  literal stars as markers again.  Such input is accepted and rendered
  best-effort; it is not byte-stable.
- ``\\r\\n`` is normalised to ``\\n`` during parsing.
- Whitespace-only lines produce a ``LayoutLine`` with a single
  text element containing that whitespace (preserved literally).
"""

from __future__ import annotations

import re

from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FieldType,
    LayoutElement,
    LayoutLine,
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Matches {Name} or [Name] where Name is non-empty and contains no
# brace/bracket characters.  Used ANCHORED at a position while walking
# the line, so an unmatched "{" / "[" simply stays literal.
_TECH_FIELD_AT_RE = re.compile(r"\{[^{}]+\}")
_ARCH_FIELD_AT_RE = re.compile(r"\[[^\[\]]+\]")

# [Screenshot:N] — matched BEFORE the archival [Name] regex,
# which would otherwise swallow it as a field literally named
# "Screenshot:N".  The count must be digits only, with no whitespace
# inside the token.
_SCREENSHOT_AT_RE = re.compile(r"\[Screenshot:(\d+)\]")
# Valid N range, mirroring the syntax spec (1-20).
_SCREENSHOT_MIN = 1
_SCREENSHOT_MAX = 20

# Inline style markers.  The bracketed forms are matched
# BEFORE the archival [Name] regex for the same reason [Screenshot:N]
# is — it would otherwise read them as field names ("mark=#FF0000",
# "/mark", …).  A "#RRGGBB" value is mandatory: a malformed one makes
# the token literal text.
_MARK_OPEN_AT_RE = re.compile(r"\[mark=(#[0-9A-Fa-f]{6})\]")
_MARK_CLOSE_AT_RE = re.compile(r"\[/mark\]")
_COLOR_OPEN_AT_RE = re.compile(r"\[color=(#[0-9A-Fa-f]{6})\]")
_COLOR_CLOSE_AT_RE = re.compile(r"\[/color\]")
# The colour the bare "==" marker uses.
_DEFAULT_HIGHLIGHT = "#FFFF00"


def _has_nested(text: str) -> bool:
    """Return True if text contains nested braces or brackets.

    Examples of nested: ``{a{b}}``, ``[a[b]]``, ``{a[b]}``.
    """
    depth = 0
    for ch in text:
        if ch in ("{", "["):
            depth += 1
            if depth > 1:
                return True
        elif ch in ("}", "]"):
            depth = max(depth - 1, 0)
    return False


def parse_layout_lines(text: str) -> list[LayoutLine]:
    """Parse a multi-line string into :class:`LayoutLine` objects.

    Each line in the input becomes one ``LayoutLine``.  Empty lines
    (zero characters) produce ``LayoutLine(elements=[])``.  Whitespace-
    only lines produce a ``LayoutLine`` with a single text element
    containing that whitespace.

    Unclosed ``{`` or ``[`` is treated as literal text.  Empty
    ``{}`` or ``[]`` is treated as literal text.  Nested braces are
    treated as literal text.  ``**``/``*`` markers set the
    ``ElementStyle`` bold/italic flags per element; see the
    module docstring for the marker rules.
    """
    # Normalise line endings
    text = text.replace("\r\n", "\n")

    if not text:
        return []

    raw_lines = text.split("\n")

    result: list[LayoutLine] = []
    for raw in raw_lines:
        result.append(_parse_one_line(raw))
    return result


def _parse_one_line(raw: str) -> LayoutLine:
    """Parse a single line into a LayoutLine."""
    if not raw:
        return LayoutLine(elements=[])

    # If the line contains nested braces/brackets, treat the entire
    # line as literal text (no field parsing); no styling is applied
    # either.
    if _has_nested(raw):
        return LayoutLine(elements=[LayoutElement(kind="text", content=raw)])

    tokens = _tokenize_line(raw)
    _resolve_markers(tokens, raw)
    return LayoutLine(elements=_tokens_to_elements(tokens))


def _tokenize_line(raw: str) -> list[dict]:
    """Walk *raw* character by character into content/marker tokens.

    Each token is a dict: ``kind`` ("text" | "field" | "marker") plus
    ``text`` (the character, field name, or marker string); field
    tokens also carry ``field_type``, and marker tokens carry ``key``
    ("bold" | "italic" | "highlight" | "color"), an ``action``
    ("toggle" | "open" | "close"), the ``value`` they apply and their
    raw ``index``.  The active style is stamped onto the content tokens
    later by :func:`_resolve_markers`.
    """
    tokens: list[dict] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "{":
            if raw.startswith("{Screenshot:", i):
                # Curly {Screenshot:N} is not valid syntax: keep
                # the "{" literal so the tech regex cannot swallow it as
                # a field named "Screenshot:N".
                tokens.append({"kind": "text", "text": ch})
                i += 1
                continue
            m = _TECH_FIELD_AT_RE.match(raw, i)
            if m is not None:
                tokens.append({
                    "kind": "field",
                    "text": m.group()[1:-1],
                    "field_type": FieldType.TECHNICAL,
                })
                i = m.end()
                continue
        elif ch == "[":
            if raw.startswith("[Screenshot:", i):
                m = _SCREENSHOT_AT_RE.match(raw, i)
                if m is not None and _SCREENSHOT_MIN <= int(m.group(1)) <= _SCREENSHOT_MAX:
                    tokens.append({"kind": "screenshot", "text": m.group(1)})
                    i = m.end()
                    continue
                # Out-of-range count or whitespace inside the token:
                # literal "[" (the rest then falls through as text).
                tokens.append({"kind": "text", "text": ch})
                i += 1
                continue
            # Bracketed style markers.  Checked before the
            # archival regex below, which would read them as fields.
            if raw.startswith("[mark=", i):
                m = _MARK_OPEN_AT_RE.match(raw, i)
                if m is None:
                    tokens.append({"kind": "text", "text": ch})
                    i += 1
                    continue
                tokens.append({
                    "kind": "marker", "text": m.group(), "index": i,
                    "key": "highlight", "action": "open", "value": m.group(1),
                })
                i = m.end()
                continue
            if raw.startswith("[/mark]", i):
                m = _MARK_CLOSE_AT_RE.match(raw, i)
                tokens.append({
                    "kind": "marker", "text": m.group(), "index": i,
                    "key": "highlight", "action": "close",
                })
                i = m.end()
                continue
            if raw.startswith("[color=", i):
                m = _COLOR_OPEN_AT_RE.match(raw, i)
                if m is None:
                    tokens.append({"kind": "text", "text": ch})
                    i += 1
                    continue
                tokens.append({
                    "kind": "marker", "text": m.group(), "index": i,
                    "key": "color", "action": "open", "value": m.group(1),
                })
                i = m.end()
                continue
            if raw.startswith("[/color]", i):
                m = _COLOR_CLOSE_AT_RE.match(raw, i)
                tokens.append({
                    "kind": "marker", "text": m.group(), "index": i,
                    "key": "color", "action": "close",
                })
                i = m.end()
                continue
            m = _ARCH_FIELD_AT_RE.match(raw, i)
            if m is not None:
                tokens.append({
                    "kind": "field",
                    "text": m.group()[1:-1],
                    "field_type": FieldType.ARCHIVAL,
                })
                i = m.end()
                continue
        if raw.startswith("**", i):
            tokens.append({
                "kind": "marker", "text": "**", "index": i,
                "key": "bold", "action": "toggle", "value": True,
            })
            i += 2
            continue
        if ch == "*":
            tokens.append({
                "kind": "marker", "text": "*", "index": i,
                "key": "italic", "action": "toggle", "value": True,
            })
            i += 1
            continue
        if raw.startswith("==", i):
            tokens.append({
                "kind": "marker", "text": "==", "index": i,
                "key": "highlight", "action": "toggle",
                "value": _DEFAULT_HIGHLIGHT,
            })
            i += 2
            continue
        tokens.append({"kind": "text", "text": ch})
        i += 1
    return tokens


def _resolve_markers(tokens: list[dict], raw: str) -> None:
    """Resolve inline style markers in place.

    ``**``/``*`` toggle the bold/italic flags; ``==`` toggles a
    highlight in the default colour; ``[mark=#HEX]``/``[/mark]`` open
    and close a custom highlight; ``[color=#HEX]``/``[/color]`` open
    and close a foreground colour.  Every content token is stamped with
    the ``(bold, italic, highlight, color)`` active at its position.

    A marker is INVALID — and becomes literal text — when:

    - it would open a style that is already on (same-flag markers
      cannot nest, and highlight/colour may not nest with each other),
      or
    - it is a closing token with no matching opener.

    Opening and closing must also match exactly: ``[mark=#HEX]`` is
    closed only by ``[/mark]`` and ``==`` only by ``==``.  An empty
    pair (``****``, ``====``, ``[mark=#FF0000][/mark]``) literalises
    BOTH markers, and any marker left open at end of line makes the
    whole suffix from it onward one literal, unstyled text token — the
    same rule as an unclosed ``{``.
    """
    bold = italic = False
    highlight: str | None = None
    color: str | None = None
    # (token index, key, opener kind) for each style currently ON.
    open_stack: list[tuple[int, str, str]] = []

    def is_on(key: str) -> bool:
        if key == "bold":
            return bold
        if key == "italic":
            return italic
        if key == "highlight":
            return highlight is not None
        return color is not None

    def set_state(key: str, value) -> None:
        nonlocal bold, italic, highlight, color
        if key == "bold":
            bold = value
        elif key == "italic":
            italic = value
        elif key == "highlight":
            highlight = value
        else:
            color = value

    def stamp(tok: dict) -> None:
        tok["bold"] = bold
        tok["italic"] = italic
        tok["highlight"] = highlight
        tok["color"] = color

    for idx, tok in enumerate(tokens):
        if tok["kind"] != "marker":
            stamp(tok)
            continue
        key = tok["key"]
        action = tok["action"]
        on = is_on(key)

        if action == "toggle":
            opening = not on
            opener_kind = "toggle"
        else:
            opening = action == "open"
            opener_kind = "explicit"

        if opening:
            # May not open a style that is already on, and highlight
            # and colour exclude each other.  Bold/italic
            # sit outside that rule and apply on top.
            blocked = on or (
                (key == "highlight" and color is not None)
                or (key == "color" and highlight is not None)
            )
            if blocked:
                tok["kind"] = "text"
                stamp(tok)
                continue
            open_stack.append((idx, key, opener_kind))
            set_state(key, tok.get("value", True))
            continue

        # Closing marker: must match both the key and the opener kind,
        # so "[mark=…]" is never closed by "==" and vice versa.  No
        # matching opener (stray close) → literal text.
        matches = [
            i for i in range(len(open_stack) - 1, -1, -1)
            if open_stack[i][1] == key and open_stack[i][2] == opener_kind
        ]
        if not matches:
            tok["kind"] = "text"
            stamp(tok)
            continue
        open_idx = matches[0]
        del open_stack[open_idx]
        # Only text/field tokens count as marker content.  A screenshot
        # element between markers therefore leaves the ** literal —
        # "**[Screenshot:3]** keeps its asterisks.
        has_content = any(
            t["kind"] in ("text", "field")
            for t in tokens[open_idx + 1:idx]
        )
        if has_content:
            pass  # both markers stay markers: consumed as boundaries
        else:
            # Empty pair → both markers literal.
            for i in (open_idx, idx):
                t = tokens[i]
                t["kind"] = "text"
                t["bold"] = False
                t["italic"] = False
                t["highlight"] = None
                t["color"] = None
        set_state(key, None if key in ("highlight", "color") else False)

    if open_stack:
        # Unbalanced markers: the suffix from the first unclosed one
        # becomes literal text, exactly like an unclosed "{".
        first_tok = min(idx for idx, _, _ in open_stack)
        suffix = raw[tokens[first_tok]["index"]:]
        del tokens[first_tok:]
        tokens.append({
            "kind": "text",
            "text": suffix,
            "bold": False,
            "italic": False,
            "highlight": None,
            "color": None,
        })


def _tokens_to_elements(tokens: list[dict]) -> list[LayoutElement]:
    """Group resolved tokens into LayoutElements.

    Consecutive text tokens sharing the same style stamp merge into one
    text element; every field and screenshot token is its own element;
    a consumed marker forces an element boundary so ``**bold**plain``
    yields two text elements.  ``style`` is an :class:`ElementStyle`
    when at least one of bold/italic/highlight/colour is set — the
    ``None`` convention is preserved otherwise.  Screenshot elements
    never carry a style.
    """
    def make(kind: str, content: str, tok: dict,
             field_type: FieldType | None = None) -> LayoutElement:
        bold = bool(tok.get("bold"))
        italic = bool(tok.get("italic"))
        highlight = tok.get("highlight")
        color = tok.get("color")
        style = None
        if bold or italic or highlight or color:
            style = ElementStyle(
                bold=bold,
                italic=italic,
                color=color,
                highlight_color=highlight,
            )
        return LayoutElement(kind=kind, content=content, field_type=field_type, style=style)

    elements: list[LayoutElement] = []
    buf: list[str] = []
    buf_tok: dict | None = None

    def flush() -> None:
        nonlocal buf, buf_tok
        if buf and buf_tok is not None:
            elements.append(make("text", "".join(buf), buf_tok))
        buf = []

    for tok in tokens:
        if tok["kind"] == "marker":
            flush()  # element boundary at every toggle
            continue
        if tok["kind"] == "screenshot":
            flush()
            # Screenshots are their own element kind: no field_type, and
            # no style (markers around one stay literal).
            elements.append(
                LayoutElement(kind="screenshot", content=tok["text"])
            )
            continue
        if tok["kind"] == "field":
            flush()
            elements.append(
                make("field", tok["text"], tok, field_type=tok["field_type"])
            )
            continue
        if buf_tok is not None and (
            bool(tok.get("bold")) != bool(buf_tok.get("bold"))
            or bool(tok.get("italic")) != bool(buf_tok.get("italic"))
            or tok.get("highlight") != buf_tok.get("highlight")
            or tok.get("color") != buf_tok.get("color")
        ):
            flush()
        buf_tok = tok
        buf.append(tok["text"])
    flush()
    return elements


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

def _element_style_key(elem: LayoutElement) -> tuple[bool, bool, str | None, str | None]:
    """The ``(bold, italic, highlight, color)`` stamp of *elem*."""
    style = elem.style
    if style is None:
        return (False, False, None, None)
    return (
        bool(style.bold),
        bool(style.italic),
        style.highlight_color or None,
        style.color or None,
    )


def _styled(key: tuple[bool, bool, str | None, str | None]) -> bool:
    bold, italic, highlight, color = key
    return bold or italic or highlight is not None or color is not None


# Marker order, outermost → innermost: bold, highlight, colour, italic.
# The close sequence is the exact reverse.  This order is what makes
# "**[color=#FF0000]*bold red*[/color]**" — the canonical combined form
# — byte-stable through format(parse(x)), since bold sits
# outside the colour run and italic inside it.
def _open_markers(key: tuple[bool, bool, str | None, str | None]) -> str:
    bold, italic, highlight, color = key
    out = ""
    if bold:
        out += "**"
    if highlight:
        out += "==" if highlight == _DEFAULT_HIGHLIGHT else f"[mark={highlight}]"
    if color:
        out += f"[color={color}]"
    if italic:
        out += "*"
    return out


def _close_markers(key: tuple[bool, bool, str | None, str | None]) -> str:
    bold, italic, highlight, color = key
    out = ""
    if italic:
        out += "*"
    if color:
        out += "[/color]"
    if highlight:
        out += "==" if highlight == _DEFAULT_HIGHLIGHT else "[/mark]"
    if bold:
        out += "**"
    return out


def format_layout_lines(lines: list[LayoutLine]) -> str:
    """Serialize :class:`LayoutLine` objects into plain text.

    - ``kind="text"`` elements → their ``content`` verbatim.
    - ``FieldType.TECHNICAL`` → ``"{Name}"``.
    - ``FieldType.ARCHIVAL`` → ``"[Name]"``.
    - Elements on the same line are concatenated.
    - Lines are joined with ``\\n``.

    Styles: consecutive elements sharing
    the same ``(bold, italic, highlight, color)`` stamp form a group;
    styled groups are wrapped in their markers in the fixed order
    bold → highlight → colour → italic (see :func:`_open_markers`).
    A ``#FFFF00`` highlight serialises as ``==...==``; any other
    highlight colour as ``[mark=#HEX]...[/mark]``; a foreground colour
    as ``[color=#HEX]...[/color]``.  ``style=None`` (or unstyled
    stamps) emit their content raw.  ``font_size`` is not
    representable and ignored.
    """
    formatted: list[str] = []
    for line in lines:
        parts: list[str] = []
        group: tuple[bool, bool, str | None, str | None] | None = None
        for elem in line.elements:
            key = _element_style_key(elem)
            if group is None:
                group = key
                if _styled(key):
                    parts.append(_open_markers(key))
            elif key != group:
                if _styled(group):
                    parts.append(_close_markers(group))
                group = key
                if _styled(key):
                    parts.append(_open_markers(key))

            if elem.kind == "text":
                parts.append(elem.content)
            elif elem.kind == "screenshot":
                parts.append(f"[Screenshot:{elem.content}]")
            elif elem.field_type == FieldType.TECHNICAL:
                parts.append(f"{{{elem.content}}}")
            elif elem.field_type == FieldType.ARCHIVAL:
                parts.append(f"[{elem.content}]")
            else:
                # Fallback — should not happen for well-formed elements
                parts.append(elem.content)
        if group is not None and _styled(group):
            parts.append(_close_markers(group))
        formatted.append("".join(parts))
    return "\n".join(formatted)
