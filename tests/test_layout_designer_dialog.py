"""Tests for LayoutDesignerDialog (L11.2)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.core.field_registry import (
    ARCHIVAL_FIELDS,
    SUGGESTION_FIELDS,
    TECHNICAL_FIELDS,
)
from bootleg_catalog.core.field_resolver import ResolvedElement, ResolvedLine
from bootleg_catalog.core.layout_manager import LayoutManager, default_layout
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    FooterConfig,
    HeaderConfig,
    LayoutElement,
    LayoutLine,
    LayoutModel,
    LogoSlot,
    PageConfig,
    ScreenshotConfig,
    TextSlot,
)
from bootleg_catalog.models.resolved_export import (
    ResolvedExportDocument,
    ResolvedRecording,
    ResolvedShowSection,
)
from bootleg_catalog.ui.dialogs.header_footer_dialog import HeaderFooterDialog
from bootleg_catalog.ui.dialogs.layout_designer_dialog import LayoutDesignerDialog
from bootleg_catalog.ui.widgets.export_preview_widget import PreviewWidget

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)


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


def _make_layout(**overrides) -> LayoutModel:
    from bootleg_catalog.models.layout_model import FieldType
    defaults = dict(
        name="Test Layout",
        show_header=[
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Show", field_type=FieldType.ARCHIVAL),
            ]),
        ],
        recording_body=[
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Duration", field_type=FieldType.TECHNICAL),
            ]),
        ],
    )
    defaults.update(overrides)
    return LayoutModel(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLayoutDesignerDialog:
    def test_opens_with_layout(self, qt_app) -> None:
        """Dialog opens and editors contain the formatted layout text."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg.show()
        assert dlg.windowTitle() == "Layout Designer"
        # Name field contains layout name
        assert dlg._name_edit.text() == "Test Layout"

    def test_editors_contain_formatted_text(self, qt_app) -> None:
        """Editors contain the formatted text of show_header and recording_body."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        # Show header editor should contain the formatted header
        header_text = dlg._show_header_edit.toPlainText()
        assert "[Show]" in header_text
        # Recording body editor should contain the formatted body
        body_text = dlg._recording_body_edit.toPlainText()
        assert "{Duration}" in body_text

    def test_change_name_and_save(self, qt_app) -> None:
        """Changing the name and Save → result_layout.name matches."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg._name_edit.setText("New Name")
        dlg._on_save()
        assert dlg.result_layout is not None
        assert dlg.result_layout.name == "New Name"

    def test_result_show_header_reflects_editors(self, qt_app) -> None:
        """result_layout.show_header reflects the parsed editor content."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg._show_header_edit.setPlainText("[Title] by [Artist]")
        dlg._on_save()
        assert dlg.result_layout is not None
        assert len(dlg.result_layout.show_header) == 1
        elems = dlg.result_layout.show_header[0].elements
        assert len(elems) == 3
        assert elems[0].content == "Title"
        assert elems[1].content == " by "
        assert elems[2].content == "Artist"

    def test_result_recording_body_reflects_editors(self, qt_app) -> None:
        """result_layout.recording_body reflects the parsed editor content."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg._recording_body_edit.setPlainText("{Duration} - {Resolution}")
        dlg._on_save()
        assert dlg.result_layout is not None
        assert len(dlg.result_layout.recording_body) == 1
        elems = dlg.result_layout.recording_body[0].elements
        assert len(elems) == 3
        assert elems[0].content == "Duration"
        assert elems[2].content == "Resolution"

    def test_cancel_returns_none(self, qt_app) -> None:
        """Cancel → result_layout is None."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg.reject()
        assert dlg.result_layout is None

    def test_input_layout_not_modified_on_save(self, qt_app) -> None:
        """Input layout is not mutated after Save."""
        layout = _make_layout()
        original_name = layout.name
        original_header = [line.elements[0].content for line in layout.show_header]
        dlg = LayoutDesignerDialog(layout)
        dlg._name_edit.setText("Changed")
        dlg._show_header_edit.setPlainText("[New Header]")
        dlg._on_save()
        assert layout.name == original_name
        assert [line.elements[0].content for line in layout.show_header] == original_header

    def test_input_layout_not_modified_on_cancel(self, qt_app) -> None:
        """Input layout is not mutated after Cancel."""
        layout = _make_layout()
        original_name = layout.name
        dlg = LayoutDesignerDialog(layout)
        dlg._name_edit.setText("Changed")
        dlg.reject()
        assert layout.name == original_name

    def test_empty_name_shows_warning(self, qt_app) -> None:
        """Empty name on Save → warning shown, result_layout is None."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg._name_edit.setText("")
        with patch("bootleg_catalog.ui.dialogs.layout_designer_dialog.QMessageBox.warning"):
            dlg._on_save()
        assert dlg.result_layout is None

    def test_whitespace_only_name_shows_warning(self, qt_app) -> None:
        """Whitespace-only name on Save → warning, result_layout is None."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg._name_edit.setText("   ")
        with patch("bootleg_catalog.ui.dialogs.layout_designer_dialog.QMessageBox.warning"):
            dlg._on_save()
        assert dlg.result_layout is None

    def test_technical_palette_inserts_field(self, qt_app) -> None:
        """Double-clicking a technical palette item inserts {Name}."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg._show_header_edit.setFocus()
        dlg._last_focused = dlg._show_header_edit
        # Simulate inserting a technical field
        dlg._insert_field("Duration", technical=True)
        text = dlg._show_header_edit.toPlainText()
        assert "{Duration}" in text

    def test_archival_palette_inserts_field(self, qt_app) -> None:
        """Double-clicking an archival palette item inserts [Name]."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg._recording_body_edit.setFocus()
        dlg._last_focused = dlg._recording_body_edit
        dlg._insert_field("Show", technical=False)
        text = dlg._recording_body_edit.toPlainText()
        assert "[Show]" in text

    def test_palette_lists_all_technical_fields(self, qt_app) -> None:
        """Palette lists every technical field from TECHNICAL_FIELDS."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        palette_items = [
            dlg._tech_list.item(i).text()
            for i in range(dlg._tech_list.count())
        ]
        for name in TECHNICAL_FIELDS:
            assert name in palette_items

    def test_palette_lists_all_archival_fields(self, qt_app) -> None:
        """Palette lists every archival + suggestion field."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        palette_items = [
            dlg._arch_list.item(i).text()
            for i in range(dlg._arch_list.count())
        ]
        for name in ARCHIVAL_FIELDS:
            assert name in palette_items
        for name in SUGGESTION_FIELDS:
            assert name in palette_items

    def test_text_change_schedules_debounce(self, qt_app) -> None:
        """Editing either editor schedules the debounced preview update."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)

        # Timer should not be running before edits
        assert not dlg._preview_timer.isActive()

        dlg._show_header_edit.setPlainText("[New Header]")
        assert dlg._preview_timer.isActive()

        # Also verify the recording-body editor schedules the timer
        dlg._preview_timer.stop()
        assert not dlg._preview_timer.isActive()
        dlg._recording_body_edit.setPlainText("{Duration} {Resolution}")
        assert dlg._preview_timer.isActive()

    def test_debounce_timeout_updates_preview(self, qt_app) -> None:
        """The armed debounce timer's timeout signal drives the preview update."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)

        with patch.object(dlg._preview_widget, "set_document") as mock_set:
            mock_set.reset_mock()
            dlg._show_header_edit.setPlainText("[Another Header]")
            # The textChanged signal armed the 200 ms single-shot timer
            assert dlg._preview_timer.isActive()
            assert dlg._preview_timer.isSingleShot()
            assert dlg._preview_timer.interval() == 200

            # Let the real event loop dispatch the timer's timeout signal
            QTest.qWait(350)
            assert not dlg._preview_timer.isActive()

        # timeout -> _update_preview -> set_document wiring is intact
        assert mock_set.called

    def test_preview_uses_sample_recordings(self, qt_app) -> None:
        """Preview uses the provided sample_recordings."""
        from bootleg_catalog.models.export_recording import ExportRecording
        rec = ExportRecording(
            source_path="C:/test.mkv",
            extension=".mkv",
            show="My Show",
            duration=100.0,
            screenshot_paths=[],
        )
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout, sample_recordings=[rec])
        with patch.object(dlg._preview_widget, "set_document") as mock_set:
            dlg._update_preview()
            doc = mock_set.call_args[0][0]
            total = sum(len(s.recordings) for s in doc.show_sections)
            assert total == 1

    def test_preview_uses_builtin_sample_when_none(self, qt_app) -> None:
        """Preview uses built-in sample when sample_recordings=None."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout, sample_recordings=None)
        with patch.object(dlg._preview_widget, "set_document") as mock_set:
            dlg._update_preview()
            doc = mock_set.call_args[0][0]
            total = sum(len(s.recordings) for s in doc.show_sections)
            assert total == 2  # Built-in sample has 2 recordings

    def test_invalid_syntax_shows_error_but_preview_still_renders(self, qt_app) -> None:
        """Bad field syntax shows an inline error but the preview still renders."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg.show()

        # Sanity: no error before the bad edit
        assert not dlg._error_label.isVisible()

        # Make a bad edit that the resolver will flag:
        # {Show} uses technical syntax for an archival field
        dlg._show_header_edit.setPlainText("{Show}")

        with patch.object(dlg._preview_widget, "set_document") as mock_set:
            dlg._update_preview()

        # (a) The preview still rendered (did not crash)
        assert mock_set.called

        # (b) The error label is now visible with a meaningful message
        assert dlg._error_label.isVisible()
        assert "Show" in dlg._error_label.text()

        # (c) The dialog is still usable
        assert dlg._save_btn.isEnabled()

    def test_valid_layout_hides_error_label(self, qt_app) -> None:
        """A valid layout leaves the inline error label hidden."""
        layout = _make_layout()  # already valid
        dlg = LayoutDesignerDialog(layout)
        dlg.show()
        dlg._update_preview()
        assert not dlg._error_label.isVisible()

    def test_independence_no_catalog_import(self) -> None:
        """Module must not import Catalog, CatalogRepository, or sqlite3."""
        import bootleg_catalog.ui.dialogs.layout_designer_dialog as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "from bootleg_catalog.core.catalog" not in content
        assert "from bootleg_catalog.database" not in content
        assert "import catalog" not in content
        assert "import CatalogRepository" not in content
        assert "sqlite3" not in content

    def test_result_preserves_page_config(self, qt_app) -> None:
        """result_layout preserves original page_config and screenshot_config."""
        layout = _make_layout()
        dlg = LayoutDesignerDialog(layout)
        dlg._on_save()
        assert dlg.result_layout is not None
        assert dlg.result_layout.page_config == layout.page_config
        assert dlg.result_layout.screenshot_config == layout.screenshot_config
        assert dlg.result_layout.separator == layout.separator

    def test_opens_maximized(self, qt_app) -> None:
        """The designer opens maximized for a legible preview (L15.7b.2)."""
        dlg = LayoutDesignerDialog(_make_layout())
        dlg.show()
        assert dlg.isMaximized()


# ---------------------------------------------------------------------------
# Page metaphor + near-chip tokens (L15.7a.2)
# ---------------------------------------------------------------------------

class TestPageStyling:
    """The editors read as one document page, not two text areas."""

    def test_editors_share_one_page_frame(self, qt_app) -> None:
        """Both editors sit inside the same 'layoutPage' frame."""
        dlg = LayoutDesignerDialog(_make_layout())

        parent = dlg._show_header_edit.parentWidget()
        assert parent is dlg._recording_body_edit.parentWidget()
        assert parent.objectName() == "layoutPage"
        assert parent is dlg._page

    def test_page_frame_has_drop_shadow(self, qt_app) -> None:
        """The page floats on the desk via a QGraphicsDropShadowEffect."""
        dlg = LayoutDesignerDialog(_make_layout())

        effect = dlg._page.graphicsEffect()
        assert isinstance(effect, QGraphicsDropShadowEffect)
        assert effect.blurRadius() == 24

    def test_technical_token_gets_background_format(self, qt_app) -> None:
        """{Duration} is highlighted with a non-empty background brush.

        Stability choice: assert on the *applied* layout formats of the
        document (QTextLayout.FormatRange) after rehighlight() — the
        visible result — rather than poking at the highlighter's rule
        table, whose shape is an implementation detail.
        """
        dlg = LayoutDesignerDialog(_make_layout())
        dlg._show_header_edit.setPlainText("{Duration}")
        dlg._show_header_hl.rehighlight()

        token = "{Duration}"
        block_layout = dlg._show_header_edit.document().firstBlock().layout()
        # NB: extract the plain style enum per item — keeping QTextCharFormat
        # copies alive past the formats() temporary trips shiboken's
        # "C++ object already deleted" guard.
        covering_styles = [
            fr.format.background().style()
            for fr in block_layout.formats()
            if fr.start == 0 and fr.length == len(token)
        ]
        assert covering_styles, "no highlight range applied to {Duration}"
        assert any(style != Qt.BrushStyle.NoBrush for style in covering_styles)

    def test_preview_widget_has_page_frame(self, qt_app) -> None:
        """The embedded preview is the page-based PreviewWidget (L15.7b)."""
        dlg = LayoutDesignerDialog(_make_layout())

        assert isinstance(dlg._preview_widget, PreviewWidget)
        assert dlg._preview_widget._page is not None
        assert dlg._preview_widget._page.objectName() == "previewPage"


# ---------------------------------------------------------------------------
# Field-reference validation (L15.8.3)
# ---------------------------------------------------------------------------

class TestFieldValidation:
    def test_valid_archival_token_has_no_issues(self, qt_app) -> None:
        dlg = LayoutDesignerDialog(_make_layout())
        dlg._show_header_edit.setPlainText("[Show]")

        assert dlg._current_validation_issues() == []

    def test_wrong_category_token_reported(self, qt_app) -> None:
        dlg = LayoutDesignerDialog(_make_layout())
        dlg._show_header_edit.setPlainText("{Show}")

        assert dlg._current_validation_issues() == [
            ("{Show}", "wrong_category"),
        ]

    def test_unknown_token_reported(self, qt_app) -> None:
        dlg = LayoutDesignerDialog(_make_layout())
        dlg._show_header_edit.setPlainText("{Nonexistent}")

        assert dlg._current_validation_issues() == [
            ("{Nonexistent}", "unknown"),
        ]

    def test_only_invalid_tokens_count(self, qt_app) -> None:
        """Valid archival and technical tokens never surface as issues."""
        dlg = LayoutDesignerDialog(_make_layout())
        dlg._show_header_edit.setPlainText("[Show]\n{Fake}\n{Duration}")

        assert dlg._current_validation_issues() == [("{Fake}", "unknown")]

    def test_status_label_pluralization(self, qt_app) -> None:
        dlg = LayoutDesignerDialog(_make_layout())
        dlg._show_header_edit.setPlainText("{Show}")

        dlg._update_validation()
        assert dlg._validation_label.text() == "1 field reference issue"

        dlg._show_header_edit.setPlainText("{Show}\n{Fake}")
        dlg._update_validation()
        assert dlg._validation_label.text() == "2 field reference issues"

    def test_status_label_hidden_without_issues(self, qt_app) -> None:
        dlg = LayoutDesignerDialog(_make_layout())
        dlg._show_header_edit.setPlainText("{Show}")
        dlg._update_validation()
        assert not dlg._validation_label.isHidden()

        dlg._show_header_edit.setPlainText("[Show]")
        dlg._update_validation()
        assert dlg._validation_label.isHidden()


# ---------------------------------------------------------------------------
# Bold/italic markers (L15.8.4)
# ---------------------------------------------------------------------------

class TestBoldItalicPreview:
    def _preview_labels(self, dlg) -> list[QLabel]:
        return dlg._preview_widget.findChildren(QLabel)

    def test_bold_marker_reaches_preview(self, qt_app) -> None:
        """`**bold**` renders a rich-text QLabel wrapped in <b>."""
        dlg = LayoutDesignerDialog(_make_layout())
        dlg._recording_body_edit.setPlainText("**bold**")
        dlg._update_preview()

        labels = [
            lb for lb in self._preview_labels(dlg)
            if lb.textFormat() == Qt.TextFormat.RichText and "<b>bold</b>" in lb.text()
        ]
        assert labels, "expected a preview QLabel carrying <b>bold</b>"

    def test_italic_marker_reaches_preview(self, qt_app) -> None:
        """`*it*` renders a rich-text QLabel wrapped in <i>."""
        dlg = LayoutDesignerDialog(_make_layout())
        dlg._recording_body_edit.setPlainText("*it*")
        dlg._update_preview()

        labels = [
            lb for lb in self._preview_labels(dlg)
            if lb.textFormat() == Qt.TextFormat.RichText and "<i>it</i>" in lb.text()
        ]
        assert labels, "expected a preview QLabel carrying <i>it</i>"


class TestMarkerHighlighting:
    def test_asterisk_markers_painted_grey(self, qt_app) -> None:
        """`**`/`*` markers get the grey (no-background) control format."""
        dlg = LayoutDesignerDialog(_make_layout())
        dlg._show_header_edit.setPlainText("**bold**")
        dlg._show_header_hl.rehighlight()

        block_layout = dlg._show_header_edit.document().firstBlock().layout()
        # Eager extraction — QTextCharFormat copies must outlive the
        # formats() temporary (shiboken lifetime guard).
        marker_styles = [
            (
                fr.format.foreground().color().name(),
                fr.format.background().style(),
            )
            for fr in block_layout.formats()
            if fr.start == 0 and fr.length == 2
        ]
        assert marker_styles, "no highlight range applied to the leading **"
        grey_no_bg = [
            (fg, bg) for fg, bg in marker_styles
            if fg.lower() == "#6b7280" and bg == Qt.BrushStyle.NoBrush
        ]
        assert grey_no_bg, f"expected grey/no-bg marker format, got {marker_styles}"


# ---------------------------------------------------------------------------
# Preview auto-zoom fallback (L15.7b.2)
# ---------------------------------------------------------------------------

def _make_preview_document() -> ResolvedExportDocument:
    """A minimal non-empty document for the auto-zoom tests."""
    recording = ExportRecording(source_path="C:/media/concert.mkv")
    resolved = ResolvedRecording(
        recording=recording,
        lines=[ResolvedLine(elements=[ResolvedElement(text="Test Show - 120 min")])],
    )
    section = ResolvedShowSection(
        show_name="Test Show", header_lines=[], recordings=[resolved]
    )
    return ResolvedExportDocument(
        layout=_make_layout(), show_sections=[section], separator=None
    )


class TestPreviewAutoZoom:
    """L15.7b.2: narrow panels fall back to a legible 100% default."""

    def test_auto_zoom_fallback_only_when_enabled(self, qt_app) -> None:
        """auto_zoom_fallback=True drops Fit Width; the default keeps it.

        The widgets are resized narrow before their first set_document:
        a not-yet-laid-out preview has a tiny internal scroll viewport
        (the same situation as the designer's first update during
        __init__), where Fit Width would render below 0.5x.
        """
        narrow = PreviewWidget(auto_zoom_fallback=True)
        narrow.resize(380, 500)
        narrow.set_document(_make_preview_document())
        assert narrow._zoom_combo.currentText() == "100%"

        default = PreviewWidget()
        default.resize(380, 500)
        default.set_document(_make_preview_document())
        assert default._zoom_combo.currentText() == "Fit Width"


# ---------------------------------------------------------------------------
# Page Setup dialog (L15.7c)
# ---------------------------------------------------------------------------

class TestPageSetup:
    """The designer surfaces page size, orientation, and margins."""

    def test_page_setup_button_opens_dialog(self, qt_app) -> None:
        """The 'Page Setup…' button opens the PageSetupDialog."""
        dlg = LayoutDesignerDialog(_make_layout())

        assert dlg._page_setup_btn.text() == "Page Setup\u2026"
        with patch(
            "bootleg_catalog.ui.dialogs.layout_designer_dialog.PageSetupDialog"
        ) as mock_dialog:
            dlg._page_setup_btn.click()
        mock_dialog.assert_called_once()

    def test_page_setup_accept_updates_preview_geometry(self, qt_app) -> None:
        """Accepting Letter landscape updates the preview's page geometry."""
        dlg = LayoutDesignerDialog(_make_layout())
        # Deterministic scale: force 100% zoom before the geometry change.
        dlg._preview_widget._zoom_combo.setCurrentText("100%")

        letter_landscape = PageConfig(page_size="Letter", orientation="landscape")
        with patch(
            "bootleg_catalog.ui.dialogs.layout_designer_dialog.PageSetupDialog"
        ) as mock_dialog:
            mock_dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            mock_dialog.return_value.result_page_config = letter_landscape
            dlg._on_page_setup()

        assert dlg._preview_widget._page_config == letter_landscape
        # Letter landscape = 279 x 216 mm -> 1054 px wide at 96 DPI, 100% zoom.
        assert dlg._preview_widget._page.width() == 1054


