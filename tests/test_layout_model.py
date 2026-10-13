"""Tests for the layout model."""

from __future__ import annotations

import json

import pytest

from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FieldType,
    FooterConfig,
    HeaderConfig,
    LayoutElement,
    LayoutLine,
    LayoutModel,
    LogoSlot,
    PageConfig,
    ScreenshotConfig,
    TextSlot,
    page_size_dimensions_mm,
)


# ---------------------------------------------------------------------------
# ElementStyle
# ---------------------------------------------------------------------------

class TestElementStyle:
    def test_defaults(self) -> None:
        s = ElementStyle()
        assert s.font_size is None
        assert s.bold is False
        assert s.italic is False
        assert s.color is None

    def test_custom(self) -> None:
        s = ElementStyle(font_size=14, bold=True, italic=True, color="#FF0000")
        assert s.font_size == 14
        assert s.bold is True
        assert s.italic is True
        assert s.color == "#FF0000"

    def test_to_dict(self) -> None:
        s = ElementStyle(bold=True, color="#00FF00")
        d = s.to_dict()
        assert d["bold"] is True
        assert d["color"] == "#00FF00"
        assert d["font_size"] is None

    def test_from_dict(self) -> None:
        s = ElementStyle.from_dict({"font_size": 12, "italic": True})
        assert s.font_size == 12
        assert s.italic is True
        assert s.bold is False

    def test_roundtrip(self) -> None:
        original = ElementStyle(font_size=16, bold=True, color="#ABCDEF")
        restored = ElementStyle.from_dict(original.to_dict())
        assert restored == original


# ---------------------------------------------------------------------------
# FieldType
# ---------------------------------------------------------------------------

class TestFieldType:
    def test_technical_value(self) -> None:
        assert FieldType.TECHNICAL.value == "technical"

    def test_archival_value(self) -> None:
        assert FieldType.ARCHIVAL.value == "archival"


# ---------------------------------------------------------------------------
# LayoutElement
# ---------------------------------------------------------------------------

class TestLayoutElement:
    def test_text_element(self) -> None:
        e = LayoutElement(kind="text", content="Hello")
        assert e.kind == "text"
        assert e.content == "Hello"
        assert e.field_type is None
        assert e.style is None

    def test_technical_field_element(self) -> None:
        e = LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL)
        assert e.kind == "field"
        assert e.content == "Duration"
        assert e.field_type == FieldType.TECHNICAL

    def test_archival_field_element(self) -> None:
        e = LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL)
        assert e.field_type == FieldType.ARCHIVAL

    def test_to_dict_text(self) -> None:
        e = LayoutElement(kind="text", content="---")
        d = e.to_dict()
        assert d == {"kind": "text", "content": "---"}

    def test_to_dict_field_technical(self) -> None:
        e = LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL)
        d = e.to_dict()
        assert d["field_type"] == "technical"

    def test_to_dict_field_archival(self) -> None:
        e = LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL)
        d = e.to_dict()
        assert d["field_type"] == "archival"

    def test_to_dict_with_style(self) -> None:
        style = ElementStyle(bold=True)
        e = LayoutElement(kind="text", content="X", style=style)
        d = e.to_dict()
        assert d["style"]["bold"] is True

    def test_from_dict_text(self) -> None:
        e = LayoutElement.from_dict({"kind": "text", "content": "Hi"})
        assert e.kind == "text"
        assert e.content == "Hi"
        assert e.field_type is None

    def test_from_dict_field(self) -> None:
        e = LayoutElement.from_dict({
            "kind": "field", "content": "Show", "field_type": "archival",
        })
        assert e.kind == "field"
        assert e.field_type == FieldType.ARCHIVAL

    def test_from_dict_unknown_field_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown field_type"):
            LayoutElement.from_dict({
                "kind": "field", "content": "X", "field_type": "invalid",
            })

    def test_roundtrip(self) -> None:
        style = ElementStyle(font_size=12, bold=True, color="#123456")
        original = LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL, style=style)
        restored = LayoutElement.from_dict(original.to_dict())
        assert restored.kind == original.kind
        assert restored.content == original.content
        assert restored.field_type == original.field_type
        assert restored.style == original.style


# ---------------------------------------------------------------------------
# LayoutLine
# ---------------------------------------------------------------------------

