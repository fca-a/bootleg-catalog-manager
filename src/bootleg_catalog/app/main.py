from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from bootleg_catalog.app.application import BootlegCatalogApplication
from bootleg_catalog.core.export_session_service import (
    ExportSessionService,
    ExportSessionServiceError,
)
from bootleg_catalog.core.layout_manager import default_layout
from bootleg_catalog.core.recent_sessions import RecentSessions
from bootleg_catalog.core.session_store import SessionStoreError, evict_cache
from bootleg_catalog.core.settings_service import load_settings
from bootleg_catalog.ui.dialogs.layout_designer_dialog import LayoutDesignerDialog
from bootleg_catalog.ui.dialogs.session_chooser_dialog import SessionChooserDialog
from bootleg_catalog.ui.dialogs.startup_chooser_dialog import StartupChooserDialog
from bootleg_catalog.ui.export_preview_window import ExportPreviewWindow
from bootleg_catalog.utils.logging import get_logger, setup_logging


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    logger = get_logger()
    args = sys.argv if argv is None else argv
    app = BootlegCatalogApplication(args)

    # Drop screenshot-cache directories left behind by past
    # sessions.  No session is open yet, so keep_paths is None; this is
    # the only sanctioned call site — never evict while a session is
    # open, because loaded recordings resolve their embedded
    # screenshots from this cache.  evict_cache never raises.
    evicted = evict_cache()
    if evicted:
        logger.info("Evicted %d stale screenshot cache directories", evicted)

    result = _resolve_startup_session()
    if result is None:
        logger.info("Startup cancelled")
        return 0
    service, initial_path = result

    window = ExportPreviewWindow(service, initial_path=initial_path)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    window.show()
    logger.info("Application started")
    return app.exec()


def _resolve_startup_session() -> tuple[ExportSessionService, Path | None] | None:
    """Run the chooser chain; ``None`` means the user cancelled startup.

    A failed NEW/CONTINUE attempt (designer or name cancelled, load
    error) loops back to the chooser instead of exiting.  Settings are
    read once before the loop; the configured ffprobe path (empty
    string treated as unset) is passed to both branches.
    """
    ffprobe_path = load_settings().ffprobe_path or None
    while True:
        chooser = StartupChooserDialog()
        if chooser.exec() != QDialog.DialogCode.Accepted or chooser.choice is None:
            return None
        if chooser.choice == StartupChooserDialog.NEW:
            result = _start_new_session(ffprobe_path)
        else:
            result = _continue_recent_session(ffprobe_path)
        if result is not None:
            return result


def _start_new_session(
    ffprobe_path: str | None,
) -> tuple[ExportSessionService, Path | None] | None:
    designer = LayoutDesignerDialog(default_layout(), library_mode=False)
    if designer.exec() != QDialog.DialogCode.Accepted or designer.result_layout is None:
        return None
    layout = designer.result_layout

    name, ok = QInputDialog.getText(
        None, "Session Name", "Display name:", text="Untitled",
    )
    if not ok or not name.strip():
        return None

    service = ExportSessionService(layout, ffprobe_path=ffprobe_path)
    service.set_display_name(name.strip())
    service.mark_clean()
    return service, None


def _continue_recent_session(
    ffprobe_path: str | None,
) -> tuple[ExportSessionService, Path | None] | None:
    recents = RecentSessions()
    chooser = SessionChooserDialog(recents)
    if chooser.exec() != QDialog.DialogCode.Accepted or chooser.selected_path is None:
        return None
    path = Path(chooser.selected_path)

    service = ExportSessionService(default_layout(), ffprobe_path=ffprobe_path)
    try:
        service.load(path)
    except (SessionStoreError, ExportSessionServiceError) as exc:
        QMessageBox.warning(None, "Open Error", str(exc))
        return None

    service.mark_clean()
    recents.add(service.display_name, path)
    return service, path


if __name__ == "__main__":
    raise SystemExit(main())
