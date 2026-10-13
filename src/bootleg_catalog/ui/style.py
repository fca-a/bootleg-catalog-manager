"""Restrained desktop styling."""

APP_STYLESHEET = """
QMainWindow, QDialog, QWidget {
    color: #1f1f1f;
}
QMainWindow, QDialog {
    background: #f0f0f0;
}
QWidget {
    font-size: 12px;
}
QLabel {
    color: #1f1f1f;
}
QLabel#panelHeading {
    font-weight: 600;
    padding-bottom: 2px;
}
QLabel#emptyState {
    color: #444444;
    font-size: 13px;
    padding: 24px;
    background: #ffffff;
}
QMenuBar {
    background: #f7f7f7;
    color: #1f1f1f;
    border-bottom: 1px solid #d4d4d4;
}
QMenuBar::item:selected {
    background: #dde8f5;
}
QMenu {
    background: #ffffff;
    color: #1f1f1f;
    border: 1px solid #c8c8c8;
}
QMenu::item:selected {
    background: #cde4f7;
    color: #000000;
}
QToolBar {
    background: #ececec;
    border: none;
    border-bottom: 1px solid #d0d0d0;
    spacing: 6px;
    padding: 2px 6px;
}
QToolBar QToolButton {
    padding: 3px 8px;
    color: #1f1f1f;
}
QStatusBar {
    background: #ececec;
    color: #1f1f1f;
    border-top: 1px solid #d0d0d0;
}
QTableView {
    background: #ffffff;
    color: #1f1f1f;
    alternate-background-color: #f7f7f7;
    gridline-color: #e2e2e2;
    selection-background-color: #cde4f7;
    selection-color: #000000;
}
QHeaderView::section {
    background: #ececec;
    color: #1f1f1f;
    border: 1px solid #d4d4d4;
    padding: 3px 6px;
    font-weight: 600;
}
QListWidget, QPlainTextEdit, QLineEdit, QComboBox {
    background: #ffffff;
    color: #1f1f1f;
    border: 1px solid #c8c8c8;
}
QListWidget::item:selected {
    background: #cde4f7;
    color: #000000;
}
QScrollArea {
    background: #f0f0f0;
    border: none;
}
QPushButton {
    background: #f5f5f5;
    color: #1f1f1f;
    border: 1px solid #b5b5b5;
    padding: 3px 10px;
}
QPushButton:hover {
    background: #ececec;
}
QPushButton:disabled {
    color: #888888;
    background: #f0f0f0;
}
QGroupBox {
    font-weight: 600;
    color: #1f1f1f;
    background: #f0f0f0;
    border: 1px solid #d0d0d0;
    margin-top: 10px;
    padding-top: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: #1f1f1f;
}
QSplitter::handle {
    background: #d4d4d4;
}
"""