class TestLayoutLine:
    def test_single_element(self) -> None:
        line = LayoutLine(elements=[
            LayoutElement(kind="text", content="Hello"),
        ])
        assert len(line.elements) == 1

    def test_multiple_elements(self) -> None:
        line = LayoutLine(elements=[
            LayoutElement(kind="text", content="Show: "),
            LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL),
        ])
        assert len(line.elements) == 2

    def test_to_dict(self) -> None:
        line = LayoutLine(elements=[
            LayoutElement(kind="text", content="X"),
            LayoutElement(kind="field", content="Y", field_type=FieldType.TECHNICAL),
        ])
        d = line.to_dict()
        assert len(d["elements"]) == 2
        assert d["elements"][0]["kind"] == "text"
        assert d["elements"][1]["field_type"] == "technical"

    def test_from_dict(self) -> None:
        line = LayoutLine.from_dict({
            "elements": [
                {"kind": "text", "content": "A"},
                {"kind": "field", "content": "B", "field_type": "archival"},
            ]
        })
        assert len(line.elements) == 2
        assert line.elements[0].content == "A"
        assert line.elements[1].field_type == FieldType.ARCHIVAL

    def test_roundtrip(self) -> None:
        original = LayoutLine(elements=[
            LayoutElement(kind="text", content="Size: "),
            LayoutElement(kind="field", content="Size", field_type=FieldType.TECHNICAL),
        ])
        restored = LayoutLine.from_dict(original.to_dict())
        assert len(restored.elements) == len(original.elements)
        for r, o in zip(restored.elements, original.elements):
            assert r.kind == o.kind
            assert r.content == o.content
            assert r.field_type == o.field_type


# ---------------------------------------------------------------------------
# PageConfig
# ---------------------------------------------------------------------------

class TestPageConfig:
    def test_defaults(self) -> None:
        pc = PageConfig()
        assert pc.width_mm == 210.0
        assert pc.height_mm == 297.0
        assert pc.margin_mm == 15.0
        assert pc.body_font == "Helvetica"
        assert pc.header_font == "Helvetica"

    def test_to_dict(self) -> None:
        pc = PageConfig(margin_mm=20)
        d = pc.to_dict()
        assert d["width_mm"] == 210  # derived from the default A4/portrait
        assert d["margin_mm"] == 20

    def test_from_dict(self) -> None:
        # width_mm is derived from page_size (L15.7c): A3 -> 297 mm.
        pc = PageConfig.from_dict({"page_size": "A3", "body_font": "Arial"})
        assert pc.width_mm == 297
        assert pc.body_font == "Arial"
        assert pc.margin_mm == 15.0  # default preserved

    def test_ignores_unknown_keys(self) -> None:
        pc = PageConfig.from_dict({"margin_mm": 100, "unknown_key": 42})
        assert pc.margin_mm == 100

    def test_page_size_dimensions_portrait(self) -> None:
        assert page_size_dimensions_mm("A4", "portrait") == (210.0, 297.0)

    def test_page_size_dimensions_landscape_swaps_axes(self) -> None:
        assert page_size_dimensions_mm("A4", "landscape") == (297.0, 210.0)

    def test_page_size_dimensions_letter_and_a3(self) -> None:
        assert page_size_dimensions_mm("Letter", "portrait") == (216.0, 279.0)
        assert page_size_dimensions_mm("A3", "landscape") == (420.0, 297.0)

    def test_effective_margins_uniform_fallback(self) -> None:
        pc = PageConfig(margin_mm=18.0)
        assert pc.effective_margins_mm() == (18.0, 18.0, 18.0, 18.0)

    def test_effective_margins_partial_override(self) -> None:
        pc = PageConfig(margin_mm=15.0, margin_top_mm=30.0)
        assert pc.effective_margins_mm() == (30.0, 15.0, 15.0, 15.0)

    def test_round_trip_preserves_page_geometry(self) -> None:
        pc = PageConfig(
            page_size="Letter",
            orientation="landscape",
            margin_top_mm=10.0,
            margin_right_mm=12.5,
            margin_bottom_mm=14.0,
            margin_left_mm=16.5,
        )
        restored = PageConfig.from_dict(pc.to_dict())
        assert restored.page_size == "Letter"
        assert restored.orientation == "landscape"
        assert restored.width_mm == 279.0  # derived: Letter landscape
        assert (
            restored.margin_top_mm,
            restored.margin_right_mm,
            restored.margin_bottom_mm,
            restored.margin_left_mm,
        ) == (10.0, 12.5, 14.0, 16.5)


# ---------------------------------------------------------------------------
# ScreenshotConfig
# ---------------------------------------------------------------------------

