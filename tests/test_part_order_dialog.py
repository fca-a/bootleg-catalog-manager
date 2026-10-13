"""Tests for PartOrderDialog (L15.6.2a multi-part add flow)."""

from __future__ import annotations

import gc
import os

import pytest

from bootleg_catalog.ui.dialogs.part_order_dialog import PartOrderDialog

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QLabel,
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

_PATH_A = "C:/rips/show/VIDEO_TS/VTS_01_1.VOB"
_PATH_B = "C:/rips/show/VIDEO_TS/VTS_01_2.VOB"


def _radio_texts(dialog: PartOrderDialog) -> list[str]:
    return [r.text() for r in dialog.findChildren(QRadioButton)]


def _subtitle_texts(dialog: PartOrderDialog) -> list[str]:
    return [
        lbl.text()
        for lbl in dialog.findChildren(QLabel)
        if lbl.objectName() == "partSubtitle"
    ]


def _click(dialog: PartOrderDialog, button: QDialogButtonBox.StandardButton) -> None:
    box = dialog.findChild(QDialogButtonBox)
    assert box is not None
    box.button(button).click()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_constructor_shows_both_basenames(qt_app) -> None:
    dialog = PartOrderDialog((_PATH_A, _PATH_B))

    assert _radio_texts(dialog) == ["VTS_01_1.VOB", "VTS_01_2.VOB"]
    # Grey subtitles carry the full paths, one per option.
    assert _subtitle_texts(dialog) == [_PATH_A, _PATH_B]
    assert dialog.windowTitle() == "Choose the First Part"


def test_default_first_index_is_zero(qt_app) -> None:
    dialog = PartOrderDialog((_PATH_A, _PATH_B))

    assert dialog.first_index == 0

    _click(dialog, QDialogButtonBox.StandardButton.Ok)
    assert dialog.first_index == 0


def test_selecting_second_radio_then_ok_gives_one(qt_app) -> None:
    dialog = PartOrderDialog((_PATH_A, _PATH_B))
    radios = dialog.findChildren(QRadioButton)
    radios[1].setChecked(True)

    _click(dialog, QDialogButtonBox.StandardButton.Ok)

    assert dialog.first_index == 1


def test_cancel_gives_none(qt_app) -> None:
    dialog = PartOrderDialog((_PATH_A, _PATH_B))

    _click(dialog, QDialogButtonBox.StandardButton.Cancel)

    assert dialog.first_index is None


def test_wrong_tuple_size_raises(qt_app) -> None:
    with pytest.raises(ValueError):
        PartOrderDialog((_PATH_A,))
    with pytest.raises(ValueError):
        PartOrderDialog((_PATH_A, _PATH_B, _PATH_A))
