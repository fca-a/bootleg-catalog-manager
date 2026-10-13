"""Rip metadata dialog — format label + duration for "+ Add from Folder…".

Opens after the user confirms a rip scan and before the rip's first
video is probed.  The chosen format label becomes the recording's
``extension`` value (free text, no dot prefix — it is a label, not a
file extension); the duration is optional free text, because FFprobe on
the first VOB yields only a partial chunk of the title, so the real
total cannot be auto-computed.  Left empty, the recording's duration
stays ``None`` and the user can fill it in later.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

#: Suggested format labels; ``_DEFAULT_FORMAT`` is the initial choice.
FORMAT_SUGGESTIONS = ("VOB + Small files", "DVD", "VOB with Smalls", "VIDEO_TS")
_DEFAULT_FORMAT = "VIDEO_TS"


def parse_duration_text(text: str) -> float | None:
    """Parse ``"H:MM:SS"``, ``"MM:SS"`` or plain seconds into seconds.

    Returns ``None`` for empty or unparseable text — the recording's
    duration stays ``None`` and the user can fix it later.  Seconds is
    the unit everywhere else (``ExportRecording.duration``, the probed
    FFprobe values, ``format_duration``).
    """
    stripped = text.strip()
    if not stripped:
        return None
    parts = stripped.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 1:
        total = numbers[0]
    elif len(numbers) == 2:
        total = numbers[0] * 60 + numbers[1]
    elif len(numbers) == 3:
        total = numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    else:
        return None
    return total if total >= 0 else None


class RipMetadataDialog(QDialog):
    """Choose the rip's format label and (optionally) its total duration.

    ``result_format`` / ``result_duration_text`` return the choices
    after ``Accepted`` and ``None`` otherwise; the duration text is the
    raw string — parsing is the caller's concern
    (:func:`parse_duration_text`).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rip Details")

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Choose the format label and total duration for this rip.",
                self,
            ),
        )

        form = QFormLayout()
        self._format_combo = QComboBox(self)
        self._format_combo.setObjectName("formatCombo")
        self._format_combo.addItems(list(FORMAT_SUGGESTIONS))
        self._format_combo.setCurrentText(_DEFAULT_FORMAT)
        form.addRow("Format", self._format_combo)

        self._duration_edit = QLineEdit(self)
        self._duration_edit.setObjectName("durationEdit")
        self._duration_edit.setPlaceholderText(
            "e.g. 1:30:00 (leave empty if unknown)",
        )
        form.addRow("Duration", self._duration_edit)
        layout.addLayout(form)

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
    def result_format(self) -> str | None:
        """The chosen format label after ``Accepted``; ``None`` otherwise."""
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        return self._format_combo.currentText()

    @property
    def result_duration_text(self) -> str | None:
        """The raw duration text after ``Accepted``; ``None`` otherwise.

        May be an empty string on accept — the user left it blank.
        """
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        return self._duration_edit.text()