class TestScreenshotConfig:
    def test_defaults(self) -> None:
        sc = ScreenshotConfig()
        assert sc.enabled is True
        assert sc.max_width_px == 200
        assert sc.max_height_px == 150
        assert sc.layout == "row"

    def test_to_dict(self) -> None:
        sc = ScreenshotConfig(enabled=False, layout="grid")
        d = sc.to_dict()
        assert d["enabled"] is False
        assert d["layout"] == "grid"

    def test_from_dict(self) -> None:
        sc = ScreenshotConfig.from_dict({"max_width_px": 300, "layout": "grid"})
        assert sc.max_width_px == 300
        assert sc.layout == "grid"
        assert sc.enabled is True  # default


# ---------------------------------------------------------------------------
# TextSlot / HeaderConfig / FooterConfig (L15.7d)
# ---------------------------------------------------------------------------

class TestTextSlot:
    def test_roundtrip_preserves_fields(self) -> None:
        """TextSlot round-trip preserves text/font_size/bold."""
        slot = TextSlot(text="Bootleg [Year]", font_size=12, bold=True)
        restored = TextSlot.from_dict(slot.to_dict())
        assert restored.text == "Bootleg [Year]"
        assert restored.font_size == 12
        assert restored.bold is True

    def test_defaults(self) -> None:
        s = TextSlot()
        assert s.text == ""
        assert s.font_size == 10
        assert s.bold is False


class TestLogoSlot:
    """LogoSlot + kind-discriminated slot serialisation (L15.7f.1)."""

    def test_zero_width_raises(self) -> None:
        """LogoSlot with width_mm=0 raises ValueError."""
        with pytest.raises(ValueError):
            LogoSlot(width_mm=0)

    def test_logo_slot_roundtrips_through_layout_model(self) -> None:
        """header.center = LogoSlot survives to_dict/from_dict both fields."""
        layout = LayoutModel(
            name="With Logo",
            show_header=[],
            recording_body=[],
            header=HeaderConfig(
                enabled=True,
                center=LogoSlot(width_mm=15, logo_file="x.png"),
            ),
        )
        restored = LayoutModel.from_dict(layout.to_dict())
        assert restored.header is not None
        center = restored.header.center
        assert isinstance(center, LogoSlot)
        assert center.width_mm == 15
        assert center.logo_file == "x.png"

    def test_v4_text_slot_dict_without_kind_loads_as_text_slot(self) -> None:
        """A v4-style header dict (no "kind" key) loads as a TextSlot."""
        layout = LayoutModel.from_dict({
            "name": "Legacy Header",
            "show_header": [],
            "recording_body": [],
            "header": {
                "enabled": True,
                "scope": "all",
                "center": {"text": "A", "font_size": 10, "bold": False},
            },
        })
        assert layout.header is not None
        center = layout.header.center
        assert isinstance(center, TextSlot)
        assert center.text == "A"
        assert center.font_size == 10
        assert center.bold is False

    def test_logo_kind_dict_loads_as_logo_slot(self) -> None:
        """A header dict with kind == "logo" loads as a LogoSlot."""
        layout = LayoutModel.from_dict({
            "name": "Logo Header",
            "show_header": [],
            "recording_body": [],
            "header": {
                "enabled": True,
                "scope": "all",
                "center": {"kind": "logo", "width_mm": 15, "logo_file": "x.png"},
            },
        })
        assert layout.header is not None
        center = layout.header.center
        assert isinstance(center, LogoSlot)
        assert center.width_mm == 15
        assert center.logo_file == "x.png"


class TestHeaderConfig:
    def test_roundtrip_through_layout_model(self) -> None:
        """HeaderConfig with scope="all" round-trips through LayoutModel."""
        layout = LayoutModel(
            name="With Header",
            show_header=[],
            recording_body=[],
            header=HeaderConfig(
                enabled=True,
                scope="all",
                left=TextSlot(text="[Show]"),
                center=TextSlot(text="Bootleg Catalog", font_size=12),
                right=TextSlot(text="[Year]", bold=True),
            ),
        )
        restored = LayoutModel.from_dict(layout.to_dict())
        assert restored.header is not None
        assert restored.header.enabled is True
        assert restored.header.scope == "all"
        assert restored.header.left == TextSlot(text="[Show]")
        assert restored.header.center == TextSlot(text="Bootleg Catalog", font_size=12)
        assert restored.header.right == TextSlot(text="[Year]", bold=True)

    def test_invalid_scope_raises(self) -> None:
        """scope="invalid" raises ValueError."""
        with pytest.raises(ValueError):
            HeaderConfig(scope="invalid")


