"""Tests for LayoutManager — named layout persistence (L12.1).

All tests run against tmp_path; the real application data directory is
never touched (the one default-directory test patches app_data_dir).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.core.field_registry import (
    ARCHIVAL_FIELDS,
    SUGGESTION_FIELDS,
    TECHNICAL_FIELDS,
)
from bootleg_catalog.core.layout_manager import (
    LayoutManager,
    LayoutManagerError,
    default_layout,
)
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FieldType,
    HeaderConfig,
    LayoutElement,
    LayoutLine,
    LayoutModel,
    LogoSlot,
    PageConfig,
    ScreenshotConfig,
)


def _make_layout(name: str = "Test Layout") -> LayoutModel:
    return LayoutModel(
        name=name,
        show_header=[
            LayoutLine(elements=[
                LayoutElement(
                    kind="field", content="Show",
                    field_type=FieldType.ARCHIVAL,
                ),
            ]),
        ],
        recording_body=[
            LayoutLine(elements=[
                LayoutElement(
                    kind="field", content="Duration",
                    field_type=FieldType.TECHNICAL,
                ),
            ]),
        ],
        separator="-",
    )


def _rich_layout(name: str = "Rich Layout") -> LayoutModel:
    return LayoutModel(
        name=name,
        show_header=[
            LayoutLine(elements=[
                LayoutElement(
                    kind="field", content="Show",
                    field_type=FieldType.ARCHIVAL,
                    style=ElementStyle(font_size=16, bold=True, color="#112233"),
                ),
            ]),
            LayoutLine(elements=[
                LayoutElement(kind="text", content="Venue: "),
                LayoutElement(
                    kind="field", content="Venue",
                    field_type=FieldType.ARCHIVAL,
                ),
                LayoutElement(kind="text", content=" ("),
                LayoutElement(
                    kind="field", content="Date",
                    field_type=FieldType.ARCHIVAL,
                ),
                LayoutElement(kind="text", content=")"),
            ]),
        ],
        recording_body=[
            LayoutLine(elements=[
                LayoutElement(kind="text", content="Format: "),
                LayoutElement(
                    kind="field", content="Extension",
                    field_type=FieldType.TECHNICAL,
                ),
                LayoutElement(kind="text", content=" | Duration: "),
                LayoutElement(
                    kind="field", content="Duration",
                    field_type=FieldType.TECHNICAL,
                ),
            ]),
            LayoutLine(elements=[
                LayoutElement(kind="text", content="Notes: "),
                LayoutElement(
                    kind="field", content="Notes",
                    field_type=FieldType.ARCHIVAL,
                ),
            ]),
        ],
        separator="=",
        page_config=PageConfig(
            width_mm=210.0, height_mm=297.0, margin_mm=20.0,
            body_font="Arial", header_font="Helvetica",
        ),
        screenshot_config=ScreenshotConfig(
            enabled=False, max_width_px=300, max_height_px=225,
            layout="grid",
        ),
    )


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_save_then_list_returns_name(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("My Layout"))
        assert mgr.list_layouts() == ["My Layout"]

    def test_load_roundtrip_preserves_all_fields(self, tmp_path: Path) -> None:
        original = _rich_layout("Rich Layout")
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(original)
        loaded = mgr.load("Rich Layout")
        assert loaded == original
        assert loaded.show_header == original.show_header
        assert loaded.recording_body == original.recording_body
        assert loaded.separator == "="
        assert loaded.page_config == original.page_config
        assert loaded.page_config.margin_mm == 20.0
        assert loaded.page_config.body_font == "Arial"
        assert loaded.screenshot_config == original.screenshot_config
        assert loaded.screenshot_config.enabled is False
        assert loaded.screenshot_config.layout == "grid"
        assert loaded.recording_body[0].elements[1].field_type == FieldType.TECHNICAL

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(
            LayoutManagerError, match="Layout not found: Missing Layout"
        ):
            mgr.load("Missing Layout")

    def test_load_corrupt_raises(self, tmp_path: Path) -> None:
        (tmp_path / "broken.json").write_text("NOT JSON {{{", encoding="utf-8")
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(
            LayoutManagerError, match="Layout file is corrupt: broken"
        ):
            mgr.load("broken")

    def test_load_read_oserror_wrapped_as_corrupt(self, tmp_path: Path) -> None:
        """OS read failures surface as the corrupt-layout error, chained."""
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("Locked"))
        with patch.object(
            Path, "read_text", side_effect=PermissionError("access denied")
        ):
            with pytest.raises(
                LayoutManagerError, match="Layout file is corrupt: Locked"
            ) as exc_info:
                mgr.load("Locked")
        assert isinstance(exc_info.value.__cause__, PermissionError)

    def test_save_overwrites_same_name(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(LayoutModel(name="Dup", show_header=[], recording_body=[], separator="I"))
        mgr.save(LayoutModel(name="Dup", show_header=[], recording_body=[], separator="II"))
        assert mgr.list_layouts() == ["Dup"]
        assert len(list(tmp_path.glob("*.json"))) == 1
        assert mgr.load("Dup").separator == "II"

    def test_save_forbidden_chars_writes_single_flat_file(
        self, tmp_path: Path
    ) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("My/Layout\\Sub"))
        entries = list(tmp_path.iterdir())
        assert len(entries) == 1
        assert entries[0].is_file()
        assert entries[0].name == "My_Layout_Sub.json"

    def test_json_payload_preserves_original_name(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("My/Layout"))
        assert (tmp_path / "My_Layout.json").is_file()
        payload = json.loads(
            (tmp_path / "My_Layout.json").read_text(encoding="utf-8")
        )
        assert payload["name"] == "My/Layout"
        assert mgr.load("My/Layout").name == "My/Layout"

    def test_case_preserved_and_whitespace_collapsed(self, tmp_path: Path) -> None:
        original_name = "  MY   Layout  "
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout(original_name))
        # File stem: case kept, runs collapsed, edges stripped.
        assert (tmp_path / "MY Layout.json").is_file()
        # Payload: original name untouched.
        payload = json.loads((tmp_path / "MY Layout.json").read_text(encoding="utf-8"))
        assert payload["name"] == original_name

    def test_empty_name_raises(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(LayoutManagerError, match="Layout name is empty or invalid"):
            mgr.save(_make_layout(""))

    def test_whitespace_only_name_raises(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(LayoutManagerError, match="Layout name is empty or invalid"):
            mgr.save(_make_layout("   "))

    def test_only_forbidden_chars_raises(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(LayoutManagerError, match="Layout name is empty or invalid"):
            mgr.save(_make_layout("///"))

    def test_save_leaves_no_temp_file(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("Atomic"))
        assert (tmp_path / "Atomic.json").is_file()
        assert list(tmp_path.glob("*.tmp")) == []

    def test_save_failure_cleans_up_temp_file(self, tmp_path: Path) -> None:
        """A failing atomic replace is wrapped, chained, and cleaned up."""
        mgr = LayoutManager(base_dir=tmp_path)
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with pytest.raises(
                LayoutManagerError, match="Could not save layout 'Doomed'"
            ) as exc_info:
                mgr.save(_make_layout("Doomed"))
        # The surface type is LayoutManagerError, never a leaked OSError...
        assert not isinstance(exc_info.value, OSError)
        # ...and the original OS error is chained as __cause__.
        assert isinstance(exc_info.value.__cause__, OSError)
        assert "disk full" in str(exc_info.value)
        assert list(tmp_path.glob("*.tmp")) == []
        assert not (tmp_path / "Doomed.json").exists()

    def test_save_write_oserror_wrapped(self, tmp_path: Path) -> None:
        """Permission errors during the write are wrapped as LayoutManagerError."""
        mgr = LayoutManager(base_dir=tmp_path)
        with patch.object(
            Path, "write_text", side_effect=PermissionError("access denied")
        ):
            with pytest.raises(
                LayoutManagerError, match="Could not save layout 'Blocked'"
            ) as exc_info:
                mgr.save(_make_layout("Blocked"))
        assert isinstance(exc_info.value.__cause__, PermissionError)
        assert list(tmp_path.glob("*.tmp")) == []
        assert not (tmp_path / "Blocked.json").exists()


# ---------------------------------------------------------------------------
# Windows reserved device names
# ---------------------------------------------------------------------------


class TestReservedDeviceNames:
    def test_con_raises(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(LayoutManagerError, match="Layout name is empty or invalid"):
            mgr.save(_make_layout("CON"))

    def test_lowercase_con_raises(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(LayoutManagerError, match="Layout name is empty or invalid"):
            mgr.save(_make_layout("con"))

    def test_con_with_extension_raises(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(LayoutManagerError, match="Layout name is empty or invalid"):
            mgr.save(_make_layout("CON.bootleg"))

    def test_console_succeeds(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("CONsole"))
        assert (tmp_path / "CONsole.json").is_file()
        assert mgr.list_layouts() == ["CONsole"]

    def test_my_con_succeeds(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("MY CON"))
        assert (tmp_path / "MY CON.json").is_file()
        assert mgr.list_layouts() == ["MY CON"]

    def test_com3_raises(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(LayoutManagerError, match="Layout name is empty or invalid"):
            mgr.save(_make_layout("COM3"))

    def test_lpt9_raises(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(LayoutManagerError, match="Layout name is empty or invalid"):
            mgr.save(_make_layout("LPT9"))

    def test_com0_succeeds(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("COM0"))
        assert (tmp_path / "COM0.json").is_file()

    def test_lpt0_succeeds(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("LPT0"))
        assert (tmp_path / "LPT0.json").is_file()


# ---------------------------------------------------------------------------
# list / exists
# ---------------------------------------------------------------------------


class TestListExists:
    def test_fresh_manager_empty_list(self, tmp_path: Path) -> None:
        missing = LayoutManager(base_dir=tmp_path / "missing")
        assert missing.list_layouts() == []
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert LayoutManager(base_dir=empty_dir).list_layouts() == []

    def test_exists_true_after_save_false_for_unknown(
        self, tmp_path: Path
    ) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("Known"))
        assert mgr.exists("Known") is True
        assert mgr.exists("Unknown") is False

    def test_list_sorted(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("Bravo"))
        mgr.save(_make_layout("Alpha"))
        mgr.save(_make_layout("Charlie"))
        assert mgr.list_layouts() == ["Alpha", "Bravo", "Charlie"]

    def test_list_skips_corrupt_files(self, tmp_path: Path) -> None:
        (tmp_path / "bad.json").write_text("NOT JSON {{{", encoding="utf-8")
        (tmp_path / "also_bad.json").write_text("[1, 2, 3]", encoding="utf-8")
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("Good"))
        # Corrupt files are skipped silently — no exception raised.
        assert mgr.list_layouts() == ["Good"]

    def test_list_does_not_descend_subdirectories(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested"
        nested.mkdir()
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("Top"))
        # A valid payload hidden in a sub-directory must not be listed.
        (nested / "hidden.json").write_text(
            _make_layout("Hidden").to_json(), encoding="utf-8"
        )
        assert mgr.list_layouts() == ["Top"]

    def test_default_base_dir_is_app_data_layouts(self, tmp_path: Path) -> None:
        with patch(
            "bootleg_catalog.core.layout_manager.app_data_dir",
            return_value=tmp_path,
        ):
            mgr = LayoutManager()
            mgr.save(_make_layout("Base Dir Layout"))
        assert (tmp_path / "layouts" / "Base Dir Layout.json").is_file()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_removes_file(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("Doomed"))
        mgr.delete("Doomed")
        assert not (tmp_path / "Doomed.json").exists()
        assert mgr.list_layouts() == []

    def test_delete_missing_raises(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        with pytest.raises(LayoutManagerError, match="Layout not found: Ghost"):
            mgr.delete("Ghost")


# ---------------------------------------------------------------------------
# Logo storage (L15.7f.1)
# ---------------------------------------------------------------------------


class TestLogoStorage:
    def test_save_with_pending_logos_copies_and_repoints(
        self, tmp_path: Path
    ) -> None:
        """pending_logos copies the image; the JSON stores its relative name."""
        source = tmp_path / "source.png"
        source.write_bytes(b"\x89PNG-fake-bytes")
        mgr = LayoutManager(base_dir=tmp_path)
        layout = LayoutModel(
            name="Logo Layout",
            show_header=[],
            recording_body=[],
            header=HeaderConfig(enabled=True, center=LogoSlot(width_mm=15)),
        )

        mgr.save(layout, pending_logos={"header.center": source})

        stored = tmp_path / "Logo Layout.logo.header_center.png"
        assert stored.is_file()
        assert stored.read_bytes() == b"\x89PNG-fake-bytes"
        payload = json.loads(
            (tmp_path / "Logo Layout.json").read_text(encoding="utf-8")
        )
        assert payload["header"]["center"]["logo_file"] == (
            "Logo Layout.logo.header_center.png"
        )

    def test_pending_logo_reference_survives_follow_up_save(
        self, tmp_path: Path
    ) -> None:
        """Pending logos land on disk AND in the JSON; a later save without
        pending_logos keeps the reference (L15.6 reconciliation)."""
        source = tmp_path / "source.png"
        source.write_bytes(b"\x89PNG-fake-bytes")
        mgr = LayoutManager(base_dir=tmp_path)
        layout = LayoutModel(
            name="Default",
            show_header=[],
            recording_body=[],
            header=HeaderConfig(enabled=True, center=LogoSlot(width_mm=20)),
        )

        mgr.save(layout, pending_logos={"header.center": source})

        payload = json.loads(
            (tmp_path / "Default.json").read_text(encoding="utf-8")
        )
        assert payload["header"]["center"]["logo_file"] == (
            "Default.logo.header_center.png"
        )
        assert (tmp_path / "Default.logo.header_center.png").is_file()

        # The real-world clobber: the export window re-saves the accepted
        # layout whose in-memory slot still has logo_file=None.  The JSON
        # must keep pointing at the already-copied image.
        mgr.save(layout)
        payload = json.loads(
            (tmp_path / "Default.json").read_text(encoding="utf-8")
        )
        assert payload["header"]["center"]["logo_file"] == (
            "Default.logo.header_center.png"
        )

    def test_save_returns_retargeted_layout_without_mutating_input(
        self, tmp_path: Path
    ) -> None:
        """save() returns the layout as serialised; the input is untouched."""
        source = tmp_path / "source.png"
        source.write_bytes(b"\x89PNG-fake-bytes")
        mgr = LayoutManager(base_dir=tmp_path)
        layout = LayoutModel(
            name="Default",
            show_header=[],
            recording_body=[],
            header=HeaderConfig(enabled=True, center=LogoSlot(width_mm=20)),
        )

        returned = mgr.save(layout, pending_logos={"header.center": source})

        center = returned.header.center
        assert isinstance(center, LogoSlot)
        assert center.logo_file == "Default.logo.header_center.png"
        # The caller's input object still carries the original None.
        assert layout.header.center.logo_file is None

    def test_delete_removes_json_and_logo_siblings(self, tmp_path: Path) -> None:
        """delete() removes <sanitised>.logo.* siblings; unrelated files stay."""
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(
            LayoutModel(
                name="With Logos",
                show_header=[],
                recording_body=[],
                header=HeaderConfig(
                    enabled=True,
                    center=LogoSlot(
                        logo_file="With Logos.logo.header_center.png"
                    ),
                ),
            )
        )
        (tmp_path / "With Logos.logo.header_center.png").write_bytes(b"a")
        (tmp_path / "With Logos.logo.footer_left.webp").write_bytes(b"b")
        # Another layout's logo sibling must survive this delete.
        (tmp_path / "Other.logo.header_center.png").write_bytes(b"c")

        mgr.delete("With Logos")

        assert not (tmp_path / "With Logos.json").exists()
        assert not (tmp_path / "With Logos.logo.header_center.png").exists()
        assert not (tmp_path / "With Logos.logo.footer_left.webp").exists()
        assert (tmp_path / "Other.logo.header_center.png").is_file()

        # Missing siblings are not an error: a layout with none deletes fine.
        mgr.save(_make_layout("No Logos"))
        mgr.delete("No Logos")
        assert not (tmp_path / "No Logos.json").exists()

    def test_save_with_stale_logo_file_still_writes_json(
        self, tmp_path: Path
    ) -> None:
        """A LogoSlot pointing at a missing file saves without raising."""
        mgr = LayoutManager(base_dir=tmp_path)
        layout = LayoutModel(
            name="Stale Logo",
            show_header=[],
            recording_body=[],
            header=HeaderConfig(
                enabled=True,
                center=LogoSlot(
                    logo_file="Stale Logo.logo.header_center.png"
                ),
            ),
        )

        mgr.save(layout)   # must not raise although the image never exists

        assert (tmp_path / "Stale Logo.json").is_file()
        payload = json.loads(
            (tmp_path / "Stale Logo.json").read_text(encoding="utf-8")
        )
        assert payload["header"]["center"]["logo_file"] == (
            "Stale Logo.logo.header_center.png"
        )
        assert not (tmp_path / "Stale Logo.logo.header_center.png").exists()


# ---------------------------------------------------------------------------
# ensure_default
# ---------------------------------------------------------------------------


class TestEnsureDefault:
    def test_creates_default_on_empty_directory(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        layout = mgr.ensure_default()
        assert layout.name == "Default"
        assert len(layout.show_header) > 0
        assert len(layout.recording_body) > 0
        assert mgr.list_layouts() == ["Default"]
        assert (tmp_path / "Default.json").is_file()

    def test_returns_existing_layout_when_present(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        mgr.save(_make_layout("Alpha"))
        returned = mgr.ensure_default()
        assert returned.name == "Alpha"
        # No second layout was created.
        assert mgr.list_layouts() == ["Alpha"]

    def test_idempotent_across_calls(self, tmp_path: Path) -> None:
        mgr = LayoutManager(base_dir=tmp_path)
        first = mgr.ensure_default()
        second = mgr.ensure_default()
        assert first == second
        assert mgr.list_layouts() == ["Default"]
        assert len(list(tmp_path.glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# Cross-instance persistence
# ---------------------------------------------------------------------------


class TestCrossInstancePersistence:
    def test_two_managers_share_directory(self, tmp_path: Path) -> None:
        writer = LayoutManager(base_dir=tmp_path)
        reader = LayoutManager(base_dir=tmp_path)
        writer.save(_make_layout("Shared"))
        assert reader.list_layouts() == ["Shared"]
        assert reader.exists("Shared") is True
        assert reader.load("Shared").name == "Shared"

    def test_layout_survives_reinstantiation(self, tmp_path: Path) -> None:
        original = _rich_layout("Persistent")
        LayoutManager(base_dir=tmp_path).save(original)
        reloaded = LayoutManager(base_dir=tmp_path).load("Persistent")
        assert reloaded == original


# ---------------------------------------------------------------------------
# default_layout()
# ---------------------------------------------------------------------------


class TestDefaultLayout:
    def test_name_sections_and_configs(self) -> None:
        layout = default_layout()
        assert isinstance(layout, LayoutModel)
        assert layout.name == "Default"
        assert len(layout.show_header) > 0
        assert len(layout.recording_body) > 0
        assert layout.separator == "-"  # Bug F.2: ASCII default, not U+2500
        assert layout.page_config == PageConfig()
        assert layout.screenshot_config == ScreenshotConfig()

    def test_show_header_first_line_is_archival_bold_16(self) -> None:
        element = default_layout().show_header[0].elements[0]
        assert element.kind == "field"
        assert element.content == "Show"
        assert element.field_type == FieldType.ARCHIVAL
        assert element.style is not None
        assert element.style.bold is True
        assert element.style.font_size == 16

    def test_field_types_match_registry(self) -> None:
        layout = default_layout()
        for line in layout.show_header + layout.recording_body:
            for element in line.elements:
                if element.kind == "field":
                    assert element.field_type is not None
                    if element.field_type is FieldType.TECHNICAL:
                        assert element.content in TECHNICAL_FIELDS
                    else:
                        assert (
                            element.content in ARCHIVAL_FIELDS
                            or element.content in SUGGESTION_FIELDS
                        )
                else:
                    assert element.field_type is None


# ---------------------------------------------------------------------------
# Independence
# ---------------------------------------------------------------------------


class TestIndependence:
    def test_no_pyside6_import(self) -> None:
        """Module must not import PySide6."""
        import bootleg_catalog.core.layout_manager as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "PySide6" not in content

    def test_no_sqlite_import(self) -> None:
        """Module must not import sqlite3."""
        import bootleg_catalog.core.layout_manager as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "sqlite3" not in content

    def test_no_catalog_import(self) -> None:
        """Module must not import Catalog or CatalogRepository."""
        import bootleg_catalog.core.layout_manager as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "from bootleg_catalog.core.catalog" not in content
        assert "from bootleg_catalog.database" not in content
        assert "import catalog" not in content
        assert "import CatalogRepository" not in content