# ---------------------------------------------------------------------------
# Page setup screenshots group (L15.8.5)
# ---------------------------------------------------------------------------

class TestPageSetupScreenshots:
    """PageSetupDialog edits the ScreenshotConfig for [Screenshot:N]."""

    def test_page_setup_shows_screenshot_controls(self, qt_app) -> None:
        """The constructor exposes the Screenshots group's controls."""
        from bootleg_catalog.ui.dialogs.layout_designer_dialog import PageSetupDialog

        dialog = PageSetupDialog(PageConfig())

        assert dialog.findChild(QSpinBox, "shotMaxWidthSpin") is not None
        assert dialog.findChild(QSpinBox, "shotMaxHeightSpin") is not None
        assert dialog.findChild(QComboBox, "shotLayoutCombo") is not None
        assert dialog.findChild(QSpinBox, "shotPhotosPerRowSpin") is not None

    def test_accept_updates_screenshot_config_and_preview(self, qt_app) -> None:
        """Accepting a new max width lands in the layout and the preview."""
        dlg = LayoutDesignerDialog(_make_layout())
        # Deterministic scale: force 100% zoom before the rebuild.
        dlg._preview_widget._zoom_combo.setCurrentText("100%")

        with patch(
            "bootleg_catalog.ui.dialogs.layout_designer_dialog.PageSetupDialog"
        ) as mock_dialog:
            mock_dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            mock_dialog.return_value.result_page_config = PageConfig()
            mock_dialog.return_value.result_screenshot_config = (
                ScreenshotConfig(max_width_px=420)
            )
            dlg._on_page_setup()

        assert dlg._current_screenshot_config.max_width_px == 420
        # The preview only re-reads the sizing through a rebuilt document.
        preview_doc = dlg._preview_widget._document
        assert preview_doc.layout.screenshot_config.max_width_px == 420
        # ...and Save carries it into the resulting layout.
        dlg._on_save()
        assert dlg.result_layout.screenshot_config.max_width_px == 420