class TestFooterConfig:
    def test_only_center_populated_preserves_none(self) -> None:
        """FooterConfig with only center populated preserves None on left/right."""
        footer = FooterConfig(enabled=True, center=TextSlot(text="Page footer"))
        restored = FooterConfig.from_dict(footer.to_dict())
        assert restored.left is None
        assert restored.right is None
        assert restored.center == TextSlot(text="Page footer")


class TestLayoutWithoutHeaderFooter:
    def test_serializes_without_header_footer_keys(self) -> None:
        """LayoutModel without header/footer omits those keys on save."""
        layout = LayoutModel(name="No Chrome", show_header=[], recording_body=[])
        d = layout.to_dict()
        assert "header" not in d
        assert "footer" not in d

    def test_loading_dict_without_keys_yields_none(self) -> None:
        """Loading a dict without header/footer keys yields None for both."""
        layout = LayoutModel(name="No Chrome", show_header=[], recording_body=[])
        restored = LayoutModel.from_dict(layout.to_dict())
        assert restored.header is None
        assert restored.footer is None


# ---------------------------------------------------------------------------
# LayoutModel
# ---------------------------------------------------------------------------

class TestLayoutModel:
    @pytest.fixture
    def sample_layout(self) -> LayoutModel:
        return LayoutModel(
            name="Default",
            show_header=[
                LayoutLine(elements=[
                    LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL),
                ]),
                LayoutLine(elements=[
                    LayoutElement(kind="field", content="Location", field_type=FieldType.ARCHIVAL),
                    LayoutElement(kind="text", content=", "),
                    LayoutElement(kind="field", content="Country", field_type=FieldType.ARCHIVAL),
                ]),
            ],
            recording_body=[
                LayoutLine(elements=[
                    LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL),
                    LayoutElement(kind="text", content=" | "),
                    LayoutElement(kind="field", content="Resolution", field_type=FieldType.TECHNICAL),
                ]),
                LayoutLine(elements=[
                    LayoutElement(kind="field", content="Type", field_type=FieldType.ARCHIVAL),
                    LayoutElement(kind="text", content=" / "),
                    LayoutElement(kind="field", content="Source", field_type=FieldType.ARCHIVAL),
                ]),
            ],
            separator="\u2500",
        )

    def test_creation(self, sample_layout: LayoutModel) -> None:
        assert sample_layout.name == "Default"
        assert len(sample_layout.show_header) == 2
        assert len(sample_layout.recording_body) == 2

    def test_defaults(self) -> None:
        layout = LayoutModel(name="Empty", show_header=[], recording_body=[])
        assert layout.separator == "\u2500"
        assert isinstance(layout.page_config, PageConfig)
        assert isinstance(layout.screenshot_config, ScreenshotConfig)

    def test_to_dict(self, sample_layout: LayoutModel) -> None:
        d = sample_layout.to_dict()
        assert d["name"] == "Default"
        assert len(d["show_header"]) == 2
        assert len(d["recording_body"]) == 2
        assert d["separator"] == "\u2500"
        assert "page_config" in d
        assert "screenshot_config" in d

    def test_from_dict(self, sample_layout: LayoutModel) -> None:
        d = sample_layout.to_dict()
        restored = LayoutModel.from_dict(d)
        assert restored.name == sample_layout.name
        assert len(restored.show_header) == len(sample_layout.show_header)
        assert len(restored.recording_body) == len(sample_layout.recording_body)

    def test_roundtrip_preserves_field_types(self, sample_layout: LayoutModel) -> None:
        d = sample_layout.to_dict()
        restored = LayoutModel.from_dict(d)
        # Check that field_type is preserved for all field elements
        for line in restored.show_header + restored.recording_body:
            for elem in line.elements:
                if elem.kind == "field":
                    # field_type must not be None
                    assert elem.field_type is not None

    def test_json_roundtrip(self, sample_layout: LayoutModel) -> None:
        json_str = sample_layout.to_json()
        restored = LayoutModel.from_json(json_str)
        assert restored.name == sample_layout.name
        assert len(restored.show_header) == len(sample_layout.show_header)

    def test_json_is_valid_json(self, sample_layout: LayoutModel) -> None:
        json_str = sample_layout.to_json()
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_separator_none(self) -> None:
        layout = LayoutModel(
            name="No Sep",
            show_header=[],
            recording_body=[],
            separator=None,
        )
        d = layout.to_dict()
        assert d["separator"] is None
        restored = LayoutModel.from_dict(d)
        assert restored.separator is None

    def test_custom_page_config(self) -> None:
        layout = LayoutModel(
            name="Custom",
            show_header=[],
            recording_body=[],
            page_config=PageConfig(page_size="A3", margin_mm=20),
        )
        d = layout.to_dict()
        restored = LayoutModel.from_dict(d)
        assert restored.page_config.width_mm == 297  # derived from A3
        assert restored.page_config.margin_mm == 20

    def test_custom_screenshot_config(self) -> None:
        layout = LayoutModel(
            name="Screenshots",
            show_header=[],
            recording_body=[],
            screenshot_config=ScreenshotConfig(enabled=False, max_width_px=400),
        )
        d = layout.to_dict()
        restored = LayoutModel.from_dict(d)
        assert restored.screenshot_config.enabled is False
        assert restored.screenshot_config.max_width_px == 400

    def test_empty_show_header(self) -> None:
        layout = LayoutModel(name="X", show_header=[], recording_body=[])
        assert layout.show_header == []

    def test_empty_recording_body(self) -> None:
        layout = LayoutModel(name="X", show_header=[], recording_body=[])
        assert layout.recording_body == []


