"""Read-only table model over export-session recordings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt

from bootleg_catalog.models.export_recording import ExportRecording


class RecordingTableModel(QAbstractTableModel):
    """Read-only table model over a list of ExportRecording objects.

    Backs the recordings ``QTableView`` in :class:`ExportPreviewWindow`.
    ``set_recordings`` keeps a reference to the caller's list (it does not
    copy it); every column exposes its row's ``ExportRecording`` through
    ``Qt.ItemDataRole.UserRole``.  Rows are selectable but not editable.
    """

    COLUMNS: tuple[str, ...] = ("File", "Show", "Title", "Status")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._recordings: list[ExportRecording] = []

    def set_recordings(self, recordings: list[ExportRecording]) -> None:
        """Replace the rows (reference kept, not copied) and reset the model."""
        self.beginResetModel()
        self._recordings = recordings
        self.endResetModel()

    def recording_at(self, row: int) -> ExportRecording | None:
        """Return the recording at *row*, or ``None`` when out of range."""
        if 0 <= row < len(self._recordings):
            return self._recordings[row]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._recordings)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(  # noqa: N802
        self,
        index: QModelIndex,
        role: Qt.ItemDataRole = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not index.isValid():
            return None
        rec = self.recording_at(index.row())
        if rec is None:
            return None
        if role == Qt.ItemDataRole.UserRole:
            return rec
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        column = index.column()
        if column == 0:
            return Path(rec.source_path).name
        if column == 1:
            return rec.show or "\u2014"
        if column == 2:
            return rec.title or "\u2014"
        if column == 3:
            return "Error" if rec.probe_error else "OK"
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # noqa: N802
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: Qt.ItemDataRole = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(
            self.COLUMNS,
        ):
            return self.COLUMNS[section]
        return None
