"""Tests for DivergenceResolutionDialog (L15.6.2a multi-part add flow)."""

from __future__ import annotations

import gc
import os

import pytest

from bootleg_catalog.core.multi_part import PartTech
from bootleg_catalog.ui.dialogs.divergence_resolution_dialog import (
    DivergenceResolutionDialog,
)

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QRadioButton,
)


# ---------------------------------------------------------------------------
# Fixtures (copied per test-file isolation rule — no cross-imports)
# ---------------------------------------------------------------------------

@pytest.fixture
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _drain_deferred_deletes(qt_app):
    """Flush queued Qt deferred deletes while live widgets are held.

    See test_export_preview_window for the full rationale (deferred-delete
    re-entrancy crash) and the gc.collect() standing fix for shutdown
    heap corruption.
    """
    yield
    live_widgets = list(qt_app.topLevelWidgets())
    if live_widgets:
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        live_widgets.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tech(**overrides) -> PartTech:
    defaults = dict(
        path="C:/rips/show/VIDEO_TS/VTS_01_1.VOB",
        duration=3600.0,
        file_size=4_000_000_000,
        video_width=1920,
        video_height=1080,
        video_codec="h264",
        audio_codec="aac",
        frame_rate=29.97,
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate=192000,
    )
    defaults.update(overrides)
    return PartTech(**defaults)


_PART_A = _tech()
_PART_B = _tech(
    path="C:/rips/show/VIDEO_TS/VTS_01_2.VOB",
    video_width=1280,
    video_height=720,
    video_codec="hevc",
    audio_codec="ac3",
)


def _radio_texts(dialog: DivergenceResolutionDialog) -> list[str]:
    return [r.text() for r in dialog.findChildren(QRadioButton)]


def _click(dialog: DivergenceResolutionDialog, button: QDialogButtonBox.StandardButton) -> None:
    box = dialog.findChild(QDialogButtonBox)
    assert box is not None
    box.button(button).click()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_constructor_shows_two_resolutions(qt_app) -> None:
    dialog = DivergenceResolutionDialog(_PART_A, _PART_B, frozenset({"resolution"}))

    texts = _radio_texts(dialog)
    assert "Part A: 1920×1080" in texts
    assert "Part B: 1280×720" in texts
    assert dialog.windowTitle() == "Resolve Differences"


def test_default_resolutions_are_part_a(qt_app) -> None:
    dialog = DivergenceResolutionDialog(
        _PART_A,
        _PART_B,
        frozenset({"resolution", "video_codec", "audio_codec"}),
    )

    _click(dialog, QDialogButtonBox.StandardButton.Ok)

    assert dialog.resolutions == {
        "resolution": 0,
        "video_codec": 0,
        "audio_codec": 0,
    }


def test_choosing_part_b_for_video_codec(qt_app) -> None:
    dialog = DivergenceResolutionDialog(_PART_A, _PART_B, frozenset({"video_codec"}))
    radio_a, radio_b = dialog._radios["video_codec"]
    assert radio_a.isChecked()  # default = part A
    radio_b.setChecked(True)

    _click(dialog, QDialogButtonBox.StandardButton.Ok)

    assert dialog.resolutions == {"video_codec": 1}


def test_cancel_gives_none(qt_app) -> None:
    dialog = DivergenceResolutionDialog(_PART_A, _PART_B, frozenset({"resolution"}))

    _click(dialog, QDialogButtonBox.StandardButton.Cancel)

    assert dialog.resolutions is None


def test_empty_divergent_raises(qt_app) -> None:
    with pytest.raises(ValueError):
        DivergenceResolutionDialog(_PART_A, _PART_B, frozenset())


def test_unknown_field_raises(qt_app) -> None:
    with pytest.raises(ValueError):
        DivergenceResolutionDialog(
            _PART_A, _PART_B, frozenset({"unknown_field"})
        )