# ---------------------------------------------------------------------------
# Comprehensive roundtrip test
# ---------------------------------------------------------------------------

class TestFullRoundtrip:
    """End-to-end serialisation roundtrip preserving all data."""

    def test_complete_layout_roundtrip(self) -> None:
        original = LayoutModel(
            name="Complete Layout",
            show_header=[
                LayoutLine(elements=[
                    LayoutElement(
                        kind="field", content="Show", field_type=FieldType.ARCHIVAL,
                        style=ElementStyle(font_size=16, bold=True),
                    ),
                ]),
                LayoutLine(elements=[
                    LayoutElement(kind="field", content="Location", field_type=FieldType.ARCHIVAL),
                    LayoutElement(kind="text", content=", "),
                    LayoutElement(kind="field", content="Country", field_type=FieldType.ARCHIVAL),
                ]),
            ],
            recording_body=[
                LayoutLine(elements=[
                    LayoutElement(kind="text", content="Format: "),
                    LayoutElement(kind="field", content="Extension", field_type=FieldType.TECHNICAL),
                    LayoutElement(kind="text", content=" | Duration: "),
                    LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL),
                ]),
                LayoutLine(elements=[
                    LayoutElement(kind="text", content="Type: "),
                    LayoutElement(kind="field", content="Type", field_type=FieldType.ARCHIVAL),
                    LayoutElement(kind="text", content=" | Source: "),
                    LayoutElement(kind="field", content="Source", field_type=FieldType.ARCHIVAL),
                ]),
                LayoutLine(elements=[
                    LayoutElement(kind="field", content="Notes", field_type=FieldType.ARCHIVAL),
                ]),
            ],
            separator="=",
            page_config=PageConfig(margin_mm=20, body_font="Arial"),
            screenshot_config=ScreenshotConfig(enabled=False, layout="grid"),
        )

        json_str = original.to_json()
        restored = LayoutModel.from_json(json_str)

        assert restored.name == original.name
        assert restored.separator == "="
        assert restored.page_config.body_font == "Arial"
        assert restored.screenshot_config.layout == "grid"

        # Verify show_header
        assert len(restored.show_header) == 2
        h0 = restored.show_header[0]
        assert h0.elements[0].kind == "field"
        assert h0.elements[0].content == "Show"
        assert h0.elements[0].field_type == FieldType.ARCHIVAL
        assert h0.elements[0].style is not None
        assert h0.elements[0].style.bold is True

        h1 = restored.show_header[1]
        assert len(h1.elements) == 3
        assert h1.elements[0].content == "Location"
        assert h1.elements[1].content == ", "
        assert h1.elements[2].content == "Country"

        # Verify recording_body
        assert len(restored.recording_body) == 3
        rb0 = restored.recording_body[0]
        assert rb0.elements[0].content == "Format: "
        assert rb0.elements[1].content == "Extension"
        assert rb0.elements[1].field_type == FieldType.TECHNICAL
        assert rb0.elements[2].content == " | Duration: "
        assert rb0.elements[3].content == "Duration"
        assert rb0.elements[3].field_type == FieldType.TECHNICAL
