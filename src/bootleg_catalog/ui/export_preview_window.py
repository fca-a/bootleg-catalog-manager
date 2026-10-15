"""Export Preview window.

Non-modal main window that shows a live preview of an ExportSession and
provides Add/Edit/Remove/Export actions plus session persistence
(New / Open / Save / Save As).  Completely isolated from the catalog,
repository, SQLite, and Recording.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QModelIndex, QThread, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from bootleg_catalog.core.export_session import ExportSessionError
from bootleg_catalog.core.export_session_service import (
    ExportSessionService,
    ExportSessionServiceError,
)
from bootleg_catalog.core.layout_manager import (
    LayoutManager,
    LayoutManagerError,
    default_layout,
)
from bootleg_catalog.core.layout_pdf_exporter import export_layout_pdf
from bootleg_catalog.core.layout_xlsx_exporter import LayoutXLSXExportError, export_layout_xlsx
from bootleg_catalog.core.multi_part import aggregate_parts, apply_resolutions
from bootleg_catalog.core.recent_sessions import RecentSessions
from bootleg_catalog.core.rip_scanner import RipScanResult
from bootleg_catalog.core.session_store import SessionStoreError
from bootleg_catalog.core.settings_service import load_settings, save_settings
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.ui.dialogs.about_dialog import AboutDialog
from bootleg_catalog.ui.dialogs.divergence_resolution_dialog import (
    DivergenceResolutionDialog,
)
from bootleg_catalog.ui.dialogs.export_recording_dialog import (
    AddRecordingDialog,
    EditExportRecordingDialog,
)
from bootleg_catalog.ui.dialogs.layout_designer_dialog import LayoutDesignerDialog
from bootleg_catalog.ui.dialogs.part_order_dialog import PartOrderDialog
from bootleg_catalog.ui.dialogs.rip_metadata_dialog import (
    RipMetadataDialog,
    parse_duration_text,
)
from bootleg_catalog.ui.dialogs.screenshot_manager_dialog import ScreenshotManagerDialog
from bootleg_catalog.ui.dialogs.settings_dialog import SettingsDialog
from bootleg_catalog.ui.widgets.export_preview_widget import PreviewWidget
from bootleg_catalog.ui.widgets.recording_table_model import RecordingTableModel
from bootleg_catalog.ui.workers.probe_worker import ProbeWorker
from bootleg_catalog.ui.workers.rip_scan_worker import BatchProbeWorker, RipScanWorker
from bootleg_catalog.utils.logging import get_logger
from bootleg_catalog.utils.paths import format_file_size

# Characters forbidden in Windows filenames (also problematic on other
# OSes when files are moved across).
_FORBIDDEN_CHARS = frozenset('\\/:*?"<>|')


def _plural(count: int, singular: str, plural: str) -> str:
    """Return *singular* for a count of 1, otherwise *plural*."""
    return singular if count == 1 else plural


#: File-dialog filter for "+ Add Audio Recording…" single-file mode
#: (mirrors the video filter on _on_add_recording's picker).
_AUDIO_FILE_FILTER = (
    "Audio files (*.mp3 *.flac *.m4a *.aac *.ogg *.opus *.wav *.wma "
    "*.aiff *.ape *.alac);;All files (*)"
)


class AddMediaSourceDialog(QDialog):
    """Ask whether a new recording comes from a single file or a folder.

    Shared by "+ Add Video Recording…" and "+ Add Audio Recording…";
    *kind* only decides the wording — and that video's folder pick is
    the rip scanner.  :attr:`result_choice` returns ``"file"`` or
    ``"folder"`` after ``Accepted`` and ``None`` otherwise, mirroring
    :class:`RipMetadataDialog`'s result-accessor style.
    """

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._choice: str | None = None
        self.setWindowTitle(f"Add {kind} recording")

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"Add a {kind} recording from a single file or a folder.",
                self,
            ),
        )

        self._buttons = QDialogButtonBox(parent=self)
        file_btn = self._buttons.addButton(
            "Single file\u2026",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        folder_label = "Rip Folder\u2026" if kind == "video" else "Folder\u2026"
        folder_btn = self._buttons.addButton(
            folder_label,
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        # One click = one decision: the choice is recorded and the
        # dialog accepted right away, so ``accepted`` needs no wiring.
        file_btn.clicked.connect(
            lambda _checked=False: self._choose("file"),
        )
        folder_btn.clicked.connect(
            lambda _checked=False: self._choose("folder"),
        )
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _choose(self, choice: str) -> None:
        """Record the pick and accept."""
        self._choice = choice
        self.accept()

    @property
    def result_choice(self) -> str | None:
        """``"file"``/``"folder"`` after ``Accepted``; ``None`` otherwise."""
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        return self._choice


class ExportPreviewWindow(QMainWindow):
    """Main window for previewing and exporting an export session.

    Parameters
    ----------
    service:
        An initialized :class:`ExportSessionService`.
    parent:
        Parent widget.
    """

    edit_layout_requested = Signal()

    def __init__(
        self,
        service: ExportSessionService,
        parent: QWidget | None = None,
        *,
        initial_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumSize(900, 650)

        self._service = service
        self._current_path: Path | None = (
            Path(initial_path) if initial_path is not None else None
        )

        # Background probe state — one operation at a time;
        # the toolbar stays locked while a probe is running.
        self._probe_thread: QThread | None = None
        self._probe_worker: ProbeWorker | None = None

        # Rip scan + batch probe state, same single-operation
        # rule: the toolbar stays locked until the flow finishes.
        self._rip_thread: QThread | None = None
        self._rip_worker: RipScanWorker | None = None
        self._batch_thread: QThread | None = None
        self._batch_worker: BatchProbeWorker | None = None
        # Multi-part add state: two files probed as one
        # recording, same single-operation rule as the other threads.
        self._multipart_thread: QThread | None = None
        self._multipart_worker: BatchProbeWorker | None = None
        # Rip accepted by the user, waiting for its single probe to
        # finish (one recording per rip).  ``None`` outside
        # that window.
        self._pending_rip: RipScanResult | None = None
        # Format label + user-typed duration from RipMetadataDialog,
        # stashed together with the pending rip.  The probed
        # duration of the first VOB is a partial chunk and never used.
        self._pending_rip_format: str | None = None
        self._pending_rip_duration: float | None = None

        # --- Central widget: toolbar row, table, preview (unchanged layout) ---
        central = QWidget(self)
        layout = QVBoxLayout(central)

        # --- Toolbar ---
        toolbar = QHBoxLayout()

        self._add_btn = QPushButton("+ Add Video Recording\u2026")
        self._add_btn.clicked.connect(self._on_add_video_recording)
        toolbar.addWidget(self._add_btn)

        self._add_audio_btn = QPushButton("+ Add Audio Recording\u2026")
        self._add_audio_btn.clicked.connect(self._on_add_audio_recording)
        toolbar.addWidget(self._add_audio_btn)

        self._add_folder_btn = QPushButton("+ Add from Folder\u2026")
        self._add_folder_btn.clicked.connect(self._on_add_from_folder)
        toolbar.addWidget(self._add_folder_btn)

        self._edit_layout_btn = QPushButton("Edit Layout\u2026")
        self._edit_layout_btn.clicked.connect(self.edit_layout_requested)
        self._edit_layout_btn.clicked.connect(self._on_edit_layout)
        toolbar.addWidget(self._edit_layout_btn)

        self._edit_btn = QPushButton("Edit Recording\u2026")
        self._edit_btn.clicked.connect(self._on_edit_selected)
        self._edit_btn.setEnabled(False)
        toolbar.addWidget(self._edit_btn)

        self._remove_btn = QPushButton("Remove Recording")
        self._remove_btn.clicked.connect(self._on_remove_selected)
        # _on_remove_selected only runs while the table has
        # focus (so Delete/Backspace never fire mid-edit elsewhere), so
        # the button must not steal focus when clicked.
        self._remove_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._remove_btn.setEnabled(False)
        toolbar.addWidget(self._remove_btn)

        # Manual screenshot attachment; selection-driven like
        # Edit/Remove.
        self._screenshots_btn = QPushButton("Screenshots\u2026")
        self._screenshots_btn.clicked.connect(self._on_manage_screenshots)
        self._screenshots_btn.setEnabled(False)
        toolbar.addWidget(self._screenshots_btn)

        toolbar.addStretch()

        self._export_pdf_btn = QPushButton("Export PDF\u2026")
        self._export_pdf_btn.clicked.connect(self._on_export_pdf)
        toolbar.addWidget(self._export_pdf_btn)

        self._export_xlsx_btn = QPushButton("Export XLSX\u2026")
        self._export_xlsx_btn.clicked.connect(self._on_export_xlsx)
        toolbar.addWidget(self._export_xlsx_btn)

        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.close)
        toolbar.addWidget(self._close_btn)

        self._busy_label = QLabel("")
        self._busy_label.hide()
        toolbar.addWidget(self._busy_label)

        toolbar_container = QWidget(self)
        toolbar_container.setLayout(toolbar)
        layout.addWidget(toolbar_container)
        self._toolbar = toolbar_container

        # --- Recordings table ---
        self._table_model = RecordingTableModel(self)
        self._table = QTableView(self)
        self._table.setModel(self._table_model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMaximumHeight(200)
        self._table.selectionModel().selectionChanged.connect(
            self._on_selection_changed,
        )
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self._table)

        # Delete/Backspace remove the selected rows. The slot
        # itself guards on table focus, so these are inert while the
        # user is editing text elsewhere.
        self._delete_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self)
        self._delete_shortcut.activated.connect(self._on_remove_selected)
        self._backspace_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Backspace), self,
        )
        self._backspace_shortcut.activated.connect(self._on_remove_selected)

        # --- Preview area ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._preview_widget = PreviewWidget()
        scroll.setWidget(self._preview_widget)
        layout.addWidget(scroll, 1)

        self.setCentralWidget(central)

        self._build_menus()

        # Initial refresh (also sets the window title)
        self._refresh()

    # -- Menus --------------------------------------------------------------

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(
            self._action("New", self._on_new, QKeySequence.StandardKey.New),
        )
        file_menu.addAction(
            self._action("Open\u2026", self._on_open, QKeySequence.StandardKey.Open),
        )
        file_menu.addAction(
            self._action("Save", self._on_save, QKeySequence.StandardKey.Save),
        )
        file_menu.addAction(
            self._action(
                "Save As\u2026", self._on_save_as, QKeySequence.StandardKey.SaveAs,
            ),
        )
        file_menu.addSeparator()
        file_menu.addAction(
            self._action("Settings\u2026", self._on_settings),
        )
        file_menu.addAction(
            self._action("Exit", self.close, QKeySequence.StandardKey.Quit),
        )

        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction(self._action("About", self._on_about))

    def _action(self, text: str, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(shortcut)
        return action

    # -- Title / dirty state ------------------------------------------------

    def _update_title(self) -> None:
        name = self._service.display_name or "Untitled"
        title = f"{name} \u2014 Bootleg Catalog Manager"
        if self._service.is_dirty():
            title += " *"
        self.setWindowTitle(title)

    def _confirm_discard(self) -> bool:
        """Three-button Save/Discard/Cancel prompt; ``True`` means proceed.

        Non-dirty sessions proceed without a prompt.  On Save the session
        is saved first; if that save did not actually happen (dialog
        cancelled or an error) the answer is ``False``.
        """
        if not self._service.is_dirty():
            return True
        result = QMessageBox.question(
            self,
            "Unsaved Changes",
            "This session has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Save:
            self._on_save()
            return not self._service.is_dirty()
        return result == QMessageBox.StandardButton.Discard

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._background_running():
            QMessageBox.information(
                self,
                "Please Wait",
                "An operation is still running. "
                "Wait for it to finish before closing.",
            )
            event.ignore()
            return
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

    def _background_running(self) -> bool:
        """True while any background worker thread is still running."""
        for thread in (
            self._probe_thread,
            self._rip_thread,
            self._batch_thread,
            self._multipart_thread,
        ):
            if thread is not None and thread.isRunning():
                return True
        return False

    # -- Session file operations -------------------------------------------

    def _manual_screenshot_paths(self) -> set[str]:
        """Union of manual screenshot paths across all recordings."""
        paths: set[str] = set()
        for rec in self._service.session.recordings:
            paths.update(rec.manual_paths)
        return paths

    def _all_screenshot_paths(self) -> set[str]:
        """Union of every screenshot path (manual and auto) across recordings.

        Passed as ``embed_paths`` when the user opts to embed all
        screenshots, making the ``.bootleg`` self-contained.
        """
        paths: set[str] = set()
        for rec in self._service.session.recordings:
            paths.update(rec.screenshot_paths)
        return paths

    def _on_save(self) -> None:
        """Save to the current path, falling through to Save As if none."""
        if self._current_path is None:
            self._on_save_as()
            return
        try:
            self._service.save(
                self._current_path,
                embed_paths=self._manual_screenshot_paths(),
            )
        except SessionStoreError as exc:
            QMessageBox.warning(self, "Save Error", str(exc))
            return
        self._update_title()
        RecentSessions().add(self._service.display_name, self._current_path)

    def _on_save_as(self) -> None:
        """Prompt for a destination and save the session there."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Session As",
            self._suggested_session_filename(),
            "Bootleg sessions (*.bootleg)",
        )
        if not path:
            return  # cancelled — _current_path unchanged
        if not path.endswith(".bootleg"):
            path += ".bootleg"

        if not self._service.display_name:
            # A display name is required for an unnamed session; an empty
            # or cancelled result aborts the save.  Named sessions are
            # never prompted here — renaming belongs to an explicit
            # Rename action.
            name, ok = QInputDialog.getText(
                self, "Session Name", "Display name:", text=Path(path).stem,
            )
            if not ok or not name.strip():
                return
            self._service.set_display_name(name.strip())
            self._update_title()

        # Ask whether to embed every screenshot (self-contained file) or
        # keep the current behaviour (embed only manual attachments and
        # reference the auto sidecars by path).
        embed_reply = QMessageBox.question(
            self,
            "Embed Screenshots",
            "Embed all screenshots in the session file?\n\n"
            "Embedding makes the .bootleg self-contained, but the file "
            "can grow significantly if the session has many "
            "screenshots.\n\n"
            "If you choose No, screenshots are referenced by their "
            "current paths. They will be lost if the original files "
            "move or if the application cache is cleared.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if embed_reply == QMessageBox.StandardButton.Cancel:
            return  # cancelled — no file written, _current_path unchanged
        if embed_reply == QMessageBox.StandardButton.Yes:
            embed_paths = self._all_screenshot_paths()
        else:
            # No: keep the existing behaviour — embed only the manually
            # attached screenshots, reference the auto sidecars.
            embed_paths = self._manual_screenshot_paths()

        try:
            self._service.save(path, embed_paths=embed_paths)
        except SessionStoreError as exc:
            QMessageBox.warning(self, "Save Error", str(exc))
            return  # _current_path unchanged
        self._current_path = Path(path)
        self._update_title()
        RecentSessions().add(self._service.display_name, self._current_path)

    def _on_open(self) -> None:
        """Open an existing .bootleg session file."""
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Session", "", "Bootleg sessions (*.bootleg)",
        )
        if not path:
            return
        try:
            self._service.load(path)
        except (SessionStoreError, ExportSessionServiceError) as exc:
            QMessageBox.warning(self, "Open Error", str(exc))
            return
        self._current_path = Path(path)
        self._refresh()
        RecentSessions().add(self._service.display_name, self._current_path)

    def _on_new(self) -> None:
        """Replace the session with a blank one (fresh default layout)."""
        if not self._confirm_discard():
            return
        self._service.clear()
        self._service.set_layout(default_layout())
        self._service.set_display_name("")
        self._current_path = None
        self._service.mark_clean()
        self._refresh()

    def _on_about(self) -> None:
        AboutDialog(self).exec()

    def _on_settings(self) -> None:
        """Open the Settings dialog; save on accept (no restart needed).

        The running service keeps its current ffprobe_path — only new
        probes pick up a changed value.
        """
        settings = load_settings()
        dialog = SettingsDialog(settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            save_settings(dialog.settings)

    @staticmethod
    def _sanitize_display_name(name: str) -> str:
        """Inline copy of the old MainWindow filename-sanitising rules."""
        stem = Path(name).stem  # drop extension
        cleaned = "".join("_" if c in _FORBIDDEN_CHARS else c for c in stem)
        cleaned = cleaned.strip(". ")
        return cleaned

    def _suggested_session_filename(self) -> str:
        """Default ``.bootleg`` filename derived from the display name."""
        raw = self._sanitize_display_name(self._service.display_name or "session")
        return str(Path(raw or "session").with_suffix(".bootleg"))

    # -- Refresh -----------------------------------------------------------

    def _refresh(self) -> None:
        """Rebuild the table and preview from the current session state."""
        self._refresh_table()
        self._refresh_preview()
        self._update_title()

    def _refresh_table(self) -> None:
        """Reload the table model from the session state."""
        recordings = list(self._service.session.recordings)
        self._table_model.set_recordings(recordings)
        self._table.resizeColumnsToContents()
        self._sync_action_buttons()

    def _refresh_preview(self) -> None:
        """Rebuild the preview widget from the current document."""
        try:
            document = self._service.build_document()
            self._preview_widget.set_document(document)
        except Exception:
            # Errors from the session service should not crash the preview
            pass

    def _on_selection_changed(self, _selected, _deselected) -> None:
        """Keep the Edit/Remove toolbar buttons in sync with the selection."""
        self._sync_action_buttons()

    def _sync_action_buttons(self) -> None:
        count = len(self._selected_recordings())
        self._edit_btn.setEnabled(count == 1)
        self._remove_btn.setEnabled(count >= 1)
        self._screenshots_btn.setEnabled(count == 1)

    def _selected_recording(self) -> ExportRecording | None:
        selection = self._table.selectionModel()
        if selection is None:
            return None
        rows = selection.selectedRows()
        if not rows:
            return None
        return self._table_model.recording_at(rows[0].row())

    def _selected_recordings(self) -> list[ExportRecording]:
        """Return the selected recordings in display order (row 0 first)."""
        selection = self._table.selectionModel()
        if selection is None:
            return []
        rows = sorted(index.row() for index in selection.selectedRows())
        recordings: list[ExportRecording] = []
        for row in rows:
            rec = self._table_model.recording_at(row)
            if rec is not None:
                recordings.append(rec)
        return recordings

    def _on_edit_selected(self) -> None:
        rec = self._selected_recording()
        if rec is not None:
            self._on_edit_recording(rec.source_path)

    def _on_remove_selected(self) -> None:
        """Confirm once, then remove every selected recording.

        Runs only while the table has focus so the Delete/Backspace
        shortcuts cannot fire while the user is editing text elsewhere.
        """
        if not self._table.hasFocus():
            return
        recordings = self._selected_recordings()
        if not recordings:
            return
        if len(recordings) == 1:
            text = "Remove this recording?"
        else:
            text = f"Remove {len(recordings)} recordings?"
        reply = QMessageBox.question(
            self,
            "Remove Recording",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for rec in recordings:
            try:
                self._service.remove_recording(rec.source_path)
            except Exception:
                pass
        self._refresh()

    def _on_manage_screenshots(self) -> None:
        """Open the Screenshot Manager for the selected recording.

        On Accepted the dialog's results replace the recording via
        ``service.update_recording`` and the table is refreshed so the
        model picks up the replaced object.
        """
        rec = self._selected_recording()
        if rec is None:
            return
        dialog = ScreenshotManagerDialog(rec, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = replace(
            rec,
            screenshot_paths=dialog.result_screenshot_paths,
            manual_paths=dialog.result_manual_paths,
            screenshot_split=dialog.result_screenshot_split,
        )
        self._service.update_recording(rec.source_path, updated)
        self._refresh()

    def _on_row_double_clicked(self, index: QModelIndex) -> None:
        rec = self._table_model.recording_at(index.row())
        if rec is not None:
            self._on_edit_recording(rec.source_path)

    # -- Add Recording flow ------------------------------------------------

    def _on_add_recording(self) -> None:
        """Open the multi-file picker and route 1-2 files to a probe flow.

        One file follows the original single-probe path; two files open
        the part-order dialog then probe both as one recording; three or
        more are refused (a recording holds at most two parts).
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Media File",
            "",
            "Video files (*.mkv *.mp4 *.avi *.mov *.ts *.flv *.webm);;All files (*)",
        )
        if not paths:
            return
        if len(paths) == 1:
            self._start_background_probe(paths[0])
            return
        if len(paths) == 2:
            self._start_multipart_add(paths)
            return
        QMessageBox.information(
            self,
            "Too Many Files",
            "You can add at most 2 files per recording. "
            "To add more, create separate recordings.",
        )

    # -- Add Video / Audio routing ------------------------------------------

    def _on_add_video_recording(self) -> None:
        """File or folder?  Route to the existing video flows unchanged."""
        choice = self._ask_media_source("video")
        if choice == "file":
            self._on_add_recording()
        elif choice == "folder":
            self._on_add_from_folder()

    def _on_add_audio_recording(self) -> None:
        """File or folder?  Probe audio, then run the shared add dialog.

        The probe runs synchronously — one pass per track at most — and
        failures (missing path, no audio files) surface as a warning
        instead of escaping the Qt slot.
        """
        choice = self._ask_media_source("audio")
        if choice == "file":
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select Audio File",
                "",
                _AUDIO_FILE_FILTER,
            )
        elif choice == "folder":
            path = QFileDialog.getExistingDirectory(
                self,
                "Select Album Folder",
                "",
            )
        else:
            return  # cancelled: result_choice is None
        if not path:
            return
        try:
            recording = self._service.probe_audio_recording_from_path(path)
        except ExportSessionServiceError as exc:
            QMessageBox.warning(self, "Audio Probe Error", str(exc))
            return
        self._show_add_dialog(recording)

    def _ask_media_source(self, kind: str) -> str | None:
        """Open the file-or-folder chooser for *kind*; ``None`` on cancel."""
        dialog = AddMediaSourceDialog(kind, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.result_choice

    def _start_background_probe(self, path: str) -> None:
        """Probe *path* on a worker thread; results arrive via slots.

        The worker does not own the thread: ``started`` runs it,
        ``finished``/``failed`` quit it, and the thread's ``finished``
        signal releases both objects.
        """
        self._set_busy("Probing\u2026")
        thread = QThread(self)
        worker = ProbeWorker(self._service, path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_probe_finished)
        worker.failed.connect(self._on_probe_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._on_probe_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._probe_thread = thread
        self._probe_worker = worker
        thread.start()

    def _on_probe_finished(self, recording: ExportRecording) -> None:
        """Probe succeeded: run the add dialog on the main thread."""
        self._set_busy(None)
        self._show_add_dialog(recording)

    def _show_add_dialog(self, recording: ExportRecording) -> None:
        """Open AddRecordingDialog for an already-probed recording.

        Accepted → commit through the service (dirty flag set); rejected
        → the recording is NOT added.  Either way the preview refreshes.
        """
        settings = load_settings()
        dialog = AddRecordingDialog(recording, settings, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Route through the service so the dirty flag is set.
            try:
                self._service.add_recording(dialog.result_recording)
            except ExportSessionError:
                # Duplicate source_path: the session is unchanged; do not
                # let the error escape the Qt slot.
                QMessageBox.information(
                    self,
                    "Already in Session",
                    "This file is already in the session.",
                )
        # If rejected, the recording is NOT added.
        self._refresh()

    def _on_probe_failed(self, message: str) -> None:
        """Probe failed: unlock the UI and show the error."""
        self._set_busy(None)
        QMessageBox.warning(self, "Probe Error", message)

    def _on_probe_thread_finished(self) -> None:
        """Release the worker/thread references once the thread stopped."""
        self._probe_thread = None
        self._probe_worker = None

    # -- Multi-part add flow ------------------------------------------------

    def _start_multipart_add(self, paths: list[str]) -> None:
        """Ask for order, probe both files, then build a 2-part recording.

        The chosen order decides which file becomes part 1 (and thus the
        recording's ``source_path``): the worker probes the list as
        given, so reordering here is what makes the aggregate's first
        part the user's first part.
        """
        dialog = PartOrderDialog((paths[0], paths[1]), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        first = dialog.first_index
        if first is None:
            return  # cancelled: first_index is only None on cancel
        ordered = [paths[first], paths[1 - first]]
        self._set_busy("Probing 0 / 2\u2026")
        thread = QThread(self)
        worker = BatchProbeWorker(self._service, ordered)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_multipart_progress)
        worker.finished.connect(self._on_multipart_finished)
        worker.failed.connect(self._on_multipart_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._on_multipart_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._multipart_thread = thread
        self._multipart_worker = worker
        thread.start()

    def _on_multipart_progress(self, current: int, total: int) -> None:
        """Update the busy indicator with the two-file probe progress."""
        self._busy_label.setText(f"Probing {current} / {total}\u2026")

    def _on_multipart_finished(self, probeds: list, failed_paths: list) -> None:
        """Build the 2-part recording from two probed files.

        ``probeds``: list of ExportRecording (len 2 expected);
        ``failed_paths``: list[str].  Any probe failure aborts before
        the session is touched; divergent technical fields are resolved
        through a dialog before the recording is built and confirmed
        with :class:`AddRecordingDialog` (whose ``replace`` keeps
        ``parts`` intact).
        """
        # From here on the flow is modal dialogs, not background work.
        self._set_busy(None)
        if failed_paths or len(probeds) != 2:
            QMessageBox.warning(
                self,
                "Probe Error",
                "One or both files could not be probed. "
                "Add them individually to see the specific error.",
            )
            return
        aggregate = aggregate_parts(probeds)
        if aggregate.divergent:
            dialog = DivergenceResolutionDialog(
                aggregate.parts[0],
                aggregate.parts[1],
                aggregate.divergent,
                self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            aggregate = apply_resolutions(aggregate, dialog.resolutions)
        recording = ExportRecording(
            source_path=probeds[0].source_path,
            extension=probeds[0].extension,
            file_size=aggregate.file_size,
            duration=aggregate.duration,
            container_format=probeds[0].container_format,
            video_codec=aggregate.video_codec,
            video_width=aggregate.video_width,
            video_height=aggregate.video_height,
            frame_rate=aggregate.frame_rate,
            pixel_format=probeds[0].pixel_format,
            audio_codec=aggregate.audio_codec,
            audio_channels=aggregate.audio_channels,
            audio_sample_rate=aggregate.audio_sample_rate,
            audio_bitrate=aggregate.audio_bitrate,
            parts=(probeds[0].source_path, probeds[1].source_path),
        )
        settings = load_settings()
        dialog = AddRecordingDialog(recording, settings, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._service.add_recording(dialog.result_recording)
        except ExportSessionError:
            QMessageBox.information(
                self,
                "Already in Session",
                "This recording is already in the session.",
            )
            return
        self._refresh()

    def _on_multipart_failed(self, message: str) -> None:
        """Catastrophic multipart probe failure: unlock and show the error."""
        self._set_busy(None)
        QMessageBox.warning(self, "Probe Error", message)

    def _on_multipart_thread_finished(self) -> None:
        """Release the multipart probe thread/worker references once stopped."""
        self._multipart_thread = None
        self._multipart_worker = None

    def _set_busy(self, message: str | None) -> None:
        """Show *message* in the busy indicator and lock the toolbar.

        ``None`` hides the indicator and unlocks the toolbar.
        """
        if message is None:
            self._busy_label.setText("")
            self._busy_label.hide()
            self._toolbar.setEnabled(True)
        else:
            self._busy_label.setText(message)
            self._busy_label.show()
            self._toolbar.setEnabled(False)

    # -- Add from Folder flow ----------------------------------------------

    def _on_add_from_folder(self) -> None:
        """Pick a rip folder and scan it on a worker thread."""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Rip Folder",
            "",
        )
        if not directory:
            return
        self._set_busy("Scanning rip\u2026")
        thread = QThread(self)
        worker = RipScanWorker(directory)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_rip_scan_finished)
        worker.failed.connect(self._on_rip_scan_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._on_rip_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._rip_thread = thread
        self._rip_worker = worker
        thread.start()

    def _on_rip_scan_finished(self, result: RipScanResult) -> None:
        """Confirm the scan result and probe the rip as ONE recording.

        Accepting the rip first asks for the format label and total
        duration (RipMetadataDialog), then probes only
        ``video_files[0]`` for the technical fields; the recording's
        ``source_path`` becomes the rip root and ``file_size`` the total
        of every part (see ``_on_rip_probe_finished``).
        """
        self._set_busy(None)
        if not result.video_files:
            QMessageBox.information(
                self,
                "Add from Folder",
                "No video files found in this rip.",
            )
            return
        total = format_file_size(result.total_size_bytes)
        reply = QMessageBox.question(
            self,
            "Add from Folder",
            f"Found {len(result.video_files)} video files "
            f"(total: {total}). Add this rip to the session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        meta = RipMetadataDialog(self)
        if meta.exec() != QDialog.DialogCode.Accepted:
            return  # metadata cancelled: nothing is added
        self._pending_rip = result
        self._pending_rip_format = meta.result_format
        self._pending_rip_duration = parse_duration_text(
            meta.result_duration_text or "",
        )
        self._start_rip_probe(result.video_files[0])

    def _start_rip_probe(self, path: str) -> None:
        """Probe the rip's first video on a worker thread (single recording).

        Reuses the ProbeWorker pattern; ``finished`` routes to
        ``_on_rip_probe_finished``, which folds the result into one rip
        recording instead of adding one recording per file.
        """
        self._set_busy("Probing rip\u2026")
        thread = QThread(self)
        worker = ProbeWorker(self._service, path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_rip_probe_finished)
        worker.failed.connect(self._on_rip_probe_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._on_probe_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._probe_thread = thread
        self._probe_worker = worker
        thread.start()

    def _on_rip_probe_finished(self, recording: ExportRecording) -> None:
        """Rip probe succeeded: build ONE rip recording and run the add dialog.

        Technical fields (codecs, resolution, frame rate…) come from
        probing ``video_files[0]``; ``source_path`` is the rip root and
        ``file_size`` the total across all parts
        (``RipScanResult.total_size_bytes``).  ``extension`` carries the
        user-chosen format label from RipMetadataDialog and ``is_rip``
        marks the recording as a rip; ``duration`` is the
        user-typed total or ``None`` — the first VOB probes only a
        partial chunk of the title, so the probed duration is dropped.
        """
        self._set_busy(None)
        rip = self._pending_rip
        rip_format = self._pending_rip_format
        rip_duration = self._pending_rip_duration
        self._pending_rip = None
        self._pending_rip_format = None
        self._pending_rip_duration = None
        if rip is None:
            return  # stale signal: no accepted rip is waiting
        rip_recording = replace(
            recording,
            source_path=str(rip.folder_path),
            extension=rip_format or "VIDEO_TS",
            is_rip=True,
            duration=rip_duration,
            file_size=rip.total_size_bytes,
            parts=(str(rip.folder_path),),
        )
        settings = load_settings()
        dialog = AddRecordingDialog(rip_recording, settings, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self._service.add_recording(dialog.result_recording)
            except ExportSessionError:
                QMessageBox.information(
                    self,
                    "Already in Session",
                    "This rip is already in the session.",
                )
        self._refresh()

    def _on_rip_probe_failed(self, message: str) -> None:
        """Rip probe failed: drop the pending rip and show the error."""
        self._pending_rip = None
        self._pending_rip_format = None
        self._pending_rip_duration = None
        self._on_probe_failed(message)

    def _on_rip_scan_failed(self, message: str) -> None:
        """Scan failed: unlock the UI and show the error."""
        self._set_busy(None)
        QMessageBox.warning(self, "Rip Error", message)

    def _start_batch_probe(self, paths: list[str]) -> None:
        """Probe every scanned file on a worker thread.

        NOTE: the rip flow does not call this — accepting a rip probes
        only its first video and creates ONE recording.  The
        batch machinery is kept for the planned multi-video combining
        feature; nothing currently invokes it from the UI.
        """
        total = len(paths)
        self._set_busy(f"Probing 0 / {total}\u2026")
        thread = QThread(self)
        worker = BatchProbeWorker(self._service, paths)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_batch_progress)
        worker.finished.connect(self._on_batch_finished)
        worker.failed.connect(self._on_batch_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._on_batch_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._batch_thread = thread
        self._batch_worker = worker
        thread.start()

    def _on_batch_progress(self, current: int, total: int) -> None:
        """Update the busy indicator with the batch probe progress."""
        self._busy_label.setText(f"Probing {current} / {total}\u2026")

    def _on_batch_finished(
        self,
        added: list[ExportRecording],
        failed_paths: list[str],
    ) -> None:
        """Commit probed recordings on the main thread, then summarize.

        Duplicates (``ExportSessionError`` from the session) are counted
        as *skipped*, distinct from ``failed_paths`` (probe failures), and
        never abort the loop.  Summary shape: any probe failure shows one
        warning whose text is the probe sentence plus — when duplicates
        were skipped too — the skip sentence; duplicates alone show an
        information with the skip sentence.
        """
        skipped = 0
        for recording in added:
            try:
                self._service.add_recording(recording)
            except ExportSessionError:
                skipped += 1
        self._refresh()
        self._set_busy(None)
        failed_count = len(failed_paths)
        total = len(added) + len(failed_paths)
        skip_sentence = (
            f"{skipped} "
            f"{_plural(skipped, 'file was', 'files were')} "
            "already in the session."
        )
        if failed_count:
            lines = [
                f"{failed_count} of {total} "
                f"{_plural(total, 'file could', 'files could')} "
                "not be probed."
            ]
            if skipped:
                lines.append(skip_sentence)
            QMessageBox.warning(self, "Add from Folder", "\n".join(lines))
        elif skipped:
            QMessageBox.information(self, "Add from Folder", skip_sentence)

    def _on_batch_failed(self, message: str) -> None:
        """Catastrophic batch failure: unlock the UI and show the error."""
        self._set_busy(None)
        QMessageBox.warning(self, "Rip Error", message)

    def _on_rip_thread_finished(self) -> None:
        """Release the rip scan thread/worker references once stopped."""
        self._rip_thread = None
        self._rip_worker = None

    def _on_batch_thread_finished(self) -> None:
        """Release the batch probe thread/worker references once stopped."""
        self._batch_thread = None
        self._batch_worker = None

    # -- Edit Recording flow -----------------------------------------------

    def _on_edit_recording(self, source_path: str) -> None:
        """Open the edit dialog for a recording."""
        try:
            recording = self._service.session.get_recording(source_path)
        except Exception:
            return

        settings = load_settings()
        dialog = EditExportRecordingDialog(recording, settings, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._service.update_recording(source_path, dialog.result_recording)
        self._refresh()

    # -- Remove Recording flow ---------------------------------------------

    def _on_remove_recording(self, source_path: str) -> None:
        """Confirm and remove a recording."""
        filename = Path(source_path).name
        reply = QMessageBox.question(
            self,
            "Remove Recording",
            f"Remove {filename} from the export session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self._service.remove_recording(source_path)
            except Exception:
                pass
            self._refresh()

    # -- Edit Layout flow ---------------------------------------------------

    def _on_edit_layout(self) -> None:
        """Open the Layout Designer and apply an accepted result to the session.

        Cancel leaves the session layout untouched and writes nothing.  On
        accept the new layout is applied to the session first (the session
        works without persistence), then persisted via LayoutManager — a
        save failure warns but does not roll back the in-memory change.
        """
        dialog = LayoutDesignerDialog(self._service.layout, self, library_mode=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return                      # Cancel: session and disk untouched
        new_layout = dialog.result_layout
        if new_layout is None:
            return
        self._service.set_layout(new_layout)
        try:
            LayoutManager().save(new_layout)
        except LayoutManagerError as exc:
            QMessageBox.warning(self, "Layout Save Error", str(exc))
        self._refresh()

    # -- Export PDF flow ---------------------------------------------------

    def _on_export_pdf(self) -> None:
        """Export the current session as PDF.

        Everything — document build, screenshot mapping, and the export
        itself — runs inside one try: an exception that escaped this slot
        would be swallowed silently by PySide6.  Failures are
        logged and shown; a missing ``.pdf`` suffix is appended.
        """
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export PDF",
            "bootleg-export.pdf",
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"

        try:
            document = self._service.build_document()
            screenshots = self._build_screenshots_dict()
            export_layout_pdf(
                document, path, screenshot_paths_by_recording=screenshots,
            )
        except Exception as exc:
            get_logger().warning("PDF export failed: %s", exc)
            QMessageBox.warning(self, "PDF Export Error", str(exc))
            return
        QMessageBox.information(self, "PDF Export", f"Exported to:\n{path}")

    # -- Export XLSX flow --------------------------------------------------

    def _on_export_xlsx(self) -> None:
        """Export the current session as XLSX."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export XLSX",
            "bootleg-export.xlsx",
            "XLSX Files (*.xlsx)",
        )
        if not path:
            return

        document = self._service.build_document()
        screenshots = self._build_screenshots_dict()

        try:
            export_layout_xlsx(
                document, path, screenshot_paths_by_recording=screenshots,
            )
            QMessageBox.information(
                self, "Export Complete", f"XLSX saved to:\n{path}",
            )
        except LayoutXLSXExportError as exc:
            QMessageBox.warning(self, "Export Error", str(exc))

    # -- Helpers -----------------------------------------------------------

    def _build_screenshots_dict(self) -> dict[str, list[str]]:
        """Build the screenshot_paths_by_recording dict for exporters."""
        result: dict[str, list[str]] = {}
        for rec in self._service.session.recordings:
            if rec.screenshot_paths:
                result[rec.source_path] = list(rec.screenshot_paths)
        return result
