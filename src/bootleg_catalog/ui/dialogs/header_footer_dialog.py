"""Header & Footer configuration dialog.

Modal editor for a layout's optional page-header and page-footer rows
(:class:`HeaderConfig` / :class:`FooterConfig`).  Each of the
six slots is a small "None / Text / Logo" combo: None hides every
widget for that row, Text shows the line edit + size + bold, Logo shows
a "Choose Image..." button, a fixed-size thumbnail, and a width_mm spin
box.  Pure UI: no probing, no session, no Catalog/SQLite.

Accept rules, per side:

- "Enabled" unchecked -> the result is ``None``
- "Enabled" checked with at least one non-empty slot -> a NEW config
  with ``enabled=True``; an empty line edit maps to a ``None`` slot
  (never an empty TextSlot), and a "Logo" slot with neither a file
  chosen in this session nor a pre-existing logo also maps to ``None``
- Cancel -> both results ``None`` and no pending logos leak

Logos in every flow: the "Logo" combo entry is enabled and
pre-existing LogoSlots are shown as "Logo" regardless of the
``library_mode`` flag.  The parameter stays on the constructor (public
signature; callers pass it) as a decision-time signal for the caller's
save policy — the dialog never branches on it and never
infers the mode from the layout name.

Chosen images are NOT written by this dialog.  They accumulate in
:attr:`result_pending_logos` (slot id -> absolute source path) and are
handed to :meth:`bootleg_catalog.core.layout_manager.LayoutManager.save`
at save time; a freshly chosen LogoSlot therefore comes back with
``logo_file=None``.  Pre-existing LogoSlots the user did not re-choose
keep their original ``logo_file`` and stay out of the pending dict.

The input configs are only read, never mutated: ``HeaderConfig`` and
``FooterConfig`` are plain (non-frozen) dataclasses, so results are
always freshly built instances by convention.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bootleg_catalog.models.layout_model import (
    FooterConfig,
    HeaderConfig,
    LogoSlot,
    TextSlot,
)
from bootleg_catalog.utils.paths import app_data_dir


_SLOT_PLACEHOLDER = "Use {Field} or [Field] for dynamic values"
_SLOT_SIDES = ("left", "center", "right")

# Kind-combo item data; the visible labels are None / Text / Logo.
_KIND_NONE = "none"
_KIND_TEXT = "text"
_KIND_LOGO = "logo"

_LOGO_FILTER = "Images (*.png *.jpg *.jpeg *.webp)"
_LOGO_PREVIEW_W = 80
_LOGO_PREVIEW_H = 40
_LOGO_MISSING_TEXT = "missing"


class _SideWidgets(NamedTuple):
    """Widget bundle for one side (header/footer) of the dialog."""

    prefix: str
    group: QGroupBox
    enabled: QCheckBox
    scope: QComboBox | None          # None for the footer (no scope)
    kinds: dict[str, QComboBox]
    kind_models: dict[str, QStandardItemModel]
    edits: dict[str, QLineEdit]
    sizes: dict[str, QSpinBox]
    bolds: dict[str, QCheckBox]
    logo_buttons: dict[str, QPushButton]
    logo_previews: dict[str, QLabel]
    logo_widths: dict[str, QDoubleSpinBox]


class HeaderFooterDialog(QDialog):
    """Modal editor for a layout's header and footer slots."""

    def __init__(
        self,
        header: HeaderConfig | None,
        footer: FooterConfig | None,
        parent: QWidget | None = None,
        *,
        library_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Header and Footer")
        self.setModal(True)
        self.setMinimumSize(640, 480)

        # Kept for callers (public constructor); no longer gates any UI.
        self._library_mode = library_mode
        self._input_header = header
        self._input_footer = footer
        # slot id ("header.left" ... "footer.right") -> absolute source
        # path chosen in THIS dialog session.
        self._pending_logos: dict[str, Path] = {}

        self._result_header: HeaderConfig | None = None
        self._result_footer: FooterConfig | None = None

        layout = QVBoxLayout(self)

        self._header_side = self._build_group("Header", "header", with_scope=True)
        self._footer_side = self._build_group("Footer", "footer", with_scope=False)
        layout.addWidget(self._header_side.group)
        layout.addWidget(self._footer_side.group)
        layout.addStretch()

        # --- Buttons ---
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        # Populate from the input configs (read-only), then make the
        # child controls follow the Enabled checkboxes and slot kinds.
        self._populate(header, footer)
        self._header_side.enabled.toggled.connect(
            lambda on: self._set_side_enabled(self._header_side, on)
        )
        self._footer_side.enabled.toggled.connect(
            lambda on: self._set_side_enabled(self._footer_side, on)
        )
        self._set_side_enabled(
            self._header_side, self._header_side.enabled.isChecked()
        )
        self._set_side_enabled(
            self._footer_side, self._footer_side.enabled.isChecked()
        )
        for side in (self._header_side, self._footer_side):
            for key in _SLOT_SIDES:
                self._apply_slot_mode(side.prefix, key)

    # -- Result accessors -------------------------------------------------

    @property
    def result_header(self) -> HeaderConfig | None:
        """The accepted header config, or None if cancelled."""
        return self._result_header

    @property
    def result_footer(self) -> FooterConfig | None:
        """The accepted footer config, or None if cancelled."""
        return self._result_footer

    @property
    def result_pending_logos(self) -> dict[str, Path]:
        """Return a copy of the pending logos dict (slot_id -> absolute path).

        Empty dict when no logo was chosen in this dialog session; a
        Cancelled dialog never leaks choices made before the Cancel.
        """
        return dict(self._pending_logos)

    def reject(self) -> None:
        """Cancel: results stay None and pending logo choices are dropped."""
        self._pending_logos.clear()
        super().reject()

    # -- Build UI ----------------------------------------------------------

    def _build_group(
        self, title: str, prefix: str, *, with_scope: bool
    ) -> _SideWidgets:
        """Build one QGroupBox with Enabled, optional Scope, and 3 slot rows."""
        group = QGroupBox(title)
        group.setObjectName(f"{prefix}Group")
        v = QVBoxLayout(group)

        enabled = QCheckBox("Enabled", group)
        enabled.setObjectName(f"{prefix}EnabledCheck")
        v.addWidget(enabled)

        scope: QComboBox | None = None
        if with_scope:
            scope_row = QHBoxLayout()
            scope_row.addWidget(QLabel("Scope:", group))
            scope = QComboBox(group)
            scope.setObjectName(f"{prefix}ScopeCombo")
            scope.addItem("All pages", "all")
            scope.addItem("First page only", "first")
            scope_row.addWidget(scope)
            scope_row.addStretch()
            v.addLayout(scope_row)

        grid = QGridLayout()
        grid.addWidget(QLabel("Type", group), 0, 1)
        grid.addWidget(QLabel("Text", group), 0, 2)
        grid.addWidget(QLabel("Size", group), 0, 3)

        kinds: dict[str, QComboBox] = {}
        kind_models: dict[str, QStandardItemModel] = {}
        edits: dict[str, QLineEdit] = {}
        sizes: dict[str, QSpinBox] = {}
        bolds: dict[str, QCheckBox] = {}
        logo_buttons: dict[str, QPushButton] = {}
        logo_previews: dict[str, QLabel] = {}
        logo_widths: dict[str, QDoubleSpinBox] = {}
        for row, side in enumerate(_SLOT_SIDES, start=1):
            cap = side.capitalize()
            grid.addWidget(QLabel(f"{cap}:", group), row, 0)

            kind = QComboBox(group)
            kind.setObjectName(f"{prefix}{cap}Kind")
            # A QStandardItemModel backs the combo so every entry —
            # including "Logo" — can be enabled in every mode; tests
            # read the item flags through kind_models.
            kind_model = QStandardItemModel(kind)
            kind.setModel(kind_model)
            kind.addItem("None", _KIND_NONE)
            kind.addItem("Text", _KIND_TEXT)
            kind.addItem("Logo", _KIND_LOGO)
            kind_models[side] = kind_model
            grid.addWidget(kind, row, 1)

            edit = QLineEdit(group)
            edit.setObjectName(f"{prefix}{cap}Edit")
            edit.setPlaceholderText(_SLOT_PLACEHOLDER)
            grid.addWidget(edit, row, 2)

            size = QSpinBox(group)
            size.setObjectName(f"{prefix}{cap}Size")
            size.setRange(6, 24)
            size.setValue(10)
            grid.addWidget(size, row, 3)

            bold = QCheckBox("Bold", group)
            bold.setObjectName(f"{prefix}{cap}Bold")
            grid.addWidget(bold, row, 4)

            # Logo widgets share the text cells of the row; visibility
            # swaps the two sets (hidden grid widgets take no space).
            logo_btn = QPushButton("Choose Image\u2026", group)
            logo_btn.setObjectName(f"{prefix}{cap}LogoButton")
            grid.addWidget(logo_btn, row, 2)

            logo_preview = QLabel(group)
            logo_preview.setObjectName(f"{prefix}{cap}LogoPreview")
            logo_preview.setFixedSize(_LOGO_PREVIEW_W, _LOGO_PREVIEW_H)
            logo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            logo_preview.setWordWrap(False)
            logo_preview.setStyleSheet(
                "QLabel { border: 1px solid #E5E7EB; background-color: #F9FAFB; }"
            )
            grid.addWidget(logo_preview, row, 3)

            logo_width = QDoubleSpinBox(group)
            logo_width.setObjectName(f"{prefix}{cap}LogoWidth")
            logo_width.setRange(1.0, 100.0)
            logo_width.setSingleStep(1.0)
            logo_width.setDecimals(1)
            logo_width.setValue(20.0)
            grid.addWidget(logo_width, row, 4)

            kinds[side] = kind
            edits[side] = edit
            sizes[side] = size
            bolds[side] = bold
            logo_buttons[side] = logo_btn
            logo_previews[side] = logo_preview
            logo_widths[side] = logo_width

            kind.currentIndexChanged.connect(
                lambda _index, p=prefix, k=side: self._apply_slot_mode(p, k)
            )
            logo_btn.clicked.connect(
                lambda _checked=False, p=prefix, k=side: self._on_choose_logo(p, k)
            )

        grid.setColumnStretch(2, 1)
        v.addLayout(grid)

        return _SideWidgets(
            prefix=prefix,
            group=group,
            enabled=enabled,
            scope=scope,
            kinds=kinds,
            kind_models=kind_models,
            edits=edits,
            sizes=sizes,
            bolds=bolds,
            logo_buttons=logo_buttons,
            logo_previews=logo_previews,
            logo_widths=logo_widths,
        )

    # -- Populate from input configs ---------------------------------------

    def _populate(
        self, header: HeaderConfig | None, footer: FooterConfig | None
    ) -> None:
        self._header_side.enabled.setChecked(
            header.enabled if header is not None else False
        )
        if header is not None and self._header_side.scope is not None:
            index = self._header_side.scope.findData(header.scope)
            if index >= 0:
                self._header_side.scope.setCurrentIndex(index)
        self._fill_slots(self._header_side, header)

        self._footer_side.enabled.setChecked(
            footer.enabled if footer is not None else False
        )
        self._fill_slots(self._footer_side, footer)

    def _fill_slots(
        self, side: _SideWidgets, config: HeaderConfig | FooterConfig | None
    ) -> None:
        for key in _SLOT_SIDES:
            slot = getattr(config, key) if config is not None else None
            combo = side.kinds[key]
            if isinstance(slot, LogoSlot):
                combo.setCurrentIndex(combo.findData(_KIND_LOGO))
                side.logo_widths[key].setValue(slot.width_mm)
                self._show_logo_image(side, key, slot.logo_file)
            elif isinstance(slot, TextSlot):
                combo.setCurrentIndex(combo.findData(_KIND_TEXT))
                side.edits[key].setText(slot.text)
                side.sizes[key].setValue(max(6, min(24, int(slot.font_size))))
                side.bolds[key].setChecked(bool(slot.bold))
            else:
                # None slot.
                combo.setCurrentIndex(combo.findData(_KIND_NONE))
                side.edits[key].setText("")
                side.sizes[key].setValue(10)
                side.bolds[key].setChecked(False)

    def _show_logo_image(
        self, side: _SideWidgets, key: str, logo_file: str | None
    ) -> None:
        """Thumbnail a stored logo, or a 'missing' label when absent."""
        preview = side.logo_previews[key]
        preview.clear()
        if not logo_file:
            return
        # logo_file is a relative filename inside the layouts directory
        # (resolved by the render layer; existence is never validated on
        # load — a missing file shows the placeholder below).
        image_path = app_data_dir() / "layouts" / logo_file
        if not image_path.is_file():
            preview.setText(_LOGO_MISSING_TEXT)
            return
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            preview.setText(_LOGO_MISSING_TEXT)
            return
        preview.setPixmap(self._scaled_logo(pixmap))

    @staticmethod
    def _scaled_logo(pixmap: QPixmap) -> QPixmap:
        return pixmap.scaled(
            _LOGO_PREVIEW_W,
            _LOGO_PREVIEW_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _apply_slot_mode(self, prefix: str, key: str) -> None:
        """Show only the widget set belonging to the slot's kind."""
        side = self._side_for(prefix)
        kind = side.kinds[key].currentData()
        show_text = kind == _KIND_TEXT
        show_logo = kind == _KIND_LOGO
        side.edits[key].setVisible(show_text)
        side.sizes[key].setVisible(show_text)
        side.bolds[key].setVisible(show_text)
        side.logo_buttons[key].setVisible(show_logo)
        side.logo_previews[key].setVisible(show_logo)
        side.logo_widths[key].setVisible(show_logo)

    @staticmethod
    def _set_side_enabled(side: _SideWidgets, on: bool) -> None:
        """Enable/disable the child controls to follow the Enabled checkbox."""
        if side.scope is not None:
            side.scope.setEnabled(on)
        for widgets in (
            side.kinds,
            side.edits,
            side.sizes,
            side.bolds,
            side.logo_buttons,
            side.logo_previews,
            side.logo_widths,
        ):
            for widget in widgets.values():
                widget.setEnabled(on)

    def _side_for(self, prefix: str) -> _SideWidgets:
        return self._header_side if prefix == "header" else self._footer_side

    # -- Logo picking -------------------------------------------------------

    def _on_choose_logo(self, prefix: str, key: str) -> None:
        """Ask for an image and remember it as this slot's pending logo."""
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Choose Logo Image", "", _LOGO_FILTER
        )
        if not chosen:
            return                          # file dialog cancelled
        source = Path(chosen).absolute()
        self._pending_logos[f"{prefix}.{key}"] = source
        preview = self._side_for(prefix).logo_previews[key]
        pixmap = QPixmap(str(source))
        if pixmap.isNull():
            preview.clear()
            preview.setText(_LOGO_MISSING_TEXT)
        else:
            preview.setPixmap(self._scaled_logo(pixmap))

    # -- Accept ------------------------------------------------------------

    def _on_accept(self) -> None:
        """Build fresh result configs from the widgets and accept."""
        self._result_header = self._build_header()
        self._result_footer = self._build_footer()
        self._prune_pending_logos()
        self.accept()

    def _build_header(self) -> HeaderConfig | None:
        side = self._header_side
        if not side.enabled.isChecked():
            return None
        slots = {
            key: self._make_result_slot(side, "header", key)
            for key in _SLOT_SIDES
        }
        if not any(slot is not None for slot in slots.values()):
            # Enabled but nothing to draw anywhere -> nothing configured.
            return None
        scope = side.scope.currentData() if side.scope is not None else "all"
        return HeaderConfig(
            enabled=True,
            scope=scope,
            left=slots["left"],
            center=slots["center"],
            right=slots["right"],
        )

    def _build_footer(self) -> FooterConfig | None:
        side = self._footer_side
        if not side.enabled.isChecked():
            return None
        slots = {
            key: self._make_result_slot(side, "footer", key)
            for key in _SLOT_SIDES
        }
        if not any(slot is not None for slot in slots.values()):
            return None
        return FooterConfig(
            enabled=True,
            left=slots["left"],
            center=slots["center"],
            right=slots["right"],
        )

    def _make_result_slot(
        self, side: _SideWidgets, prefix: str, key: str
    ) -> TextSlot | LogoSlot | None:
        kind = side.kinds[key].currentData()
        if kind == _KIND_TEXT:
            text = side.edits[key].text()
            if not text.strip():
                # Empty (or whitespace-only) line edit -> no slot, never
                # an empty TextSlot.
                return None
            return TextSlot(
                text=text,
                font_size=side.sizes[key].value(),
                bold=side.bolds[key].isChecked(),
            )
        if kind == _KIND_LOGO:
            width_mm = side.logo_widths[key].value()
            if f"{prefix}.{key}" in self._pending_logos:
                # Chosen in this session: LayoutManager.save fills
                # logo_file from pending_logos at save time.
                return LogoSlot(width_mm=width_mm, logo_file=None)
            original = self._original_slot(prefix, key)
            if isinstance(original, LogoSlot):
                # Pre-existing logo kept: the original reference stays.
                return LogoSlot(width_mm=width_mm, logo_file=original.logo_file)
            return None                      # Logo mode, nothing chosen
        return None

    def _original_slot(self, prefix: str, key: str) -> TextSlot | LogoSlot | None:
        config = self._input_header if prefix == "header" else self._input_footer
        return getattr(config, key) if config is not None else None

    def _prune_pending_logos(self) -> None:
        """Drop choices whose slot did not survive as a LogoSlot.

        Switching a slot back to None/Text after picking an image — or
        disabling the whole side — must not leave a pending entry, or
        LayoutManager.save would copy an image for a slot the layout
        does not have.
        """
        for slot_id in list(self._pending_logos):
            prefix, _, key = slot_id.partition(".")
            if prefix == "header":
                config = self._result_header
            elif prefix == "footer":
                config = self._result_footer
            else:
                config = None
            slot = (
                getattr(config, key)
                if config is not None and key in _SLOT_SIDES
                else None
            )
            if not isinstance(slot, LogoSlot):
                del self._pending_logos[slot_id]
