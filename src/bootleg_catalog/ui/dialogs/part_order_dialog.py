"""Part order dialog — which of two files is part 1.

Shows the two candidate filenames as radio buttons with their full
paths as grey subtitles.  The caller reads ``first_index`` after
``exec()``: ``0``/``1`` on Accept, ``None`` on Cancel/close.

Pure UI: no probing, no session, no Catalog/SQLite.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class PartOrderDialog(QDialog):
    """Ask the user which of two files should be treated as part 1."""

    def __init__(
        self,
        paths: tuple[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(paths, tuple) or len(paths) != 2:
            raise ValueError(f"paths must be a 2-tuple, got: {paths!r}")

        self.setWindowTitle("Choose the First Part")
        self.setMinimumSize(420, 220)
        self._cancelled = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Which file is the first part?", self))

        self._radios: list[QRadioButton] = []
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, path in enumerate(paths):
            radio = QRadioButton(Path(path).name, self)
            radio.setObjectName("partRadio")
            if index == 0:
                radio.setChecked(True)
            subtitle = QLabel(path, self)
            subtitle.setObjectName("partSubtitle")
            # Small grey subtitle showing the full path.
            subtitle.setStyleSheet("color: #888888; font-size: 11px;")
            group.addButton(radio, index)
            layout.addWidget(radio)
            layout.addWidget(subtitle)
            self._radios.append(radio)

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
    def first_index(self) -> int | None:
        """0 if the first path is part 1, 1 if the second is, None if cancelled."""
        if self._cancelled:
            return None
        return 1 if self._radios[1].isChecked() else 0

    # -- Cancellation tracking -----------------------------------------------

    def reject(self) -> None:
        self._cancelled = True
        super().reject()

    def done(self, code: int) -> None:
        if code == int(QDialog.DialogCode.Rejected):
            self._cancelled = True
        super().done(code)
