"""Layout Designer dialog.

Interactive editor for building and editing a :class:`LayoutModel`
without touching JSON.  Uses the canonical layout parser/formatter,
the export preview widget, and the FieldRegistry.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFontComboBox,
    QGraphicsDropShadowEffect,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bootleg_catalog.core.field_registry import (
    ARCHIVAL_FIELDS,
    SUGGESTION_FIELDS,
    TECHNICAL_FIELDS,
)
from bootleg_catalog.core.layout_manager import LayoutManager
from bootleg_catalog.core.layout_resolver import LayoutResolver
from bootleg_catalog.core.layout_text import format_layout_lines, parse_layout_lines
from bootleg_catalog.core.show_grouper import group_by_show
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    LayoutModel,
    PageConfig,
    ScreenshotConfig,
    page_size_dimensions_mm,
)
from bootleg_catalog.ui.dialogs.header_footer_dialog import HeaderFooterDialog
from bootleg_catalog.ui.widgets.export_preview_widget import PreviewWidget
from bootleg_catalog.ui.widgets.layout_editor_view import (
    FIELD_TOKEN_RE,
    LayoutEditorView,
    classify_field_token,
    is_screenshot_token,
)


# ---------------------------------------------------------------------------
# Built-in sample recordings
# ---------------------------------------------------------------------------

def _make_sample_recordings() -> list[ExportRecording]:
    """Create two sample recordings for the live preview."""
    return [
        ExportRecording(
            source_path="C:/media/bootleg-show1.mkv",
            extension=".mkv",
            file_size=4_500_000_000,
            duration=7320.0,
            container_format="matroska,webm",
            video_codec="h264",
            video_width=1920,
            video_height=1080,
            frame_rate=29.97,
            audio_codec="aac",
            audio_channels=2,
            audio_sample_rate=48000,
            audio_bitrate=320000,
            title="Tokyo Night Live",
            year=2024,
            date="2024-03-15",
            venue="Nippon Budokan",
            location="Tokyo",
            country="Japan",
            tour="World Tour 2024",
            show="Tokyo 2024",
            source="Audience recording",
            type="Bootleg",
            quality="Very Good",
            master="No",
            notes="Excellent audience capture",
            screenshot_paths=[],
        ),
        ExportRecording(
            source_path="C:/media/bootleg-show2.mkv",
            extension=".mkv",
            file_size=3_200_000_000,
            duration=6480.0,
            container_format="matroska,webm",
            video_codec="h264",
            video_width=1280,
            video_height=720,
            frame_rate=25.0,
            audio_codec="mp3",
            audio_channels=2,
            audio_sample_rate=44100,
            audio_bitrate=256000,
            title="Oslo Acoustic Set",
            year=2024,
            date="2024-06-20",
            venue="Rockefeller",
            location="Oslo",
            country="Norway",
            tour="European Leg",
            show="Tokyo 2024",
            source="Soundboard",
            type="Soundboard",
            quality="Excellent",
            master="Yes",
            notes="Direct soundboard feed",
            screenshot_paths=[],
        ),
    ]


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class LayoutDesignerDialog(QDialog):
    """Modal dialog for editing a LayoutModel via plain text.

    Parameters
    ----------
    layout:
        The layout to edit.  Not mutated.
    parent:
        Parent widget.
    sample_recordings:
        Optional recordings for the live preview.
    library_mode:
        True when the layout lives in the library (opened via
        "Edit Layout…"); False for the startup "New Session" flow.
        The CALLER decides whether the accepted layout is saved to the
        library; it is never inferred from the layout name.
    """

    def __init__(
        self,
        layout: LayoutModel,
        parent: QWidget | None = None,
        *,
        sample_recordings: list[ExportRecording] | None = None,
        library_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Layout Designer")
        self.setMinimumSize(1100, 700)
        self.setModal(True)

        self._input_layout = layout
        self._current_page_config = replace(layout.page_config)
        self._current_screenshot_config = replace(layout.screenshot_config)
        self._current_header = layout.header
        self._current_footer = layout.footer
        self._result_layout: LayoutModel | None = None
        self._sample_recordings = sample_recordings or _make_sample_recordings()
        self._last_focused: QPlainTextEdit | None = None
        self._library_mode = library_mode
        # slot id -> absolute image path, accumulated from accepted
        # Header & Footer dialogs and written by LayoutManager.save().
        self._pending_logos: dict[str, Path] = {}

        main_layout = QVBoxLayout(self)

        # --- Layout name + page setup ---
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Layout Name:"))
        self._name_edit = QLineEdit(layout.name)
        name_row.addWidget(self._name_edit, 1)
        self._page_setup_btn = QPushButton("Page Setup\u2026")
        self._page_setup_btn.setObjectName("pageSetupButton")
        self._page_setup_btn.clicked.connect(self._on_page_setup)
        name_row.addWidget(self._page_setup_btn)
        self._header_footer_btn = QPushButton("Header && Footer\u2026")
        self._header_footer_btn.setObjectName("headerFooterButton")
        self._header_footer_btn.clicked.connect(self._on_edit_header_footer)
        name_row.addWidget(self._header_footer_btn)
        main_layout.addLayout(name_row)

        # --- Three-column splitter ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: palette
        splitter.addWidget(self._build_palette())

        # Center: editors
        splitter.addWidget(self._build_editors())

        # Right: preview
        splitter.addWidget(self._build_preview())

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([200, 400, 400])
        main_layout.addWidget(splitter, 1)

        # --- Buttons ---
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._save_btn = QPushButton("Save")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._on_save)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._save_btn)
        btn_layout.addWidget(self._cancel_btn)
        main_layout.addLayout(btn_layout)

        # --- Preview debounce timer ---
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._update_preview)
        # The status line reuses the same debounce — one timer
        # drives both, so typing never triggers a repaint storm.
        self._preview_timer.timeout.connect(self._update_validation)

        # Connect text changes to debounced preview
        self._show_header_edit.textChanged.connect(self._schedule_preview)
        self._recording_body_edit.textChanged.connect(self._schedule_preview)

        # Initial preview
        self._update_preview()
        self._update_validation()

        # Open maximized so the preview page is legible.
        self.showMaximized()

    # -- Properties --------------------------------------------------------

    @property
    def result_layout(self) -> LayoutModel | None:
        """The edited layout, or None if cancelled."""
        return self._result_layout

    # -- Build UI ----------------------------------------------------------

    def _build_palette(self) -> QWidget:
        """Build the field palette (left column)."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Technical fields
        tech_group = QGroupBox("Technical {…}")
        tech_layout = QVBoxLayout(tech_group)
        self._tech_list = QListWidget()
        for spec in TECHNICAL_FIELDS.values():
            self._tech_list.addItem(spec.display_name)
        self._tech_list.itemDoubleClicked.connect(
            lambda item: self._insert_field(item.text(), technical=True)
        )
        tech_layout.addWidget(self._tech_list)
        layout.addWidget(tech_group)

        # Archival fields (archival + suggestion)
        arch_group = QGroupBox("Archival […]")
        arch_layout = QVBoxLayout(arch_group)
        self._arch_list = QListWidget()
        for spec in ARCHIVAL_FIELDS.values():
            self._arch_list.addItem(spec.display_name)
        for spec in SUGGESTION_FIELDS.values():
            self._arch_list.addItem(spec.display_name)
        self._arch_list.itemDoubleClicked.connect(
            lambda item: self._insert_field(item.text(), technical=False)
        )
        arch_layout.addWidget(self._arch_list)
        layout.addWidget(arch_group)

        return widget

    def _build_editors(self) -> QWidget:
        """Build the text editors (center column, one floating page).

        Both editors live inside a single ``layoutPage`` frame — a
        white sheet with a hairline border and a soft drop shadow,
        floating on a grey desk.
        """
        widget = QWidget()
        widget.setStyleSheet("background-color: #F3F4F6;")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)

        # Inline formatting toolbar, directly above the page.
        # Built before the editors exist so an early FocusIn can already
        # reach _update_toolbar_state safely.
        self._format_toolbar = self._build_format_toolbar()
        layout.addWidget(self._format_toolbar)

        self._page = QFrame(widget)
        self._page.setObjectName("layoutPage")
        self._page.setStyleSheet(
            "#layoutPage {"
            "  background-color: #FFFFFF;"
            "  border: 1px solid #E5E7EB;"
            "  border-radius: 4px;"
            "}"
        )
        shadow = QGraphicsDropShadowEffect(self._page)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 30))
        self._page.setGraphicsEffect(shadow)

        page_layout = QVBoxLayout(self._page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        # --- Show Header section (fixed minimum height) ---
        page_layout.addWidget(self._make_section_label("Show Header"))
        self._show_header_edit = LayoutEditorView(self._page)
        self._show_header_edit.setPlainText(
            format_layout_lines(self._input_layout.show_header)
        )
        self._show_header_edit.setMinimumHeight(160)
        self._show_header_edit.setTabChangesFocus(True)
        self._show_header_edit.installEventFilter(self)
        page_layout.addWidget(self._show_header_edit)

        page_layout.addWidget(self._make_divider())

        # --- Recording Body section (expands) ---
        page_layout.addWidget(self._make_section_label("Recording Body"))
        self._recording_body_edit = LayoutEditorView(self._page)
        self._recording_body_edit.setPlainText(
            format_layout_lines(self._input_layout.recording_body)
        )
        self._recording_body_edit.setTabChangesFocus(True)
        self._recording_body_edit.installEventFilter(self)
        page_layout.addWidget(self._recording_body_edit, 1)

        # Field-reference status line: hidden until an editor
        # contains wrong-category or unknown tokens.
        self._validation_label = QLabel(self._page)
        self._validation_label.setObjectName("validationStatusLabel")
        self._validation_label.setWordWrap(True)
        self._validation_label.hide()
        page_layout.addWidget(self._validation_label)

        # The formatting toolbar enables on a live selection
        # in whichever editor last had focus.
        self._show_header_edit.selectionChanged.connect(
            self._update_toolbar_state
        )
        self._recording_body_edit.selectionChanged.connect(
            self._update_toolbar_state
        )

        layout.addWidget(self._page, 1)

        # Syntax highlighting lives on the views; keep the old names.
        self._show_header_hl = self._show_header_edit.highlighter
        self._recording_body_hl = self._recording_body_edit.highlighter

        return widget

    @staticmethod
    def _make_section_label(text: str) -> QLabel:
        """An uppercase, letter-spaced section label."""
        label = QLabel(text)
        font = label.font()
        font.setPointSize(10)
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105.0)
        label.setFont(font)
        label.setStyleSheet("QLabel { color: #6B7280; padding-bottom: 8px; }")
        return label

    @staticmethod
    def _make_divider() -> QWidget:
        """A 1px hairline between the sections, 24px apart vertically."""
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 24, 0, 24)
        line = QFrame(holder)
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #E5E7EB;")
        holder_layout.addWidget(line)
        return holder

    def _build_preview(self) -> QWidget:
        """Build the live preview (right column)."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("Preview:"))

        # The preview scrolls internally — no outer QScrollArea.
        # auto_zoom_fallback: the narrow column would render Fit Width
        # below 0.5x, so the initial mode may drop to "100%" instead.
        self._preview_widget = PreviewWidget(
            page_config=self._current_page_config,
            auto_zoom_fallback=True,
        )
        layout.addWidget(self._preview_widget, 1)

        # Error label (hidden by default)
        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #c0392b; font-size: 11px;")
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        return widget

    # -- Event filter for focus tracking -----------------------------------

    def eventFilter(self, obj, event):
        if event.type() == event.Type.FocusIn and isinstance(obj, QPlainTextEdit):
            self._last_focused = obj
            self._update_toolbar_state()
        return super().eventFilter(obj, event)

    # -- Palette insertion -------------------------------------------------

    def _insert_field(self, field_name: str, *, technical: bool) -> None:
        """Insert a field token into the focused editor."""
        editor = self._last_focused
        if editor is None:
            editor = self._show_header_edit

        token = f"{{{field_name}}}" if technical else f"[{field_name}]"
        cursor = editor.textCursor()
        cursor.insertText(token)
        editor.setFocus()

    # -- Inline formatting toolbar ------------------------------------------

    _DEFAULT_HIGHLIGHT_COLOUR = "#FFFF00"

    def _build_format_toolbar(self) -> QWidget:
        """The inline formatting bar shown directly above the page.

        Every button wraps (or unwraps) the selection of whichever
        editor last had focus — the same ``_last_focused`` the palette
        uses.  With no live selection the whole bar is disabled.
        """
        bar = QWidget(self)
        bar.setObjectName("formatToolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 8)
        row.setSpacing(6)

        self._bold_btn = QPushButton("B", bar)
        self._bold_btn.setObjectName("boldButton")
        self._bold_btn.setCheckable(True)
        self._bold_btn.setFixedWidth(28)
        self._bold_btn.clicked.connect(
            lambda: self._toggle_markers("**", "**")
        )
        row.addWidget(self._bold_btn)

        self._italic_btn = QPushButton("I", bar)
        self._italic_btn.setObjectName("italicButton")
        self._italic_btn.setCheckable(True)
        self._italic_btn.setFixedWidth(28)
        self._italic_btn.clicked.connect(
            lambda: self._toggle_markers("*", "*")
        )
        row.addWidget(self._italic_btn)
        row.addSpacing(10)

        self._highlight_default = self._DEFAULT_HIGHLIGHT_COLOUR
        self._highlight_btn = QPushButton("Highlight", bar)
        self._highlight_btn.setObjectName("highlightButton")
        self._highlight_btn.clicked.connect(self._on_highlight_clicked)
        row.addWidget(self._highlight_btn)
        self._highlight_swatch = QLabel(bar)
        self._highlight_swatch.setObjectName("highlightSwatch")
        self._set_swatch_color(self._highlight_swatch, self._highlight_default)
        row.addWidget(self._highlight_swatch)

        self._colour_last = "#000000"
        self._colour_btn = QPushButton("Text colour", bar)
        self._colour_btn.setObjectName("textColourButton")
        self._colour_btn.clicked.connect(self._on_colour_clicked)
        row.addWidget(self._colour_btn)
        self._colour_swatch = QLabel(bar)
        self._colour_swatch.setObjectName("textColourSwatch")
        self._set_swatch_color(self._colour_swatch, self._colour_last)
        row.addWidget(self._colour_swatch)

        self._clear_btn = QPushButton("Clear", bar)
        self._clear_btn.setObjectName("clearFormatButton")
        self._clear_btn.clicked.connect(self._clear_selection_formatting)
        row.addWidget(self._clear_btn)
        row.addStretch(1)

        # Right-click re-picks the colours without wrapping anything.
        self._highlight_btn.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._highlight_btn.customContextMenuRequested.connect(
            self._pick_highlight_colour
        )
        self._colour_btn.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._colour_btn.customContextMenuRequested.connect(
            self._pick_last_colour
        )

        # Nothing focused yet.
        self._update_toolbar_state()
        return bar

    @staticmethod
    def _set_swatch_color(label: QLabel, colour: str) -> None:
        label.setStyleSheet(
            f"background-color: {colour}; border: 1px solid #9CA3AF;"
        )

    def _update_toolbar_state(self) -> None:
        """Enable the formatting buttons only while a selection is live.

        The bar reflects the editor that last had focus; with none, or
        with an empty selection, every button is disabled.
        """
        editor = self._last_focused
        enabled = editor is not None and bool(
            editor.textCursor().hasSelection()
        )
        for btn in (
            self._bold_btn,
            self._italic_btn,
            self._highlight_btn,
            self._colour_btn,
            self._clear_btn,
        ):
            btn.setEnabled(enabled)
        if not enabled:
            self._bold_btn.setChecked(False)
            self._italic_btn.setChecked(False)
            return
        cursor = editor.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        text = editor.toPlainText()
        before = text[max(0, start - 2):start]
        after = text[end:end + 2]
        self._bold_btn.setChecked(before == "**" and after == "**")
        self._italic_btn.setChecked(
            before[-1:] == "*" and before[-2:-1] != "*"
            and after[:1] == "*" and after[1:2] != "*"
        )

    def _toggle_markers(self, open_tok: str, close_tok: str) -> None:
        """Wrap the focused selection in a marker pair — or unwrap it.

        The selection is left covering the same text afterwards, so a
        second click on the same button removes the markers again.
        """
        editor = self._last_focused
        if editor is None:
            return
        cursor = editor.textCursor()
        if not cursor.hasSelection():
            return
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        length = end - start
        text = editor.toPlainText()
        o, c = len(open_tok), len(close_tok)
        if (
            text[max(0, start - o):start] == open_tok
            and text[end:end + c] == close_tok
        ):
            # Already wrapped: drop the closer first (higher offset).
            cursor.setPosition(end)
            cursor.setPosition(end + c, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.setPosition(start - o)
            cursor.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            new_start = start - o
        else:
            cursor.setPosition(end)
            cursor.insertText(close_tok)
            cursor.setPosition(start)
            cursor.insertText(open_tok)
            new_start = start + o
        cursor.setPosition(new_start)
        cursor.setPosition(new_start + length, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        self._update_toolbar_state()

    def _clear_selection_formatting(self) -> None:
        """Remove every marker pair that fully encloses the selection."""
        editor = self._last_focused
        if editor is None:
            return
        cursor = editor.textCursor()
        if not cursor.hasSelection():
            return
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        length = end - start
        text = editor.toPlainText()
        spans: list[tuple[int, int]] = []

        before = text[max(0, start - 2):start]
        after2 = text[end:end + 2]
        if before == "**" and after2 == "**":
            spans += [(start - 2, start), (end, end + 2)]
        elif text[max(0, start - 1):start] == "*" and text[end:end + 1] == "*":
            spans += [(start - 1, start), (end, end + 1)]
        if before == "==" and after2 == "==":
            spans += [(start - 2, start), (end, end + 2)]
        for pattern, close in (
            (r"\[mark=#[0-9A-Fa-f]{6}\]", "[/mark]"),
            (r"\[color=#[0-9A-Fa-f]{6}\]", "[/color]"),
        ):
            m = re.search(pattern + r"\Z", text[:start])
            if m is not None and text[end:end + len(close)] == close:
                spans.append((m.start(), m.end()))
                spans.append((end, end + len(close)))
        if not spans:
            return

        # Delete highest-offset spans first so the others stay valid.
        for s, e in sorted(spans, key=lambda p: -p[0]):
            cur = editor.textCursor()
            cur.setPosition(s)
            cur.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            cur.removeSelectedText()
        removed_before = sum(e - s for s, e in spans if e <= start)
        new_start = start - removed_before
        cur = editor.textCursor()
        cur.setPosition(new_start)
        cur.setPosition(new_start + length, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cur)
        self._update_toolbar_state()

    def _on_highlight_clicked(self) -> None:
        """Wrap in ``==`` for the default colour, ``[mark=…]`` otherwise."""
        if self._highlight_default == self._DEFAULT_HIGHLIGHT_COLOUR:
            self._toggle_markers("==", "==")
        else:
            self._toggle_markers(
                f"[mark={self._highlight_default}]", "[/mark]"
            )

    def _on_colour_clicked(self) -> None:
        """Pick a colour and wrap the selection in ``[color=…]``."""
        colour = QColorDialog.getColor(
            QColor(self._colour_last), self, "Text colour"
        )
        if not colour.isValid():
            return
        self._colour_last = colour.name().upper()
        self._set_swatch_color(self._colour_swatch, self._colour_last)
        self._toggle_markers(f"[color={self._colour_last}]", "[/color]")

    def _pick_highlight_colour(self, _pos) -> None:
        """Right-click on Highlight: change the default highlight colour."""
        colour = QColorDialog.getColor(
            QColor(self._highlight_default), self, "Highlight colour"
        )
        if not colour.isValid():
            return
        self._highlight_default = colour.name().upper()
        self._set_swatch_color(self._highlight_swatch, self._highlight_default)

    def _pick_last_colour(self, _pos) -> None:
        """Right-click on Text colour: change the last-used colour."""
        colour = QColorDialog.getColor(
            QColor(self._colour_last), self, "Text colour"
        )
        if not colour.isValid():
            return
        self._colour_last = colour.name().upper()
        self._set_swatch_color(self._colour_swatch, self._colour_last)

    # -- Preview -----------------------------------------------------------

    def _schedule_preview(self) -> None:
        """Debounce the preview update by 200 ms."""
        self._preview_timer.start()

    def _current_validation_issues(self) -> list[tuple[str, str]]:
        """Return the list of (token_text, kind) issues currently present,
        where kind is "wrong_category" or "unknown"."""
        issues: list[tuple[str, str]] = []
        for editor in (self._show_header_edit, self._recording_body_edit):
            it = FIELD_TOKEN_RE.globalMatch(editor.toPlainText())
            while it.hasNext():
                token = it.next().captured()
                if is_screenshot_token(token):
                    continue  # not a field reference
                kind = classify_field_token(token)
                if kind in ("wrong_category", "unknown"):
                    issues.append((token, kind))
        return issues

    def _update_validation(self) -> None:
        """Refresh the field-reference status line under the editors.

        Runs on the shared 200 ms debounce timer, so the registry
        lookups happen at most once per pause in typing.
        """
        issues = self._current_validation_issues()
        if not issues:
            self._validation_label.hide()
            self._validation_label.setToolTip("")
            return
        count = len(issues)
        self._validation_label.setText(
            f"{count} field reference "
            f"{'issue' if count == 1 else 'issues'}"
        )
        # Amber when every issue is a wrong delimiter pair; red as soon
        # as one token names no registered field at all.
        has_unknown = any(kind == "unknown" for _, kind in issues)
        self._validation_label.setStyleSheet(
            f"color: {'#991B1B' if has_unknown else '#92400E'};"
        )
        lines = []
        for token, kind in issues[:10]:
            if kind == "unknown":
                lines.append(f"{token} → unknown field")
            else:
                inner = token[1:-1]
                suggestion = f"[{inner}]" if token[:1] == "{" else f"{{{inner}}}"
                lines.append(f"{token} → use {suggestion}")
        self._validation_label.setToolTip("\n".join(lines))
        self._validation_label.show()

    def _update_preview(self) -> None:
        """Rebuild the live preview from current editor content."""
        try:
            show_header = parse_layout_lines(self._show_header_edit.toPlainText())
            recording_body = parse_layout_lines(self._recording_body_edit.toPlainText())

            layout = replace(
                self._input_layout,
                name=self._name_edit.text().strip() or "Untitled",
                show_header=show_header,
                recording_body=recording_body,
                screenshot_config=self._current_screenshot_config,
                header=self._current_header,
                footer=self._current_footer,
            )

            groups = group_by_show(self._sample_recordings)
            document = LayoutResolver().resolve(layout, groups)
            self._preview_widget.set_document(document)

            if document.errors:
                msgs = [e.message for e in document.errors[:3]]
                self._error_label.setText("Layout warnings: " + "; ".join(msgs))
                self._error_label.setVisible(True)
            else:
                self._error_label.setVisible(False)
        except Exception as exc:
            self._error_label.setText(f"Preview error: {exc}")
            self._error_label.setVisible(True)

    # -- Page Setup ---------------------------------------------------------

    def _on_page_setup(self) -> None:
        """Open the Page Setup dialog and apply the accepted config."""
        dialog = PageSetupDialog(
            self._current_page_config,
            screenshot_config=self._current_screenshot_config,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.result_page_config
        if result is not None:
            self._current_page_config = result
            self._preview_widget.set_page_config(result)
        # The screenshot sizing lives in the layout, so the preview only
        # picks up a change through a rebuilt document.  The
        # isinstance guard keeps a partially-mocked dialog
        # from feeding a non-config into the preview.
        shot = dialog.result_screenshot_config
        if isinstance(shot, ScreenshotConfig):
            self._current_screenshot_config = shot
            self._update_preview()

    # -- Header & Footer ----------------------------------------------------

    def _on_edit_header_footer(self) -> None:
        """Open the Header & Footer dialog and apply the accepted config."""
        dialog = HeaderFooterDialog(
            self._current_header,
            self._current_footer,
            self,
            library_mode=self._library_mode,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._current_header = dialog.result_header
        self._current_footer = dialog.result_footer
        # Logo choices made in this session travel to LayoutManager.save
        # when the designer saves.
        self._pending_logos.update(dialog.result_pending_logos)
        # The chosen sources are not on disk yet — hand them to the
        # preview so it renders them immediately.
        self._preview_widget.set_pending_logos(dict(self._pending_logos))
        # Rebuild the preview so the new header/footer slots render.
        self._update_preview()

    # -- Save / Cancel -----------------------------------------------------

    def _on_save(self) -> None:
        """Validate and accept."""
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Layout Designer", "Layout name cannot be empty.")
            return

        show_header = parse_layout_lines(self._show_header_edit.toPlainText())
        recording_body = parse_layout_lines(self._recording_body_edit.toPlainText())

        def _build_result() -> LayoutModel:
            return replace(
                self._input_layout,
                name=name,
                show_header=show_header,
                recording_body=recording_body,
                page_config=self._current_page_config,
                screenshot_config=self._current_screenshot_config,
                header=self._current_header,
                footer=self._current_footer,
            )

        self._result_layout = _build_result()
        # Persist to the library when the caller asked for it (window
        # flow) OR whenever this session chose logo images — in the
        # New-session flow (library_mode=False) the pending image must
        # still be copied and the JSON written with its logo_file, or
        # the saved layout would carry a dead reference.
        if self._library_mode or self._pending_logos:
            # Chosen logos are copied into the library directory and the
            # stored JSON picks up their <layout>.logo.<slot> filenames.
            # A snapshot is passed so the local dict can be
            # cleared after the write without aliasing what was saved.
            retargeted = LayoutManager().save(
                self._result_layout, pending_logos=dict(self._pending_logos)
            )
            self._pending_logos.clear()
            # Pending sources are on disk now (the retargeted layout
            # carries their logo_file); the preview goes back to plain
            # disk resolution.
            self._preview_widget.set_pending_logos({})
            # Adopt the retargeted header/footer and rebuild the result
            # so the layout we return to the caller carries the copied
            # files' logo_file references, not the original Nones
            # — otherwise the session/preview would hold a
            # layout whose logos resolve to nothing.
            self._current_header = retargeted.header
            self._current_footer = retargeted.footer
            self._result_layout = _build_result()
        self.accept()


# ---------------------------------------------------------------------------
# Page Setup dialog
# ---------------------------------------------------------------------------

class PageSetupDialog(QDialog):
    """Modal editor for a layout's :class:`PageConfig` and
    :class:`ScreenshotConfig`.

    Edits the page size, orientation, per-side margins, the body and
    header font families (chosen from the fonts installed on this
    machine), and the screenshot sizing used by inline
    ``[Screenshot:N]`` blocks.  On accept, :attr:`result_page_config`
    is a NEW :class:`PageConfig` with the edited fields and
    width/height recalculated from page size + orientation, and
    :attr:`result_screenshot_config` is a NEW
    :class:`ScreenshotConfig`; all other fields are preserved.  Both
    are ``None`` until the dialog is accepted.

    The font combos hold a family name (``str``), never a ``QFont``.
    A configured family that is not installed here is shown with a
    warning beneath the combo; the exporter falls back to Helvetica
    for it at export time.
    """

    def __init__(
        self,
        page_config: PageConfig,
        parent: QWidget | None = None,
        *,
        screenshot_config: ScreenshotConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Page Setup")
        self.setModal(True)
        self._initial = page_config
        self._initial_screenshots = (
            screenshot_config if screenshot_config is not None else ScreenshotConfig()
        )
        self._result_page_config: PageConfig | None = None
        self._result_screenshot_config: ScreenshotConfig | None = None

        layout = QVBoxLayout(self)

        # --- Page size ---
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Page Size:"))
        self._size_combo = QComboBox(self)
        self._size_combo.setObjectName("pageSizeCombo")
        self._size_combo.addItems(["A4", "Letter", "A3"])
        self._size_combo.setCurrentText(page_config.page_size)
        size_row.addWidget(self._size_combo)
        layout.addLayout(size_row)

        # --- Orientation ---
        orient_row = QHBoxLayout()
        orient_row.addWidget(QLabel("Orientation:"))
        self._orientation_combo = QComboBox(self)
        self._orientation_combo.setObjectName("orientationCombo")
        self._orientation_combo.addItems(["Portrait", "Landscape"])
        self._orientation_combo.setCurrentText(page_config.orientation.capitalize())
        orient_row.addWidget(self._orientation_combo)
        layout.addLayout(orient_row)

        # --- Margins (mm) ---
        margins_box = QGroupBox("Margins (mm)")
        margins_layout = QGridLayout(margins_box)
        effective = page_config.effective_margins_mm()
        self._margin_spins: dict[str, QDoubleSpinBox] = {}
        for row, (side, value) in enumerate(
            zip(("top", "right", "bottom", "left"), effective)
        ):
            margins_layout.addWidget(QLabel(side.capitalize() + ":"), row, 0)
            spin = QDoubleSpinBox(self)
            spin.setObjectName(f"margin{side.capitalize()}Spin")
            spin.setRange(0.0, 50.0)
            spin.setSingleStep(0.5)
            spin.setDecimals(1)
            spin.setValue(value)
            self._margin_spins[side] = spin
            margins_layout.addWidget(spin, row, 1)
        layout.addWidget(margins_box)

        reset_btn = QPushButton("Reset margins to uniform")
        reset_btn.setObjectName("resetMarginsButton")
        reset_btn.clicked.connect(self._on_reset_margins)
        layout.addWidget(reset_btn)

        # --- Fonts ---
        # QFontComboBox lists the fonts installed on this machine (no
        # filter — it already shows scalable fonts only).  What is
        # stored on accept is the family name (a plain str), never a
        # QFont.  setCurrentFont() does not need the family to be
        # installed: when it is not, the combo stays on its default
        # and the warning label beneath it says so.
        # QFontDatabase is a static-style class in Qt ≥ 6.8; the
        # instance constructor it replaced is deprecated.
        self._installed_fonts = {
            name.lower() for name in QFontDatabase.families()
        }
        fonts_box = QGroupBox("Fonts")
        fonts_layout = QGridLayout(fonts_box)

        fonts_layout.addWidget(QLabel("Body font:"), 0, 0)
        self._body_font_combo = QFontComboBox(self)
        self._body_font_combo.setObjectName("bodyFontCombo")
        self._body_font_combo.setCurrentFont(QFont(page_config.body_font))
        fonts_layout.addWidget(self._body_font_combo, 0, 1)
        self._body_font_warning = QLabel("", self)
        self._body_font_warning.setObjectName("bodyFontWarning")
        self._body_font_warning.setWordWrap(True)
        self._body_font_warning.setStyleSheet("color: #B45309; font-size: 10px;")
        fonts_layout.addWidget(self._body_font_warning, 1, 0, 1, 2)

        fonts_layout.addWidget(QLabel("Header font:"), 2, 0)
        self._header_font_combo = QFontComboBox(self)
        self._header_font_combo.setObjectName("headerFontCombo")
        self._header_font_combo.setCurrentFont(QFont(page_config.header_font))
        fonts_layout.addWidget(self._header_font_combo, 2, 1)
        self._header_font_warning = QLabel("", self)
        self._header_font_warning.setObjectName("headerFontWarning")
        self._header_font_warning.setWordWrap(True)
        self._header_font_warning.setStyleSheet("color: #B45309; font-size: 10px;")
        fonts_layout.addWidget(self._header_font_warning, 3, 0, 1, 2)

        # Warning state reflects the CONFIGURED family (what the
        # dialog loaded), not the combo's displayed selection.
        self._update_font_warning(
            self._body_font_warning, page_config.body_font
        )
        self._update_font_warning(
            self._header_font_warning, page_config.header_font
        )
        # The combos only offer installed fonts, so any user selection
        # clears a warning left over from the loaded configuration.
        self._body_font_combo.currentFontChanged.connect(
            lambda font: self._update_font_warning(
                self._body_font_warning, font.family()
            )
        )
        self._header_font_combo.currentFontChanged.connect(
            lambda font: self._update_font_warning(
                self._header_font_warning, font.family()
            )
        )
        layout.addWidget(fonts_box)

        # --- Screenshots ---
        # Sizing for inline [Screenshot:N] blocks, shared by the preview
        # and the PDF exporter.
        shots_box = QGroupBox("Screenshots")
        shots_layout = QGridLayout(shots_box)
        shots = self._initial_screenshots

        shots_layout.addWidget(QLabel("Max width (px):"), 0, 0)
        self._shot_width_spin = QSpinBox(self)
        self._shot_width_spin.setObjectName("shotMaxWidthSpin")
        self._shot_width_spin.setRange(50, 1000)
        self._shot_width_spin.setSingleStep(10)
        self._shot_width_spin.setValue(shots.max_width_px)
        shots_layout.addWidget(self._shot_width_spin, 0, 1)

        shots_layout.addWidget(QLabel("Max height (px):"), 1, 0)
        self._shot_height_spin = QSpinBox(self)
        self._shot_height_spin.setObjectName("shotMaxHeightSpin")
        self._shot_height_spin.setRange(50, 1000)
        self._shot_height_spin.setSingleStep(10)
        self._shot_height_spin.setValue(shots.max_height_px)
        shots_layout.addWidget(self._shot_height_spin, 1, 1)

        shots_layout.addWidget(QLabel("Layout:"), 2, 0)
        self._shot_layout_combo = QComboBox(self)
        self._shot_layout_combo.setObjectName("shotLayoutCombo")
        self._shot_layout_combo.addItems(["row", "grid"])
        self._shot_layout_combo.setCurrentText(shots.layout)
        self._shot_layout_combo.currentTextChanged.connect(
            self._on_shot_layout_changed
        )
        shots_layout.addWidget(self._shot_layout_combo, 2, 1)

        self._shot_per_row_label = QLabel("Photos per row:")
        shots_layout.addWidget(self._shot_per_row_label, 3, 0)
        self._shot_per_row_spin = QSpinBox(self)
        self._shot_per_row_spin.setObjectName("shotPhotosPerRowSpin")
        self._shot_per_row_spin.setRange(1, 6)
        self._shot_per_row_spin.setValue(shots.photos_per_row)
        shots_layout.addWidget(self._shot_per_row_spin, 3, 1)

        # Visual knobs (L15.9.3): gap, border, corner radius, alignment.
        shots_layout.addWidget(QLabel("Spacing (px):"), 4, 0)
        self._shot_spacing_spin = QSpinBox(self)
        self._shot_spacing_spin.setObjectName("shotSpacingSpin")
        self._shot_spacing_spin.setRange(0, 40)
        self._shot_spacing_spin.setSingleStep(1)
        self._shot_spacing_spin.setValue(shots.spacing_px)
        shots_layout.addWidget(self._shot_spacing_spin, 4, 1)

        shots_layout.addWidget(QLabel("Border width (px):"), 5, 0)
        self._shot_border_width_spin = QSpinBox(self)
        self._shot_border_width_spin.setObjectName("shotBorderWidthSpin")
        self._shot_border_width_spin.setRange(0, 10)
        self._shot_border_width_spin.setSingleStep(1)
        self._shot_border_width_spin.setValue(shots.border_width_px)
        shots_layout.addWidget(self._shot_border_width_spin, 5, 1)

        self._shot_border_color = shots.border_color
        shots_layout.addWidget(QLabel("Border colour:"), 6, 0)
        self._shot_border_colour_button = QPushButton(self)
        self._shot_border_colour_button.setObjectName("shotBorderColourButton")
        self._update_shot_border_swatch()
        self._shot_border_colour_button.clicked.connect(
            self._on_shot_border_colour_clicked
        )
        shots_layout.addWidget(self._shot_border_colour_button, 6, 1)

        shots_layout.addWidget(QLabel("Corner radius (px):"), 7, 0)
        self._shot_corner_radius_spin = QSpinBox(self)
        self._shot_corner_radius_spin.setObjectName("shotCornerRadiusSpin")
        self._shot_corner_radius_spin.setRange(0, 40)
        self._shot_corner_radius_spin.setSingleStep(1)
        self._shot_corner_radius_spin.setValue(shots.corner_radius_px)
        shots_layout.addWidget(self._shot_corner_radius_spin, 7, 1)

        shots_layout.addWidget(QLabel("Alignment:"), 8, 0)
        self._shot_align_combo = QComboBox(self)
        self._shot_align_combo.setObjectName("shotAlignCombo")
        self._shot_align_combo.addItems(["Left", "Center", "Right"])
        self._shot_align_combo.setCurrentText(shots.align.capitalize())
        shots_layout.addWidget(self._shot_align_combo, 8, 1)

        # Only meaningful for a grid; hidden while "row" is selected.
        self._on_shot_layout_changed(shots.layout)
        layout.addWidget(shots_box)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    @property
    def result_page_config(self) -> PageConfig | None:
        """The accepted PageConfig, or None if cancelled."""
        return self._result_page_config

    @property
    def result_screenshot_config(self) -> ScreenshotConfig | None:
        """The accepted ScreenshotConfig, or None if cancelled."""
        return self._result_screenshot_config

    def _on_shot_layout_changed(self, value: str) -> None:
        """Show the photos-per-row control only for a grid layout."""
        is_grid = value == "grid"
        self._shot_per_row_label.setVisible(is_grid)
        self._shot_per_row_spin.setVisible(is_grid)

    def _update_shot_border_swatch(self) -> None:
        """Show the border colour as the button's background swatch."""
        self._shot_border_colour_button.setText(self._shot_border_color)
        self._shot_border_colour_button.setStyleSheet(
            f"background-color: {self._shot_border_color};"
        )

    def _on_shot_border_colour_clicked(self) -> None:
        """Pick the screenshot border colour with the colour dialog."""
        colour = QColorDialog.getColor(
            QColor(self._shot_border_color), self, "Border colour"
        )
        if not colour.isValid():
            return
        # QColor.name() already returns the lowercase "#rrggbb" form.
        self._shot_border_color = colour.name()
        self._update_shot_border_swatch()

    def _update_font_warning(self, label: QLabel, family: str) -> None:
        """Show *label* beneath a font combo only for a missing family.

        A family counts as missing when this machine does not have it.
        "Helvetica" is excluded on purpose: it is the fpdf built-in and
        the fallback family itself, so it is never at risk (a warning
        claiming Helvetica falls back to Helvetica would be noise).
        """
        missing = bool(family) and family.lower() != "helvetica" \
            and family.lower() not in self._installed_fonts
        label.setText(
            f'"{family}" is not installed here \u2014 '
            "will fall back to Helvetica."
        )
        label.setVisible(missing)

    def _on_reset_margins(self) -> None:
        """Set all four margin spin boxes back to the uniform margin."""
        for spin in self._margin_spins.values():
            spin.setValue(self._initial.margin_mm)

    def _on_accept(self) -> None:
        """Build the result PageConfig and accept."""
        orientation = self._orientation_combo.currentText().lower()
        # width_mm/height_mm are re-derived from page_size + orientation
        # by PageConfig.__post_init__; passing them explicitly is redundant
        # but documents the source of truth.
        width_mm, height_mm = page_size_dimensions_mm(
            self._size_combo.currentText(), orientation
        )
        self._result_page_config = replace(
            self._initial,
            page_size=self._size_combo.currentText(),
            orientation=orientation,
            width_mm=width_mm,
            height_mm=height_mm,
            margin_top_mm=self._margin_spins["top"].value(),
            margin_right_mm=self._margin_spins["right"].value(),
            margin_bottom_mm=self._margin_spins["bottom"].value(),
            margin_left_mm=self._margin_spins["left"].value(),
            # Family names (str) as displayed by the combos.
            body_font=self._body_font_combo.currentFont().family(),
            header_font=self._header_font_combo.currentFont().family(),
        )
        self._result_screenshot_config = replace(
            self._initial_screenshots,
            max_width_px=self._shot_width_spin.value(),
            max_height_px=self._shot_height_spin.value(),
            layout=self._shot_layout_combo.currentText(),
            photos_per_row=self._shot_per_row_spin.value(),
            spacing_px=self._shot_spacing_spin.value(),
            border_width_px=self._shot_border_width_spin.value(),
            border_color=self._shot_border_color,
            corner_radius_px=self._shot_corner_radius_spin.value(),
            align=self._shot_align_combo.currentText().lower(),
        )
        self.accept()
