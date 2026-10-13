from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from bootleg_catalog import __version__


class BootlegCatalogApplication(QApplication):
    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self.setApplicationName("Bootleg Catalog Manager")
        self.setOrganizationName("Bootleg Catalog Manager")
        self.setApplicationVersion(__version__)
        self.setStyle("Fusion")
        hints = self.styleHints()
        if hints is not None:
            hints.setColorScheme(Qt.ColorScheme.Light)
        font = QFont()
        if sys.platform == "win32":
            font.setFamily("Segoe UI")
        elif sys.platform == "darwin":
            font.setFamily("Helvetica Neue")
        else:
            font.setFamily("Noto Sans")
        font.setPointSize(9)
        self.setFont(font)
        self.setPalette(_light_palette())


def _light_palette() -> QPalette:
    palette = QPalette()
    window = QColor(240, 240, 240)
    base = QColor(255, 255, 255)
    text = QColor(31, 31, 31)
    button = QColor(245, 245, 245)
    highlight = QColor(205, 228, 247)
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(247, 247, 247))
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, button)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, base)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(110, 110, 110))
    palette.setColor(QPalette.ColorRole.Light, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Midlight, QColor(230, 230, 230))
    palette.setColor(QPalette.ColorRole.Mid, QColor(196, 196, 196))
    palette.setColor(QPalette.ColorRole.Dark, QColor(160, 160, 160))
    palette.setColor(QPalette.ColorRole.Shadow, QColor(120, 120, 120))
    return palette
