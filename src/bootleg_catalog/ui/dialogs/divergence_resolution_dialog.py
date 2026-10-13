"""Divergence resolution dialog — pick which part wins.

Presents one row per divergent technical field between two probed
parts: the field label and two radios (part A / part B), defaulting to
part A.  The caller reads ``resolutions`` after ``exec()``: a mapping
of field name to ``0`` (part A) or ``1`` (part B) on Accept, ``None``
on Cancel/close.

Pure UI: no probing, no session, no Catalog/SQLite.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from bootleg_catalog.core.multi_part import PartTech

_FIELD_LABELS = {
    "resolution": "Resolution",
    "video_codec": "Video Codec",
    "audio_codec": "Audio Codec",
}
_ALLOWED_FIELDS = frozenset(_FIELD_LABELS)
# Deterministic row order regardless of frozenset iteration order.
_FIELD_ORDER = ("resolution", "video_codec", "audio_codec")


class DivergenceResolutionDialog(QDialog):
    """Ask the user how to resolve divergent technical fields between two parts."""

    def __init__(
        self,
        part_a: PartTech,
        part_b: PartTech,
        divergent: frozenset[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not divergent:
            raise ValueError("divergent must contain at least one field")
        unknown = set(divergent) - _ALLOWED_FIELDS
        if unknown:
            raise ValueError(f"Unknown divergent field(s): {sorted(unknown)}")

        self.setWindowTitle("Resolve Differences")
        self.setMinimumSize(520, 300)
        self._cancelled = False
        self._part_a = part_a
        self._part_b = part_b

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "The two parts report different values. "
                "Choose which part to use.",
                self,
            )
        )

        self._radios: dict[str, tuple[QRadioButton, QRadioButton]] = {}
        for field in _FIELD_ORDER:
            if field not in divergent:
                continue
            container = QWidget(self)
            field_layout = QVBoxLayout(container)
            field_layout.setContentsMargins(0, 4, 0, 4)
            field_layout.addWidget(QLabel(_FIELD_LABELS[field], container))

            radio_a = QRadioButton(
                f"Part A: {self._value_text(field, part_a)}", container
            )
            radio_b = QRadioButton(
                f"Part B: {self._value_text(field, part_b)}", container
            )
            radio_a.setChecked(True)  # default = part A
            group = QButtonGroup(container)
            group.setExclusive(True)
            group.addButton(radio_a, 0)
            group.addButton(radio_b, 1)
            field_layout.addWidget(radio_a)
            field_layout.addWidget(radio_b)

            layout.addWidget(container)
            self._radios[field] = (radio_a, radio_b)

        layout.addStretch()

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    # -- Result accessors ----------------------------------------------------

    @property
    def resolutions(self) -> dict[str, int] | None:
        """Mapping of field name -> 0 (use part_a) or 1 (use part_b).
        Contains one entry per divergent field on Accept, None on Cancel."""
        if self._cancelled:
            return None
        return {
            field: 1 if radio_b.isChecked() else 0
            for field, (_radio_a, radio_b) in self._radios.items()
        }

    # -- Cancellation tracking -----------------------------------------------

    def reject(self) -> None:
        self._cancelled = True
        super().reject()

    def done(self, code: int) -> None:
        if code == int(QDialog.DialogCode.Rejected):
            self._cancelled = True
        super().done(code)

    # -- Value formatting ----------------------------------------------------

    @staticmethod
    def _value_text(field: str, part: PartTech) -> str:
        """Human-readable value for *field* on *part* ('unknown' if None)."""
        if field == "resolution":
            if part.video_width is None or part.video_height is None:
                return "unknown"
            return f"{part.video_width}\u00d7{part.video_height}"
        value = getattr(part, field)
        return value if value is not None else "unknown"
