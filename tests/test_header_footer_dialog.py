"""Tests for HeaderFooterDialog (L15.7f.2) — None/Text/Logo slot switching.

The dialog itself is constructed in isolation (no designer around it);
each test runs against tmp_path for any image it writes.  The real
application data directory is never written to: pre-existing logo
thumbnails are only *read* from it, and no test feeds the dialog a
logo_file that resolves there.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.models.layout_model import (
    HeaderConfig,
    LogoSlot,
    TextSlot,
)
from bootleg_catalog.ui.dialogs.header_footer_dialog import HeaderFooterDialog

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _logo_entry_enabled(side, key: str) -> bool:
    """True while the "Logo" combo entry for that slot is selectable."""
    flags = side.kind_models[key].item(2).flags()
    return (flags & Qt.ItemFlag.ItemIsEnabled) == Qt.ItemFlag.ItemIsEnabled


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSlotKindSwitch:
    def test_text_slot_preselects_text_mode(self, qt_app) -> None:
        """A TextSlot in header.center selects "Text" and fills the widgets."""
        config = HeaderConfig(
            enabled=True,
            center=TextSlot(text="Bootleg [Year]", font_size=12, bold=True),
        )

        hf = HeaderFooterDialog(config, None, library_mode=True)

        combo = hf._header_side.kinds["center"]
        assert combo.currentText() == "Text"
        edit = hf._header_side.edits["center"]
        assert edit.text() == "Bootleg [Year]"
        assert hf._header_side.sizes["center"].value() == 12
        assert hf._header_side.bolds["center"].isChecked() is True
        # The logo widgets for that row stay hidden.
        assert hf._header_side.logo_buttons["center"].isHidden()
        assert hf._header_side.logo_widths["center"].isHidden()

    def test_logo_mode_shows_logo_widgets_hides_text(self, qt_app) -> None:
        """Switching a row to "Logo" swaps the widget sets."""
        hf = HeaderFooterDialog(None, None, library_mode=True)
        side = hf._header_side

        side.kinds["center"].setCurrentIndex(1)        # "Text"
        assert not side.edits["center"].isHidden()
        assert not side.sizes["center"].isHidden()
        assert side.logo_buttons["center"].isHidden()
        assert side.logo_widths["center"].isHidden()

        side.kinds["center"].setCurrentIndex(2)        # "Logo"

        assert not side.logo_buttons["center"].isHidden()
        assert side.logo_buttons["center"].text() == "Choose Image\u2026"
        assert not side.logo_widths["center"].isHidden()
        assert side.logo_widths["center"].value() == 20.0
        assert side.logo_widths["center"].minimum() == 1.0
        assert side.logo_widths["center"].maximum() == 100.0
        assert side.edits["center"].isHidden()
        assert side.sizes["center"].isHidden()
        assert side.bolds["center"].isHidden()

    def test_logo_entry_enabled_in_both_modes(self, qt_app) -> None:
        """The "Logo" combo entry is selectable in every mode (L15.7f.4)."""
        for library_mode in (True, False):
            hf = HeaderFooterDialog(None, None, library_mode=library_mode)
            for side in (hf._header_side, hf._footer_side):
                for key in ("left", "center", "right"):
                    assert _logo_entry_enabled(side, key)

    def test_choose_image_tracks_pending_logos(self, qt_app, tmp_path) -> None:
        """A chosen image lands in result_pending_logos; Cancel leaks none."""
        png = tmp_path / "logo.png"
        png.write_bytes(b"\x89PNG-fake-bytes")
        filter_str = "Images (*.png *.jpg *.jpeg *.webp)"

        hf = HeaderFooterDialog(None, None, library_mode=True)
        hf._header_side.enabled.setChecked(True)
        hf._header_side.kinds["center"].setCurrentIndex(2)    # "Logo"
        with patch.object(
            QFileDialog, "getOpenFileName", return_value=(str(png), filter_str)
        ):
            hf._header_side.logo_buttons["center"].click()

        assert hf.result_pending_logos == {"header.center": Path(png).absolute()}

        hf._on_accept()
        header = hf.result_header
        assert header is not None
        center = header.center
        assert isinstance(center, LogoSlot)
        assert center.width_mm == 20.0
        # logo_file is filled in later by LayoutManager.save().
        assert center.logo_file is None

        # Cancel: a chosen image never leaks out of the dialog.
        other = HeaderFooterDialog(None, None, library_mode=True)
        other._footer_side.enabled.setChecked(True)
        other._footer_side.kinds["left"].setCurrentIndex(2)   # "Logo"
        with patch.object(
            QFileDialog, "getOpenFileName", return_value=(str(png), filter_str)
        ):
            other._footer_side.logo_buttons["left"].click()
        assert other.result_pending_logos == {
            "footer.left": Path(png).absolute()
        }
        other.reject()
        assert other.result_pending_logos == {}
