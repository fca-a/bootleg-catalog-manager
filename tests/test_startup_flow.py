"""Tests for the L15.3c startup flow (chooser dialogs and app entry)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bootleg_catalog.app.main import _resolve_startup_session, main
from bootleg_catalog.core.export_session_service import ExportSessionService
from bootleg_catalog.core.recent_sessions import RecentSessions
from bootleg_catalog.core.session_store import SessionData, save_session
from bootleg_catalog.core.settings_service import AppSettings
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    LayoutElement,
    LayoutLine,
    LayoutModel,
)
from bootleg_catalog.ui.dialogs.session_chooser_dialog import SessionChooserDialog
from bootleg_catalog.ui.dialogs.startup_chooser_dialog import StartupChooserDialog
from bootleg_catalog.ui.export_preview_window import ExportPreviewWindow

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _drain_deferred_deletes(qt_app):
    """Flush queued Qt deferred deletes while live top-level widgets are held.

    Copied from ``test_export_preview_window.py``: dropping the last strong
    reference to a window with a pending cell-widget deferred delete can
    crash the process on the next event dispatch.  Holding strong references
    across the flush lets the deferred deletes fire while the widgets are
    still alive.
    """
    yield
    live_widgets = list(qt_app.topLevelWidgets())
    if live_widgets:
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        live_widgets.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACCEPTED = QDialog.DialogCode.Accepted
_REJECTED = QDialog.DialogCode.Rejected


def _mock_dialog(exec_result: QDialog.DialogCode, **attrs: object) -> MagicMock:
    """A dialog mock whose ``exec()`` returns *exec_result*.

    Every attribute the production code reads after ``exec()`` (``choice``,
    ``result_layout``, ``selected_path``, ...) is set from *attrs*.
    """
    dialog = MagicMock()
    dialog.exec.return_value = exec_result
    for name, value in attrs.items():
        setattr(dialog, name, value)
    return dialog


def _make_layout(name: str = "Test Layout") -> LayoutModel:
    return LayoutModel(
        name=name,
        show_header=[
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Show", field_type=None),
            ]),
        ],
        recording_body=[
            LayoutLine(elements=[
                LayoutElement(kind="field", content="Duration", field_type=None),
            ]),
        ],
    )


def _make_recording(source_path: str = "C:/media/concert.mkv") -> ExportRecording:
    return ExportRecording(
        source_path=source_path,
        extension=".mkv",
        file_size=1_000_000_000,
        duration=7200.0,
        video_codec="h264",
        audio_codec="aac",
        screenshot_paths=[],
        show=None,
        title=None,
    )


def _recents_file(tmp_path: Path) -> Path:
    return tmp_path / "recent_sessions.json"


# ---------------------------------------------------------------------------
# StartupChooserDialog
# ---------------------------------------------------------------------------

class TestStartupChooserDialog:
    def test_constructor_creates_option_buttons_and_cancel(self, qt_app) -> None:
        dialog = StartupChooserDialog()
        assert dialog.windowTitle() == "Bootleg Catalog Manager"
        assert dialog._new_btn.text() == "New export session"
        assert dialog._continue_btn.text() == "Continue a recent session"
        assert dialog._cancel_btn.text() == "Cancel"
        assert dialog.minimumWidth() >= 360
        assert dialog.minimumHeight() >= 200

    def test_clicking_new_sets_choice_and_accepts(self, qt_app) -> None:
        dialog = StartupChooserDialog()
        dialog._new_btn.click()
        assert dialog.choice == StartupChooserDialog.NEW
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_clicking_continue_sets_choice_and_accepts(self, qt_app) -> None:
        dialog = StartupChooserDialog()
        dialog._continue_btn.click()
        assert dialog.choice == StartupChooserDialog.CONTINUE
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_cancel_leaves_choice_none(self, qt_app) -> None:
        dialog = StartupChooserDialog()
        dialog._cancel_btn.click()
        assert dialog.choice is None
        assert dialog.result() == QDialog.DialogCode.Rejected


# ---------------------------------------------------------------------------
# SessionChooserDialog
# ---------------------------------------------------------------------------

class TestSessionChooserDialog:
    def test_empty_recents_shows_hint_and_disables_open(
        self, qt_app, tmp_path: Path
    ) -> None:
        dialog = SessionChooserDialog(RecentSessions(_recents_file(tmp_path)))
        assert dialog._list.count() == 1
        hint = dialog._list.item(0)
        assert hint.text() == "No recent sessions."
        assert Qt.ItemFlag.ItemIsSelectable not in hint.flags()
        assert not dialog._open_btn.isEnabled()
        assert dialog._browse_btn.isEnabled()

    def test_two_recents_show_display_names(self, qt_app, tmp_path: Path) -> None:
        recents = RecentSessions(_recents_file(tmp_path))
        first = tmp_path / "um.bootleg"
        second = tmp_path / "duas.bootleg"
        first.write_bytes(b"fake")
        second.write_bytes(b"fake")
        recents.add("Lista Um", first)
        recents.add("Lista Duas", second)
        dialog = SessionChooserDialog(recents)
        assert dialog._list.count() == 2
        texts = [dialog._list.item(i).text() for i in range(2)]
        assert any(text.startswith("Lista Um\n") for text in texts)
        assert any(text.startswith("Lista Duas\n") for text in texts)
        assert all("(missing)" not in text for text in texts)

    def test_select_and_open_sets_path_and_accepts(
        self, qt_app, tmp_path: Path
    ) -> None:
        recents = RecentSessions(_recents_file(tmp_path))
        target = tmp_path / "unica.bootleg"
        target.write_bytes(b"fake")
        recents.add("Unica", target)
        dialog = SessionChooserDialog(recents)
        assert not dialog._open_btn.isEnabled()
        dialog._list.setCurrentRow(0)
        assert dialog._open_btn.isEnabled()
        dialog._open_btn.click()
        assert dialog.selected_path == str(target)
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_double_click_opens_selected_row(self, qt_app, tmp_path: Path) -> None:
        recents = RecentSessions(_recents_file(tmp_path))
        target = tmp_path / "dupla.bootleg"
        target.write_bytes(b"fake")
        recents.add("Dupla", target)
        dialog = SessionChooserDialog(recents)
        item = dialog._list.item(0)
        dialog._list.itemDoubleClicked.emit(item)
        assert dialog.selected_path == str(target)
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_cancel_leaves_selected_path_none(self, qt_app, tmp_path: Path) -> None:
        recents = RecentSessions(_recents_file(tmp_path))
        target = tmp_path / "escolhida.bootleg"
        target.write_bytes(b"fake")
        recents.add("Escolhida", target)
        dialog = SessionChooserDialog(recents)
        dialog._list.setCurrentRow(0)
        dialog._cancel_btn.click()
        assert dialog.selected_path is None
        assert dialog.result() == QDialog.DialogCode.Rejected

    def test_browse_accepts_chosen_path(self, qt_app, tmp_path: Path) -> None:
        dialog = SessionChooserDialog(RecentSessions(_recents_file(tmp_path)))
        chosen = tmp_path / "buscado.bootleg"
        with patch(
            "bootleg_catalog.ui.dialogs.session_chooser_dialog."
            "QFileDialog.getOpenFileName",
            return_value=(str(chosen), ""),
        ):
            dialog._browse_btn.click()
        assert dialog.selected_path == str(chosen)
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_missing_file_row_marked_and_selectable(
        self, qt_app, tmp_path: Path
    ) -> None:
        recents = RecentSessions(_recents_file(tmp_path))
        missing = tmp_path / "nao-existe.bootleg"
        recents.add("Sumiu", missing)
        dialog = SessionChooserDialog(recents)
        assert dialog._list.count() == 1
        item = dialog._list.item(0)
        assert "(missing)" in item.text()
        assert str(missing) in item.text()
        assert Qt.ItemFlag.ItemIsSelectable in item.flags()
        dialog._list.setCurrentRow(0)
        assert dialog._open_btn.isEnabled()
        dialog._open_btn.click()
        assert dialog.selected_path == str(missing)
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_dialog_never_touches_recent_store(
        self, qt_app, tmp_path: Path
    ) -> None:
        recents = RecentSessions(_recents_file(tmp_path))
        target = tmp_path / "lista.bootleg"
        target.write_bytes(b"fake")
        recents.add("Lista", target)
        with patch(
            "bootleg_catalog.ui.dialogs.session_chooser_dialog.RecentSessions",
        ) as MockRecents, patch.object(recents, "add") as mock_add:
            dialog = SessionChooserDialog(recents)
            dialog._list.setCurrentRow(0)
            dialog._open_btn.click()
        MockRecents.assert_not_called()
        mock_add.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_startup_session
# ---------------------------------------------------------------------------

class TestResolveStartupSession:
    def test_chooser_cancelled_returns_none(self) -> None:
        chooser = _mock_dialog(_REJECTED, choice=None)
        with patch(
            "bootleg_catalog.app.main.StartupChooserDialog", return_value=chooser
        ) as MockChooser:
            assert _resolve_startup_session() is None
        MockChooser.assert_called_once()

    def test_new_flow_returns_clean_service_with_designer_layout(self) -> None:
        sentinel = _make_layout(name="Escolhido")
        chooser = _mock_dialog(_ACCEPTED, choice=StartupChooserDialog.NEW)
        designer = _mock_dialog(_ACCEPTED, result_layout=sentinel)
        with patch(
            "bootleg_catalog.app.main.StartupChooserDialog", return_value=chooser
        ) as MockChooser, patch(
            "bootleg_catalog.app.main.LayoutDesignerDialog", return_value=designer
        ) as MockDesigner, patch(
            "bootleg_catalog.app.main.QInputDialog.getText",
            return_value=("Minha Lista", True),
        ):
            # The mocked chooser class must expose the real constants so the
            # production ``choice == StartupChooserDialog.NEW`` test behaves
            # like the real class (pattern: test_settings.py).
            MockChooser.NEW = StartupChooserDialog.NEW
            MockChooser.CONTINUE = StartupChooserDialog.CONTINUE
            result = _resolve_startup_session()
        assert result is not None
        service, path = result
        assert path is None
        assert isinstance(service, ExportSessionService)
        assert service.display_name == "Minha Lista"
        assert service.is_dirty() is False
        assert service.layout is sentinel
        # The designer was seeded with the default layout:
        assert MockDesigner.call_args.args[0].name == "Default"

    def test_new_designer_cancelled_loops_back_to_chooser(self) -> None:
        first = _mock_dialog(_ACCEPTED, choice=StartupChooserDialog.NEW)
        second = _mock_dialog(_REJECTED, choice=None)
        designer = _mock_dialog(_REJECTED, result_layout=None)
        with patch(
            "bootleg_catalog.app.main.StartupChooserDialog",
            side_effect=[first, second],
        ) as MockChooser, patch(
            "bootleg_catalog.app.main.LayoutDesignerDialog", return_value=designer
        ):
            MockChooser.NEW = StartupChooserDialog.NEW
            MockChooser.CONTINUE = StartupChooserDialog.CONTINUE
            assert _resolve_startup_session() is None
        assert MockChooser.call_count == 2

    def test_new_name_picker_cancelled_loops_back_to_chooser(self) -> None:
        first = _mock_dialog(_ACCEPTED, choice=StartupChooserDialog.NEW)
        second = _mock_dialog(_REJECTED, choice=None)
        designer = _mock_dialog(_ACCEPTED, result_layout=_make_layout())
        with patch(
            "bootleg_catalog.app.main.StartupChooserDialog",
            side_effect=[first, second],
        ) as MockChooser, patch(
            "bootleg_catalog.app.main.LayoutDesignerDialog", return_value=designer
        ), patch(
            "bootleg_catalog.app.main.QInputDialog.getText", return_value=("", False)
        ) as MockGetText:
            MockChooser.NEW = StartupChooserDialog.NEW
            MockChooser.CONTINUE = StartupChooserDialog.CONTINUE
            assert _resolve_startup_session() is None
        MockGetText.assert_called_once()
        assert MockChooser.call_count == 2

    def test_continue_flow_loads_session_and_records_recent(
        self, tmp_path: Path
    ) -> None:
        session_file = tmp_path / "carregada.bootleg"
        save_session(
            SessionData(
                display_name="Carregada",
                layout=_make_layout(name="Arquivo Carregado"),
                recordings=[_make_recording()],
            ),
            session_file,
        )
        chooser = _mock_dialog(_ACCEPTED, choice=StartupChooserDialog.CONTINUE)
        session_chooser = _mock_dialog(
            _ACCEPTED, selected_path=str(session_file)
        )
        recents = RecentSessions(_recents_file(tmp_path))
        with patch(
            "bootleg_catalog.app.main.StartupChooserDialog", return_value=chooser
        ) as MockChooser, patch(
            "bootleg_catalog.app.main.SessionChooserDialog",
            return_value=session_chooser,
        ), patch(
            "bootleg_catalog.app.main.RecentSessions", return_value=recents
        ):
            MockChooser.NEW = StartupChooserDialog.NEW
            MockChooser.CONTINUE = StartupChooserDialog.CONTINUE
            result = _resolve_startup_session()
        assert result is not None
        service, path = result
        assert path == session_file
        assert service.display_name == "Carregada"
        assert service.layout.name == "Arquivo Carregado"
        assert service.is_dirty() is False
        assert [rec.source_path for rec in service.session.recordings] == [
            "C:/media/concert.mkv"
        ]
        # main.py (not the dialog) recorded the opened session:
        assert [(entry.display_name, entry.path) for entry in recents.list()] == [
            ("Carregada", str(session_file))
        ]

    def test_continue_session_chooser_cancelled_loops_back(self) -> None:
        first = _mock_dialog(_ACCEPTED, choice=StartupChooserDialog.CONTINUE)
        second = _mock_dialog(_REJECTED, choice=None)
        session_chooser = _mock_dialog(_REJECTED, selected_path=None)
        with patch(
            "bootleg_catalog.app.main.StartupChooserDialog",
            side_effect=[first, second],
        ) as MockChooser, patch(
            "bootleg_catalog.app.main.SessionChooserDialog",
            return_value=session_chooser,
        ), patch("bootleg_catalog.app.main.RecentSessions"):
            MockChooser.NEW = StartupChooserDialog.NEW
            MockChooser.CONTINUE = StartupChooserDialog.CONTINUE
            assert _resolve_startup_session() is None
        assert MockChooser.call_count == 2

    def test_continue_legacy_file_warns_and_loops_back(
        self, tmp_path: Path
    ) -> None:
        legacy = tmp_path / "antigo.bootleg"
        legacy.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
        first = _mock_dialog(_ACCEPTED, choice=StartupChooserDialog.CONTINUE)
        second = _mock_dialog(_REJECTED, choice=None)
        session_chooser = _mock_dialog(_ACCEPTED, selected_path=str(legacy))
        with patch(
            "bootleg_catalog.app.main.StartupChooserDialog",
            side_effect=[first, second],
        ) as MockChooser, patch(
            "bootleg_catalog.app.main.SessionChooserDialog",
            return_value=session_chooser,
        ), patch("bootleg_catalog.app.main.RecentSessions"), patch(
            "bootleg_catalog.app.main.QMessageBox.warning"
        ) as MockWarning:
            MockChooser.NEW = StartupChooserDialog.NEW
            MockChooser.CONTINUE = StartupChooserDialog.CONTINUE
            assert _resolve_startup_session() is None
        MockWarning.assert_called_once()
        assert "legacy" in MockWarning.call_args.args[2].lower()
        assert MockChooser.call_count == 2


# ---------------------------------------------------------------------------
# ffprobe_path from settings
# ---------------------------------------------------------------------------

class TestFfprobePathFromSettings:
    def test_new_flow_passes_ffprobe_path_from_settings(self) -> None:
        chooser = _mock_dialog(_ACCEPTED, choice=StartupChooserDialog.NEW)
        designer = _mock_dialog(_ACCEPTED, result_layout=_make_layout())
        with patch(
            "bootleg_catalog.app.main.load_settings",
            return_value=AppSettings(ffprobe_path="/custom/ffprobe"),
        ), patch(
            "bootleg_catalog.app.main.StartupChooserDialog", return_value=chooser
        ) as MockChooser, patch(
            "bootleg_catalog.app.main.LayoutDesignerDialog", return_value=designer
        ), patch(
            "bootleg_catalog.app.main.QInputDialog.getText",
            return_value=("Com FFprobe", True),
        ), patch(
            "bootleg_catalog.app.main.ExportSessionService"
        ) as MockService:
            MockChooser.NEW = StartupChooserDialog.NEW
            MockChooser.CONTINUE = StartupChooserDialog.CONTINUE
            result = _resolve_startup_session()
        assert result is not None
        service, path = result
        assert service is MockService.return_value
        assert path is None
        assert MockService.call_args.kwargs["ffprobe_path"] == "/custom/ffprobe"
        MockService.return_value.set_display_name.assert_called_once_with(
            "Com FFprobe"
        )

    def test_continue_flow_passes_ffprobe_path_from_settings(self) -> None:
        chooser = _mock_dialog(_ACCEPTED, choice=StartupChooserDialog.CONTINUE)
        session_chooser = _mock_dialog(
            _ACCEPTED, selected_path="C:/sessions/alta.bootleg"
        )
        with patch(
            "bootleg_catalog.app.main.load_settings",
            return_value=AppSettings(ffprobe_path="/custom/ffprobe"),
        ), patch(
            "bootleg_catalog.app.main.StartupChooserDialog", return_value=chooser
        ) as MockChooser, patch(
            "bootleg_catalog.app.main.SessionChooserDialog",
            return_value=session_chooser,
        ), patch("bootleg_catalog.app.main.RecentSessions"), patch(
            "bootleg_catalog.app.main.ExportSessionService"
        ) as MockService:
            MockChooser.NEW = StartupChooserDialog.NEW
            MockChooser.CONTINUE = StartupChooserDialog.CONTINUE
            result = _resolve_startup_session()
        assert result is not None
        service, path = result
        assert service is MockService.return_value
        assert path == Path("C:/sessions/alta.bootleg")
        assert MockService.call_args.kwargs["ffprobe_path"] == "/custom/ffprobe"
        MockService.return_value.load.assert_called_once_with(
            Path("C:/sessions/alta.bootleg")
        )

    def test_empty_ffprobe_path_becomes_none(self) -> None:
        chooser = _mock_dialog(_ACCEPTED, choice=StartupChooserDialog.NEW)
        designer = _mock_dialog(_ACCEPTED, result_layout=_make_layout())
        with patch(
            "bootleg_catalog.app.main.load_settings",
            return_value=AppSettings(ffprobe_path=""),
        ), patch(
            "bootleg_catalog.app.main.StartupChooserDialog", return_value=chooser
        ) as MockChooser, patch(
            "bootleg_catalog.app.main.LayoutDesignerDialog", return_value=designer
        ), patch(
            "bootleg_catalog.app.main.QInputDialog.getText",
            return_value=("Vazio", True),
        ), patch(
            "bootleg_catalog.app.main.ExportSessionService"
        ) as MockService:
            MockChooser.NEW = StartupChooserDialog.NEW
            MockChooser.CONTINUE = StartupChooserDialog.CONTINUE
            result = _resolve_startup_session()
        assert result is not None
        assert MockService.call_args.kwargs["ffprobe_path"] is None


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

class TestMainEntryPoint:
    def test_main_returns_zero_when_chooser_cancelled(self) -> None:
        chooser = _mock_dialog(_REJECTED, choice=None)
        with patch("bootleg_catalog.app.main.setup_logging"), patch(
            "bootleg_catalog.app.main.BootlegCatalogApplication"
        ) as MockAppClass, patch(
            "bootleg_catalog.app.main.StartupChooserDialog", return_value=chooser
        ), patch("bootleg_catalog.app.main.ExportPreviewWindow") as MockWindow, patch(
            "bootleg_catalog.app.main.evict_cache", return_value=0
        ) as MockEvict:
            assert main() == 0
        MockAppClass.assert_called_once()
        MockAppClass.return_value.exec.assert_not_called()
        MockWindow.assert_not_called()
        # Eviction runs on the startup path with keep_paths=None.
        MockEvict.assert_called_once_with()

    def test_main_shows_window_without_exec(self) -> None:
        service = MagicMock()
        initial = Path("C:/sessions/x.bootleg")
        with patch("bootleg_catalog.app.main.setup_logging"), patch(
            "bootleg_catalog.app.main.BootlegCatalogApplication"
        ) as MockAppClass, patch(
            "bootleg_catalog.app.main._resolve_startup_session",
            return_value=(service, initial),
        ), patch("bootleg_catalog.app.main.ExportPreviewWindow") as MockWindow, patch(
            "bootleg_catalog.app.main.evict_cache", return_value=0
        ):
            MockAppClass.return_value.exec.return_value = 0
            assert main() == 0
        MockWindow.assert_called_once_with(service, initial_path=initial)
        window = MockWindow.return_value
        window.setAttribute.assert_called_once_with(
            Qt.WidgetAttribute.WA_DeleteOnClose
        )
        window.show.assert_called_once()
        window.exec.assert_not_called()
        MockAppClass.return_value.exec.assert_called_once()


# ---------------------------------------------------------------------------
# ExportPreviewWindow initial_path
# ---------------------------------------------------------------------------

class TestInitialPath:
    def test_initial_path_sets_current_path_and_clean_title(
        self, qt_app, tmp_path: Path
    ) -> None:
        service = ExportSessionService(_make_layout(name="Startup"))
        service.set_display_name("Carregada")
        service.mark_clean()
        win = ExportPreviewWindow(service, initial_path=tmp_path / "x.bootleg")
        assert win._current_path == tmp_path / "x.bootleg"
        assert "Carregada" in win.windowTitle()
        assert "*" not in win.windowTitle()