# ---------------------------------------------------------------------------
# Inline formatting toolbar (L15.8.6a)
# ---------------------------------------------------------------------------

class TestFormattingToolbar:
    """The bar above the page wraps the focused editor's selection."""

    @staticmethod
    def _select(dlg, editor, text: str) -> None:
        """Put *text* in *editor*, claim focus for it, select it all."""
        editor.setPlainText(text)
        dlg._last_focused = editor
        cursor = editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(len(text), QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        dlg._update_toolbar_state()

    def test_toolbar_buttons_exist_and_disabled_without_selection(
        self, qt_app
    ) -> None:
        """Every button exists, and none is enabled with no selection."""
        dlg = LayoutDesignerDialog(_make_layout())

        for name in (
            "boldButton",
            "italicButton",
            "highlightButton",
            "textColourButton",
            "clearFormatButton",
        ):
            btn = dlg.findChild(QPushButton, name)
            assert btn is not None, name
            assert not btn.isEnabled(), name

    def test_bold_wraps_selection(self, qt_app) -> None:
        """B on a plain selection wraps it in **...**."""
        dlg = LayoutDesignerDialog(_make_layout())
        editor = dlg._recording_body_edit
        self._select(dlg, editor, "hello")
        assert dlg._bold_btn.isEnabled()

        dlg._bold_btn.click()

        assert editor.toPlainText() == "**hello**"

    def test_bold_toggles_off(self, qt_app) -> None:
        """B again on the same selection removes the markers."""
        dlg = LayoutDesignerDialog(_make_layout())
        editor = dlg._recording_body_edit
        self._select(dlg, editor, "hello")
        dlg._bold_btn.click()
        assert editor.toPlainText() == "**hello**"
        # The selection was restored over the inner text, so the
        # button can toggle back off.
        assert editor.textCursor().selectedText() == "hello"

        dlg._bold_btn.click()

        assert editor.toPlainText() == "hello"

    def test_highlight_wraps_selection(self, qt_app) -> None:
        """Highlight wraps a plain selection in ==...==."""
        dlg = LayoutDesignerDialog(_make_layout())
        editor = dlg._recording_body_edit
        self._select(dlg, editor, "hello")

        dlg._highlight_btn.click()

        assert editor.toPlainText() == "==hello=="


# ---------------------------------------------------------------------------
# Header & Footer dialog (L15.7e.1)
# ---------------------------------------------------------------------------

class TestHeaderFooter:
    """The designer surfaces header/footer config via a modal dialog."""

    def test_header_footer_button_in_top_row(self, qt_app) -> None:
        """The 'Header && Footer…' button sits in the top toolbar row."""
        dlg = LayoutDesignerDialog(_make_layout())
        btn = dlg._header_footer_btn
        assert btn.objectName() == "headerFooterButton"
        assert btn.text() == "Header && Footer\u2026"

        # It shares the first row with the name field and Page Setup button.
        main = dlg.layout()
        rows = []
        for i in range(main.count()):
            sub = main.itemAt(i).layout()
            if sub is not None:
                rows.append(sub)
        row = next(r for r in rows if r.indexOf(btn) >= 0)
        assert row.indexOf(dlg._page_setup_btn) >= 0
        assert row.indexOf(dlg._name_edit) >= 0

    def test_button_opens_dialog_with_current_state(self, qt_app) -> None:
        """Clicking the button constructs HeaderFooterDialog(None, None, dlg, library_mode=False)."""
        dlg = LayoutDesignerDialog(_make_layout())
        with patch(
            "bootleg_catalog.ui.dialogs.layout_designer_dialog.HeaderFooterDialog"
        ) as mock_dialog:
            mock_dialog.return_value.exec.return_value = (
                QDialog.DialogCode.Rejected
            )
            dlg._header_footer_btn.click()
        mock_dialog.assert_called_once_with(None, None, dlg, library_mode=False)
        assert dlg._current_header is None
        assert dlg._current_footer is None

    def test_accept_updates_state_and_refreshes_preview(self, qt_app) -> None:
        """Accepting a center-text header stores it and refreshes the preview."""
        dlg = LayoutDesignerDialog(_make_layout())
        expected = HeaderConfig(
            enabled=True, scope="all", center=TextSlot(text="Test [Year]")
        )
        with patch(
            "bootleg_catalog.ui.dialogs.layout_designer_dialog.HeaderFooterDialog"
        ) as mock_dialog, patch.object(
            dlg._preview_widget, "set_document"
        ) as mock_set:
            mock_dialog.return_value.exec.return_value = (
                QDialog.DialogCode.Accepted
            )
            mock_dialog.return_value.result_header = expected
            mock_dialog.return_value.result_footer = None
            dlg._on_edit_header_footer()

        assert dlg._current_header == expected
        assert dlg._current_footer is None
        mock_set.assert_called_once()
        # The layout handed to the resolver carries the new header.
        assert mock_set.call_args[0][0].layout.header == expected

    def test_disabled_header_stays_none_through_save(self, qt_app) -> None:
        """A disabled header yields None, which survives wiring and Save."""
        dlg = LayoutDesignerDialog(_make_layout())

        # Real dialog: type text but leave "Enabled" unchecked.
        hf = HeaderFooterDialog(None, None)
        hf._header_side.edits["center"].setText("Should not survive")
        hf._on_accept()
        assert hf.result_header is None

        with patch(
            "bootleg_catalog.ui.dialogs.layout_designer_dialog.HeaderFooterDialog",
            return_value=hf,
        ), patch.object(
            hf, "exec", return_value=QDialog.DialogCode.Accepted
        ):
            dlg._on_edit_header_footer()
        assert dlg._current_header is None

        dlg._on_save()
        assert dlg.result_layout is not None
        assert dlg.result_layout.header is None

    def test_save_carries_header_footer_state(self, qt_app) -> None:
        """_on_save builds a LayoutModel whose header/footer match the state."""
        dlg = LayoutDesignerDialog(_make_layout())
        header = HeaderConfig(
            enabled=True, scope="first", left=TextSlot(text="Saved Header")
        )
        footer = FooterConfig(enabled=True, right=TextSlot(text="Saved Footer"))
        dlg._current_header = header
        dlg._current_footer = footer

        dlg._on_save()

        assert dlg.result_layout is not None
        assert dlg.result_layout.header == header
        assert dlg.result_layout.footer == footer

    def test_empty_slot_line_maps_to_none(self, qt_app) -> None:
        """An empty line edit yields a None slot, never an empty TextSlot."""
        hf = HeaderFooterDialog(None, None)
        hf._header_side.enabled.setChecked(True)
        hf._header_side.kinds["left"].setCurrentIndex(1)     # "Text", empty
        hf._header_side.kinds["center"].setCurrentIndex(1)   # "Text"
        hf._header_side.edits["center"].setText("Test [Year]")
        # Left is "Text" with an empty line; right stays "None"; the
        # footer stays disabled.
        hf._on_accept()

        header = hf.result_header
        assert header is not None
        assert header.enabled is True
        assert header.scope == "all"
        assert header.left is None
        assert header.right is None
        assert header.center == TextSlot(text="Test [Year]", font_size=10, bold=False)
        assert hf.result_footer is None


# ---------------------------------------------------------------------------
# Library-only logos (L15.7f.2)
# ---------------------------------------------------------------------------


class TestLibraryLogoMode:
    """library_mode is threaded by the caller, never inferred."""

    def test_non_library_designer_saves_pending_logos(
        self, qt_app, tmp_path: Path
    ) -> None:
        """A library_mode=False designer persists chosen logos too (L15.7f.4)."""
        png = tmp_path / "chosen.png"
        png.write_bytes(b"\x89PNG-fake-bytes")
        dlg = LayoutDesignerDialog(default_layout())   # library_mode=False

        # Choose an image through a real Header & Footer dialog.
        hf = HeaderFooterDialog(dlg._current_header, dlg._current_footer, dlg)
        hf._header_side.enabled.setChecked(True)
        hf._header_side.kinds["center"].setCurrentIndex(2)   # "Logo"
        with patch.object(
            QFileDialog,
            "getOpenFileName",
            return_value=(str(png), "Images (*.png *.jpg *.jpeg *.webp)"),
        ):
            hf._header_side.logo_buttons["center"].click()
        hf._on_accept()
        assert "header.center" in hf.result_pending_logos

        with patch(
            "bootleg_catalog.ui.dialogs.layout_designer_dialog.HeaderFooterDialog",
            return_value=hf,
        ), patch.object(
            hf, "exec", return_value=QDialog.DialogCode.Accepted
        ):
            dlg._on_edit_header_footer()
        assert "header.center" in dlg._pending_logos

        with patch.object(LayoutManager, "save") as mock_save:
            dlg._on_save()

        mock_save.assert_called_once()
        kwargs = mock_save.call_args.kwargs
        assert "header.center" in kwargs["pending_logos"]
        assert kwargs["pending_logos"]["header.center"] == Path(png).absolute()

    def test_library_mode_passes_pending_logos_to_save(
        self, qt_app, tmp_path: Path
    ) -> None:
        """A chosen logo reaches LayoutManager.save(pending_logos=...)."""
        png = tmp_path / "chosen.png"
        png.write_bytes(b"\x89PNG-fake-bytes")
        dlg = LayoutDesignerDialog(default_layout(), library_mode=True)

        # Choose an image through a real Header & Footer dialog.
        hf = HeaderFooterDialog(
            dlg._current_header, dlg._current_footer, dlg, library_mode=True
        )
        hf._header_side.enabled.setChecked(True)
        hf._header_side.kinds["center"].setCurrentIndex(2)   # "Logo"
        with patch.object(
            QFileDialog,
            "getOpenFileName",
            return_value=(str(png), "Images (*.png *.jpg *.jpeg *.webp)"),
        ):
            hf._header_side.logo_buttons["center"].click()
        hf._on_accept()
        assert "header.center" in hf.result_pending_logos

        with patch(
            "bootleg_catalog.ui.dialogs.layout_designer_dialog.HeaderFooterDialog",
            return_value=hf,
        ), patch.object(
            hf, "exec", return_value=QDialog.DialogCode.Accepted
        ):
            dlg._on_edit_header_footer()
        assert "header.center" in dlg._pending_logos

        # The mock models save()'s contract (L15.7f.5): it returns the
        # layout it stored, which the designer then adopts.
        with patch.object(
            LayoutManager, "save", side_effect=lambda layout, **kwargs: layout
        ) as mock_save:
            dlg._on_save()

        mock_save.assert_called_once()
        kwargs = mock_save.call_args.kwargs
        assert "header.center" in kwargs["pending_logos"]
        assert kwargs["pending_logos"]["header.center"] == Path(png).absolute()
        assert dlg._pending_logos == {}       # consumed by the save
        assert dlg.result_layout is not None
        center = dlg.result_layout.header.center
        assert isinstance(center, LogoSlot)
        assert center.logo_file is None       # filled in by LayoutManager.save

    def test_result_layout_carries_retargeted_logo_file(
        self, qt_app, tmp_path: Path
    ) -> None:
        """The returned layout carries the copied logo's filename (L15.7f.5)."""
        png = tmp_path / "chosen.png"
        png.write_bytes(b"\x89PNG-fake-bytes")
        dlg = LayoutDesignerDialog(default_layout())   # library_mode=False
        dlg._current_header = HeaderConfig(
            enabled=True,
            center=LogoSlot(width_mm=20, logo_file=None),
        )
        dlg._pending_logos["header.center"] = png

        with patch(
            "bootleg_catalog.ui.dialogs.layout_designer_dialog.LayoutManager",
            lambda: LayoutManager(base_dir=tmp_path),
        ):
            dlg._on_save()

        assert dlg.result_layout is not None
        center = dlg.result_layout.header.center
        assert isinstance(center, LogoSlot)
        assert center.logo_file is not None
        assert center.logo_file.endswith(".logo.header_center.png")

    def test_pending_logo_shows_in_preview_after_dialog(
        self, qt_app, tmp_path: Path
    ) -> None:
        """A pending logo source renders in the preview, not the placeholder."""
        from PIL import Image
        png = tmp_path / "chosen.png"
        Image.new("RGB", (100, 50), color=(200, 100, 50)).save(png, "PNG")
        dlg = LayoutDesignerDialog(default_layout())
        dlg._preview_widget._zoom_combo.setCurrentText("100%")
        header = HeaderConfig(
            enabled=True, center=LogoSlot(width_mm=20, logo_file=None)
        )

        with patch(
            "bootleg_catalog.ui.dialogs.layout_designer_dialog.HeaderFooterDialog"
        ) as mock_dialog:
            mock_dialog.return_value.exec.return_value = (
                QDialog.DialogCode.Accepted
            )
            mock_dialog.return_value.result_header = header
            mock_dialog.return_value.result_footer = None
            mock_dialog.return_value.result_pending_logos = {
                "header.center": Path(png).absolute(),
            }
            dlg._on_edit_header_footer()

        bands = dlg._preview_widget.findChildren(QWidget, "previewHeader")
        assert bands
        labels = [lbl.text() for lbl in bands[0].findChildren(QLabel)]
        assert "missing logo" not in labels
        pixmaps = [
            lbl.pixmap()
            for lbl in bands[0].findChildren(QLabel)
            if lbl.pixmap() is not None and not lbl.pixmap().isNull()
        ]
        assert pixmaps
