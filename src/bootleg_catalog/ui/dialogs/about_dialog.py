from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from bootleg_catalog import __version__


class AboutDialog(QMessageBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Bootleg Catalog Manager")
        self.setIcon(QMessageBox.Icon.Information)
        self.setText("Bootleg Catalog Manager")
        self.setInformativeText(
            f"Version {__version__}\n\n"
            "A local catalog for bootleg, concert, performance, and broadcast recordings.\n"
            "Media files stay on disk. The catalog stores references only."
        )
        self.setStandardButtons(QMessageBox.StandardButton.Ok)
