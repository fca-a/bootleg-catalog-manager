"""Startup chooser dialog — first screen of the export-first flow."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class StartupChooserDialog(QDialog):
    """Choose how to start: a new export session or a recent one.

    ``choice`` is :attr:`NEW` or :attr:`CONTINUE` after the dialog was
    accepted, and ``None`` when it was cancelled (the app exits).
    """

    NEW = "new"
    CONTINUE = "continue"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bootleg Catalog Manager")
        self.setMinimumSize(360, 200)
        self._choice: str | None = None

        layout = QVBoxLayout(self)

        description = QLabel("Choose how to start.")
        layout.addWidget(description)

        self._new_btn = QPushButton("New export session")
        self._new_btn.clicked.connect(self._on_new)
        layout.addWidget(self._new_btn)

        self._continue_btn = QPushButton("Continue a recent session")
        self._continue_btn.clicked.connect(self._on_continue)
        layout.addWidget(self._continue_btn)

        layout.addStretch()

        cancel_row = QHBoxLayout()
        cancel_row.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        cancel_row.addWidget(self._cancel_btn)
        layout.addLayout(cancel_row)

    @property
    def choice(self) -> str | None:
        """NEW or CONTINUE after Accepted; None if cancelled."""
        return self._choice

    def _on_new(self) -> None:
        self._choice = self.NEW
        self.accept()

    def _on_continue(self) -> None:
        self._choice = self.CONTINUE
        self.accept()
