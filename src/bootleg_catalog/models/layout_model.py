"""Layout model for export layouts.

Defines the block/line-based structure used by the Layout Designer and
consumed by the FieldResolver, Preview, PDF, and XLSX exporters.

LayoutModel → FieldResolver → ResolvedExportDocument → Preview / PDF / XLSX

The model is pure — no Qt, SQLite, or catalog dependency.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Field type enum
# ---------------------------------------------------------------------------

class FieldType(Enum):
    """Semantic type of a layout field.

    ``TECHNICAL`` corresponds to ``{...}`` syntax.
    ``ARCHIVAL`` corresponds to ``[...]`` syntax.
    """
    TECHNICAL = "technical"
    ARCHIVAL = "archival"


# ---------------------------------------------------------------------------
# Element style
# ---------------------------------------------------------------------------

@dataclass
class ElementStyle:
    """Visual style applied to a layout element.

    ``color`` is the foreground text colour; ``highlight_color`` is the
    highlighter fill behind the text — ``"#FFFF00"`` is what the ``==``
    marker produces.  Both are hex strings with a leading ``#``.
    """
    font_size: int | None = None
    bold: bool = False
    italic: bool = False
    color: str | None = None  # Hex: "#FF0000" — foreground
    highlight_color: str | None = None  # Hex: "#FFFF00" — highlight

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ElementStyle:
        return cls(
            font_size=data.get("font_size"),
            bold=data.get("bold", False),
            italic=data.get("italic", False),
            color=data.get("color"),
            highlight_color=data.get("highlight_color"),
        )


# ---------------------------------------------------------------------------
# Layout element
# ---------------------------------------------------------------------------

@dataclass
class LayoutElement:
    """A single element within a layout line.

    ``kind`` is either ``"text"`` (literal string), ``"field"``
    (reference to a registered field) or ``"screenshot"`` (an inline
    ``[Screenshot:N]`` block).

    For ``"field"`` elements, ``content`` is the field name (e.g.
    ``"Duration"``) and ``field_type`` indicates whether it is
    ``TECHNICAL`` or ``ARCHIVAL``.  This is the semantic distinction
    that replaces the ``{}``/``[]`` syntax in the serialised model.

    For ``"text"`` elements, ``field_type`` must be ``None``.

    For ``"screenshot"`` elements, ``content`` is the digit-only
    screenshot count ``N`` (the text between ``Screenshot:`` and ``]``)
    and ``field_type`` is always ``None`` — it is not a field
    reference, so it is never resolved against the registry and never
    carries a style.
    """
    kind: Literal["text", "field", "screenshot"]
    content: str
    field_type: FieldType | None = None
    style: ElementStyle | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("text", "field", "screenshot"):
            raise ValueError(
                f"Unknown layout element kind {self.kind!r}.  "
                "Valid values: 'text', 'field', 'screenshot'"
            )
        if self.kind == "screenshot":
            if self.field_type is not None:
                raise ValueError(
                    "screenshot elements must not carry a field_type."
                )
            if not self.content.isdigit():
                raise ValueError(
                    "screenshot element content must be digit-only, "
                    f"got: {self.content!r}"
                )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind,
            "content": self.content,
        }
        if self.field_type is not None:
            d["field_type"] = self.field_type.value
        if self.style is not None:
            d["style"] = self.style.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LayoutElement:
        kind = data["kind"]
        content = data["content"]

        raw_ft = data.get("field_type")
        field_type: FieldType | None = None
        if raw_ft is not None:
            field_type = _parse_field_type(raw_ft)

        raw_style = data.get("style")
        style: ElementStyle | None = None
        if raw_style is not None:
            style = ElementStyle.from_dict(raw_style)

        # The constructor validates kind (and, for screenshots, that the
        # content is digit-only), so a corrupt payload raises here.
        return cls(kind=kind, content=content, field_type=field_type, style=style)


def _parse_field_type(value: str) -> FieldType:
    """Parse a field type string, raising ValueError on unknown values."""
    try:
        return FieldType(value)
    except ValueError:
        raise ValueError(
            f"Unknown field_type {value!r}.  "
            f"Valid values: {[ft.value for ft in FieldType]}"
        )


# ---------------------------------------------------------------------------
# Layout line
# ---------------------------------------------------------------------------

@dataclass
class LayoutLine:
    """A single line in a layout section (show_header or recording_body)."""
    elements: list[LayoutElement]

    def to_dict(self) -> dict[str, Any]:
        return {"elements": [e.to_dict() for e in self.elements]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LayoutLine:
        return cls(elements=[LayoutElement.from_dict(e) for e in data["elements"]])


# ---------------------------------------------------------------------------
# Page / screenshot config
# ---------------------------------------------------------------------------

_PAGE_SIZES_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "Letter": (216.0, 279.0),
    "A3": (297.0, 420.0),
}


def page_size_dimensions_mm(page_size: str, orientation: str) -> tuple[float, float]:
    """Return ``(width_mm, height_mm)`` for *page_size*.

    Recognised sizes: ``"A4"`` (210x297), ``"Letter"`` (216x279) and
    ``"A3"`` (297x420).  *orientation* swaps the axes for
    ``"landscape"``; anything else renders portrait.
    """
    try:
        width_mm, height_mm = _PAGE_SIZES_MM[page_size]
    except KeyError:
        raise ValueError(
            f"Unknown page_size {page_size!r}.  "
            f"Valid values: {sorted(_PAGE_SIZES_MM)}"
        ) from None
    if orientation.lower() == "landscape":
        return height_mm, width_mm
    return width_mm, height_mm


@dataclass
class PageConfig:
    """Page dimensions and fonts for PDF export.

    ``page_size`` + ``orientation`` are the primary page geometry:
    ``width_mm``/``height_mm`` are DERIVED from them on construction —
    any explicit ``width_mm``/``height_mm`` passed to the constructor
    is ignored.  Both are serialized: the exporters and the preview
    read the derived millimetre values, while the named size and
    orientation drive the Page Setup UI.

    Per-side margins override the uniform ``margin_mm`` on that side;
    ``None`` falls back to ``margin_mm``.
    """
    page_size: str = "A4"              # "A4" | "Letter" | "A3"
    orientation: str = "portrait"      # "portrait" | "landscape"
    width_mm: float = 210.0            # derived from page_size + orientation
    height_mm: float = 297.0           # derived from page_size + orientation
    margin_mm: float = 15.0
    margin_top_mm: float | None = None
    margin_right_mm: float | None = None
    margin_bottom_mm: float | None = None
    margin_left_mm: float | None = None
    body_font: str = "Helvetica"
    header_font: str = "Helvetica"

    def __post_init__(self) -> None:
        self.width_mm, self.height_mm = page_size_dimensions_mm(
            self.page_size, self.orientation
        )

    def effective_margins_mm(self) -> tuple[float, float, float, float]:
        """Return ``(top, right, bottom, left)`` effective margins in mm.

        A per-side margin of ``None`` falls back to the uniform
        ``margin_mm``.
        """
        return (
            self.margin_mm if self.margin_top_mm is None else self.margin_top_mm,
            self.margin_mm if self.margin_right_mm is None else self.margin_right_mm,
            self.margin_mm if self.margin_bottom_mm is None else self.margin_bottom_mm,
            self.margin_mm if self.margin_left_mm is None else self.margin_left_mm,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PageConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ScreenshotConfig:
    """Screenshot rendering settings for preview and export.

    ``photos_per_row`` only applies when ``layout == "grid"``; older
    payloads that omit it fall back to 3, which is what
    the renderers assume when the attribute is absent.
    """
    enabled: bool = True
    max_width_px: int = 200
    max_height_px: int = 150
    layout: Literal["grid", "row"] = "row"
    photos_per_row: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScreenshotConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Header / footer text slots
# ---------------------------------------------------------------------------

@dataclass
class TextSlot:
    """A header/footer text position.

    ``text`` supports ``{Field}`` and ``[Field]`` markers, resolved at
    render time against a recording (see
    :func:`bootleg_catalog.core.text_slot_resolver.resolve_text_slot`).
    Missing fields resolve to an empty string.
    """
    text: str = ""
    font_size: int = 10
    bold: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextSlot:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class LogoSlot:
    """A header/footer logo image slot.

    ``logo_file`` is a RELATIVE filename inside the layout storage
    directory (``app_data_dir()/layouts/``), written and cleaned up by
    :class:`~bootleg_catalog.core.layout_manager.LayoutManager` as
    ``<sanitised>.logo.<slot>.<ext>``; ``None`` means no image assigned.
    Its existence is deliberately NOT validated on load — a stale
    reference is the render layer's problem, the same policy as missing
    referenced screenshots.  ``width_mm`` must be positive.
    """
    width_mm: float = 20.0
    logo_file: str | None = None

    def __post_init__(self) -> None:
        if self.width_mm <= 0:
            raise ValueError(
                f"LogoSlot width_mm must be > 0, got: {self.width_mm!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LogoSlot:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


_SlotType = TextSlot | LogoSlot


def _slot_to_dict(slot: _SlotType) -> dict[str, Any]:
    """Serialise a slot, tagging it with its ``kind``.

    ``TextSlot`` -> ``{"kind": "text", ...}``; ``LogoSlot`` ->
    ``{"kind": "logo", ...}``.  The key discriminates on load; it is
    absent from older payloads, which are always text.
    """
    d = slot.to_dict()
    d["kind"] = "logo" if isinstance(slot, LogoSlot) else "text"
    return d


def _slot_from_dict(data: Any) -> _SlotType | None:
    """Build a TextSlot or LogoSlot from serialised data.

    ``None`` stays ``None``.  ``kind == "text"`` — or a MISSING ``kind``,
    for backward compatibility with layouts saved before logo slots
    existed — yields a
    :class:`TextSlot`; ``"logo"`` yields a :class:`LogoSlot`; any other
    value raises :class:`ValueError`.
    """
    if data is None:
        return None
    kind = data.get("kind", "text")
    if kind == "text":
        return TextSlot.from_dict(data)
    if kind == "logo":
        return LogoSlot.from_dict(data)
    raise ValueError(
        f"Unknown slot kind {kind!r}.  Valid values: 'text', 'logo'"
    )


@dataclass
class HeaderConfig:
    """Page header configuration (union slots: text or logo).

    ``scope`` is ``"all"`` (paint on every page) or ``"first"`` (first
    page only).  Each of the three slots may be ``None`` or hold a
    :class:`TextSlot` or :class:`LogoSlot`; ``None`` slots are
    preserved as ``None`` through serialisation and are never painted.
    Serialisation tags each slot with a ``kind`` key (``"text"`` /
    ``"logo"``) so :meth:`from_dict` can discriminate; a missing
    ``kind`` means text (layouts saved before logo slots existed).
    """
    enabled: bool = False
    scope: str = "all"                  # "all" | "first"
    left: TextSlot | LogoSlot | None = None
    center: TextSlot | LogoSlot | None = None
    right: TextSlot | LogoSlot | None = None

    def __post_init__(self) -> None:
        if self.scope not in ("all", "first"):
            raise ValueError(
                f"Unknown header scope {self.scope!r}.  "
                "Valid values: 'all', 'first'"
            )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"enabled": self.enabled, "scope": self.scope}
        if self.left is not None:
            d["left"] = _slot_to_dict(self.left)
        if self.center is not None:
            d["center"] = _slot_to_dict(self.center)
        if self.right is not None:
            d["right"] = _slot_to_dict(self.right)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HeaderConfig:
        return cls(
            enabled=data.get("enabled", False),
            scope=data.get("scope", "all"),
            left=_slot_from_dict(data.get("left")),
            center=_slot_from_dict(data.get("center")),
            right=_slot_from_dict(data.get("right")),
        )


@dataclass
class FooterConfig:
    """Page footer configuration (union slots: text or logo).

    Footers have no scope — they paint on every page.  Slot semantics
    as in :class:`HeaderConfig` (union of TextSlot | LogoSlot | None,
    discriminated by the ``kind`` key on serialise).
    """
    enabled: bool = False
    left: TextSlot | LogoSlot | None = None
    center: TextSlot | LogoSlot | None = None
    right: TextSlot | LogoSlot | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"enabled": self.enabled}
        if self.left is not None:
            d["left"] = _slot_to_dict(self.left)
        if self.center is not None:
            d["center"] = _slot_to_dict(self.center)
        if self.right is not None:
            d["right"] = _slot_to_dict(self.right)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FooterConfig:
        return cls(
            enabled=data.get("enabled", False),
            left=_slot_from_dict(data.get("left")),
            center=_slot_from_dict(data.get("center")),
            right=_slot_from_dict(data.get("right")),
        )


# ---------------------------------------------------------------------------
# Layout model
# ---------------------------------------------------------------------------

@dataclass
class LayoutModel:
    """Complete layout definition for an export.

    The layout has two sections:

    - ``show_header``: lines rendered once per show group.
    - ``recording_body``: lines rendered for each recording within a show.

    ``separator`` is the character(s) drawn between show groups (or
    ``None`` for no separator).

    ``header``/``footer`` are optional page-chrome slot rows
    (text or logo); both default to ``None`` and are
    serialised only when set, so layouts saved before they existed
    load unchanged.
    """
    name: str
    show_header: list[LayoutLine]
    recording_body: list[LayoutLine]
    separator: str | None = "\u2500"  # ─
    page_config: PageConfig = field(default_factory=PageConfig)
    screenshot_config: ScreenshotConfig = field(default_factory=ScreenshotConfig)
    header: HeaderConfig | None = None
    footer: FooterConfig | None = None

    # -- Serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "show_header": [line.to_dict() for line in self.show_header],
            "recording_body": [line.to_dict() for line in self.recording_body],
            "separator": self.separator,
            "page_config": self.page_config.to_dict(),
            "screenshot_config": self.screenshot_config.to_dict(),
        }
        if self.header is not None:
            d["header"] = self.header.to_dict()
        if self.footer is not None:
            d["footer"] = self.footer.to_dict()
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LayoutModel:
        # Read "name" first: on non-dict input this raises TypeError,
        # which LayoutManager relies on to skip corrupt files silently.
        name = data["name"]
        raw_header = data.get("header")
        raw_footer = data.get("footer")
        return cls(
            name=name,
            show_header=[LayoutLine.from_dict(l) for l in data.get("show_header", [])],
            recording_body=[LayoutLine.from_dict(l) for l in data.get("recording_body", [])],
            separator=data.get("separator", "\u2500"),
            page_config=PageConfig.from_dict(data.get("page_config", {})),
            screenshot_config=ScreenshotConfig.from_dict(data.get("screenshot_config", {})),
            header=HeaderConfig.from_dict(raw_header) if raw_header is not None else None,
            footer=FooterConfig.from_dict(raw_footer) if raw_footer is not None else None,
        )

    @classmethod
    def from_json(cls, text: str) -> LayoutModel:
        return cls.from_dict(json.loads(text))
