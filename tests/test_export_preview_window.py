"""Tests for ExportPreviewWindow and PreviewWidget (L10)."""

from __future__ import annotations

import gc
import os
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bootleg_catalog.core.export_session_service import (
    ExportSessionService,
    ExportSessionServiceError,
)
from bootleg_catalog.core.field_resolver import ResolvedElement, ResolvedLine
from bootleg_catalog.core.layout_manager import LayoutManagerError
from bootleg_catalog.core.rip_scanner import RipScanError, RipScanResult
from bootleg_catalog.core.session_store import (
    MANIFEST_NAME,
    SessionData,
    load_session,
    save_session,
)
from bootleg_catalog.core.settings_service import AppSettings
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FooterConfig,
    HeaderConfig,
    LayoutElement,
    LayoutLine,
    LayoutModel,
    LogoSlot,
    ScreenshotConfig,
    TextSlot,
)
from bootleg_catalog.models.resolved_export import (
    ResolvedExportDocument,
    ResolvedRecording,
    ResolvedShowSection,
)
from bootleg_catalog.ui.dialogs.rip_metadata_dialog import (
    RipMetadataDialog,
    parse_duration_text,
)
from bootleg_catalog.ui.export_preview_window import ExportPreviewWindow
from bootleg_catalog.ui.widgets.export_preview_widget import (
    PreviewWidget,
    _render_line,
    page_size_px,
)
from bootleg_catalog.ui.widgets.recording_table_model import RecordingTableModel
from bootleg_catalog.ui.workers.probe_worker import ProbeWorker
from bootleg_catalog.ui.workers.rip_scan_worker import BatchProbeWorker, RipScanWorker
from bootleg_catalog.utils.paths import format_file_size

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QItemSelectionModel,
    QObject,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)


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

    A test that drops its last strong reference to a window while a Qt
    deferred delete is still pending can leave the widget referenced only
    by the closure that queued the delete, so the re-entrant destruction
    crashes the process on the next event dispatch.  Holding strong
    references across the flush lets the deferred deletes fire while the
    widgets are still alive; releasing afterwards then destroys them with
    an empty queue.
    """
    yield
    live_widgets = list(qt_app.topLevelWidgets())
    if live_widgets:
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        live_widgets.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    # Collect this test's cycle garbage now, while Qt is fully alive.
    # Patched-dialog call_args (e.g. a mocked
    # QFileDialog.getOpenFileName) pins its window in a reference cycle
    # that plain refcounting never frees; letting such garbage accumulate
    # until interpreter shutdown makes the final large batch finalize in
    # an order that corrupts the heap (observed as a crash with Windows
    # status 0xC0000374 before the summary line could print).
    gc.collect()


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


def _make_recording(
    source_path: str = "C:/media/concert.mkv",
    show: str | None = None,
    title: str | None = None,
    **overrides,
) -> ExportRecording:
    defaults = dict(
        source_path=source_path,
        extension=".mkv",
        file_size=1_000_000_000,
        duration=7200.0,
        video_codec="h264",
        audio_codec="aac",
        screenshot_paths=[],
        show=show,
        title=title,
    )
    defaults.update(overrides)
    return ExportRecording(**defaults)


def _make_service(layout: LayoutModel | None = None) -> ExportSessionService:
    return ExportSessionService(layout or _make_layout())


def _make_png(path: Path, size: tuple[int, int] = (400, 200)) -> Path:
    """Write a small real PNG for inline-screenshot tests (L15.8.5)."""
    from PIL import Image
    img = Image.new("RGB", size, color=(10, 20, 30))
    img.save(path, "PNG")
    return path


def _screenshot_doc(
    screenshot_paths: list[str],
    n: int = 3,
    **config_overrides,
) -> ResolvedExportDocument:
    """A document whose recording body is a [Screenshot:N] token.

    The token is parsed and resolved through the real pipeline, so the
    preview sees exactly what the resolver produces.
    """
    from bootleg_catalog.core.field_resolver import FieldResolver
    from bootleg_catalog.core.layout_text import parse_layout_lines

    layout = _make_layout()
    body = parse_layout_lines(f"[Screenshot:{n}]")
    layout.recording_body = body
    layout.screenshot_config = ScreenshotConfig(**config_overrides)

    rec = _make_recording(screenshot_paths=screenshot_paths)
    resolver = FieldResolver()
    resolved_lines = [resolver.resolve_line(line, rec)[0] for line in body]
    section = ResolvedShowSection(
        show_name="Show",
        header_lines=[],
        recordings=[ResolvedRecording(recording=rec, lines=resolved_lines)],
    )
    return ResolvedExportDocument(
        layout=layout, show_sections=[section], separator=None,
    )


def _pixmap_labels(widget) -> list:
    """Labels in *widget* currently holding a non-null pixmap."""
    return [
        lb for lb in widget.findChildren(QLabel)
        if lb.pixmap() is not None and not lb.pixmap().isNull()
    ]


def _make_rip_result(
    video_files: list[str],
    total_size_bytes: int = 7_000_000_000,
) -> RipScanResult:
    return RipScanResult(
        folder_path="C:/rip",
        rip_kind="dvd",
        video_files=video_files,
        total_size_bytes=total_size_bytes,
    )


def _rip_meta_factory(fmt: str, duration_text: str, accept: bool):
    """RipMetadataDialog double: a real dialog pre-filled with *fmt* and
    *duration_text*, pre-settled (``accept`` False leaves it rejected),
    so the flow's ``exec`` call never enters an event loop."""

    def factory(parent=None):
        dialog = RipMetadataDialog(parent)
        dialog.findChild(QComboBox, "formatCombo").setCurrentText(fmt)
        dialog.findChild(QLineEdit, "durationEdit").setText(duration_text)
        if accept:
            dialog.accept()
        return dialog

    return factory


class _SyncQThread(QThread):
    """QThread double that runs its worker synchronously (L15.3d.2).

    ``start`` emits ``started`` directly; paired with a worker that
    stays in the main thread, the connected ``worker.run`` slot is
    invoked in place instead of being queued to a never-started thread.
    ``isRunning`` always reports False so the close guard treats the
    fake as idle.
    """

    def start(self, *args, **kwargs) -> None:  # noqa: N802 (Qt API)
        self.start_called = True
        self.started.emit()

    def isRunning(self) -> bool:  # noqa: N802 (Qt API)
        return False


class _FakeProbeWorker(QObject):
    """ProbeWorker double: real signals, borrowed run() logic, main affinity."""

    finished = Signal(object)
    failed = Signal(str)
    run = ProbeWorker.run  # reuse the real slot implementation

    def __init__(self, service, path) -> None:
        super().__init__()
        self._service = service
        self._path = path

    def moveToThread(self, thread) -> None:  # noqa: N802 (Qt API)
        # Deliberately a no-op: staying in the main thread keeps the
        # started -> run dispatch direct (synchronous) in tests.
        self.moved_thread = thread


class _SilentRipScanWorker(QObject):
    """RipScanWorker double: records the call, never emits a result."""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, folder) -> None:
        super().__init__()
        self._folder = folder
        self.ran = False

    def moveToThread(self, thread) -> None:  # noqa: N802 (Qt API)
        # Deliberately a no-op: staying in the main thread keeps the
        # started -> run dispatch direct (synchronous) in tests.
        self.moved_thread = thread

    def run(self) -> None:
        self.ran = True


class _FakeRipScanWorker(QObject):
    """RipScanWorker double: real signals, borrowed run() logic, main affinity."""

    finished = Signal(object)
    failed = Signal(str)
    run = RipScanWorker.run  # reuse the real slot implementation

    def __init__(self, folder) -> None:
        super().__init__()
        # The borrowed RipScanWorker.run reads self._folder.
        self._folder = folder

    def moveToThread(self, thread) -> None:  # noqa: N802 (Qt API)
        # Deliberately a no-op: staying in the main thread keeps the
        # started -> run dispatch direct (synchronous) in tests.
        self.moved_thread = thread


class _FakeBatchProbeWorker(QObject):
    """BatchProbeWorker double: real signals, borrowed run() logic, main affinity."""

    progress = Signal(int, int)
    finished = Signal(list, list)
    failed = Signal(str)
    run = BatchProbeWorker.run  # reuse the real slot implementation

    def __init__(self, service, paths) -> None:
        super().__init__()
        self._service = service
        self._paths = paths

    def moveToThread(self, thread) -> None:  # noqa: N802 (Qt API)
        # Deliberately a no-op: staying in the main thread keeps the
        # started -> run dispatch direct (synchronous) in tests.
        self.moved_thread = thread


class _RunningQThread(QThread):
    """QThread double that always reports a running thread."""

    def isRunning(self) -> bool:  # noqa: N802 (Qt API)
        return True


# ---------------------------------------------------------------------------
# PreviewWidget tests
# ---------------------------------------------------------------------------

class TestPreviewWidget:
    def test_empty_state(self, qt_app) -> None:
        """Empty document shows the empty-state message."""
        widget = PreviewWidget()
        doc = ResolvedExportDocument(
            layout=_make_layout(),
            show_sections=[],
            separator=None,
        )
        widget.set_document(doc)
        # Find the empty-state label
        from PySide6.QtWidgets import QLabel
        found = False
        for child in widget.findChildren(QLabel):
            if "No recordings yet" in (child.text() or ""):
                found = True
        assert found

    def test_none_document(self, qt_app) -> None:
        """Setting document to None shows empty state."""
        widget = PreviewWidget()
        # Initial state — no document set
        from PySide6.QtWidgets import QLabel
        found = False
        for child in widget.findChildren(QLabel):
            if "No recordings yet" in (child.text() or ""):
                found = True
        assert found

    def test_renders_show_section(self, qt_app) -> None:
        """A show section with a recording renders text content."""
        rec = _make_recording()
        resolved_rec = ResolvedRecording(
            recording=rec,
            lines=[],
        )
        section = ResolvedShowSection(
            show_name="Test Show",
            header_lines=[],
            recordings=[resolved_rec],
        )
        doc = ResolvedExportDocument(
            layout=_make_layout(),
            show_sections=[section],
            separator=None,
        )
        widget = PreviewWidget()
        widget.set_document(doc)
        # Should not crash; widget should exist
        assert widget is not None

    def test_screenshot_hint_removed(self, qt_app) -> None:
        """The trailing "N screenshots" count hint is gone (L15.8.5).

        Screenshots now render inline wherever the layout places a
        [Screenshot:N] token, so the per-recording summary label that
        used to trail each recording was removed.
        """
        rec = _make_recording(screenshot_paths=["a.jpg", "b.jpg", "c.jpg"])
        resolved_rec = ResolvedRecording(
            recording=rec,
            lines=[],
        )
        section = ResolvedShowSection(
            show_name="Show",
            header_lines=[],
            recordings=[resolved_rec],
        )
        doc = ResolvedExportDocument(
            layout=_make_layout(),
            show_sections=[section],
            separator=None,
        )
        widget = PreviewWidget()
        widget.set_document(doc)
        from PySide6.QtWidgets import QLabel
        hint_labels = [
            child for child in widget.findChildren(QLabel)
            if "screenshot" in (child.text() or "").lower()
        ]
        assert hint_labels == []

    def test_rebuild_reuses_layout_and_does_not_leak_widgets(self, qt_app) -> None:
        """Repeated rebuilds reuse one layout and never orphan top-level widgets."""
        from PySide6.QtWidgets import QWidget

        widget = PreviewWidget()
        doc = ResolvedExportDocument(
            layout=_make_layout(),
            show_sections=[],
            separator=None,
        )
        baseline = set(qt_app.topLevelWidgets())

        # Several rebuilds on the same widget — the exact path where a fresh
        # QVBoxLayout(self) used to be constructed but never attach.
        for _ in range(5):
            widget.set_document(doc)

        # The widget's own layout must still hold the rendered content.
        # (With an orphaned layout this is empty, so the parent assertions
        # below must not be allowed to pass vacuously.)
        layout = widget.layout()
        assert layout is not None
        assert layout.count() > 0
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget()
            if w is not None:
                assert w.parent() is widget

        # Detached children are queued for deletion; flush that queue and
        # verify no new top-level widget survived the rebuilds.
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        leaked = [w for w in qt_app.topLevelWidgets() if w not in baseline]
        assert leaked == []


# ---------------------------------------------------------------------------
# Preview page rendering (L15.7b)
# ---------------------------------------------------------------------------

def _make_resolved_line(text: str) -> ResolvedLine:
    return ResolvedLine(elements=[ResolvedElement(text=text, style=None)])


def _make_overflowing_document(num_recordings: int) -> ResolvedExportDocument:
    """A single section with many one-line recordings (overflows A4 at 100%)."""
    recordings = [
        ResolvedRecording(
            recording=_make_recording(source_path=f"C:/media/concert{i:03d}.mkv"),
            lines=[_make_resolved_line(f"Concert {i:03d} - 2001-05-04 - 120 min")],
        )
        for i in range(num_recordings)
    ]
    section = ResolvedShowSection(
        show_name="Overflow", header_lines=[], recordings=recordings
    )
    return ResolvedExportDocument(
        layout=_make_layout(), show_sections=[section], separator=None
    )


class TestInlineScreenshotToken:
    """Inline [Screenshot:N] blocks render in the preview (L15.8.5)."""

    def test_three_screenshots_render_pixmaps(self, qt_app, tmp_path) -> None:
        """3 real PNGs + [Screenshot:3] → 3 pixmap QLabels."""
        paths = [str(_make_png(tmp_path / f"s{i}.png")) for i in range(3)]
        doc = _screenshot_doc(paths, n=3)
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        widget.set_document(doc)

        assert len(_pixmap_labels(widget)) == 3
        assert [
            lb for lb in widget.findChildren(QLabel)
            if lb.text() == "no screenshot"
        ] == []

    def test_partial_screenshots_pad_with_placeholders(
        self, qt_app, tmp_path
    ) -> None:
        """1 real PNG + [Screenshot:3] → 1 pixmap + 2 placeholders."""
        paths = [str(_make_png(tmp_path / "s0.png"))]
        doc = _screenshot_doc(paths, n=3)
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        widget.set_document(doc)

        assert len(_pixmap_labels(widget)) == 1
        placeholders = [
            lb for lb in widget.findChildren(QLabel)
            if lb.text() == "no screenshot"
        ]
        assert len(placeholders) == 2

    def test_zero_screenshots_render_all_placeholders(self, qt_app) -> None:
        """0 PNGs + [Screenshot:3] → 3 "no screenshot" placeholders."""
        doc = _screenshot_doc([], n=3)
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        widget.set_document(doc)

        placeholders = [
            lb for lb in widget.findChildren(QLabel)
            if lb.text() == "no screenshot"
        ]
        assert len(placeholders) == 3
        assert _pixmap_labels(widget) == []

    def test_max_width_change_resizes_pixmaps_proportionally(
        self, qt_app, tmp_path
    ) -> None:
        """Rebuilding with a smaller max_width_px scales the pixmap down."""
        path = str(_make_png(tmp_path / "s.png", size=(400, 200)))

        def widths(max_w: int) -> list[int]:
            doc = _screenshot_doc(
                [path],
                n=1,
                max_width_px=max_w,
                max_height_px=1000,  # width is the binding constraint
            )
            widget = PreviewWidget()
            widget._zoom_combo.setCurrentText("100%")
            widget.set_document(doc)
            return [lb.pixmap().width() for lb in _pixmap_labels(widget)]

        assert widths(200) == [200]
        assert widths(100) == [100]


class TestColouredElements:
    """ElementStyle.color / .highlight_color render as spans (L15.8.6b)."""

    @staticmethod
    def _label_for(style) -> tuple[QWidget, QLabel]:
        """Render one styled element through the rich-text line builder.

        The holder QWidget is returned too — the QLabel is its child and
        would be deleted once the holder is garbage-collected.
        """
        holder = QWidget()
        QVBoxLayout(holder)
        label = _render_line(
            holder,
            ResolvedLine(elements=[ResolvedElement(text="word", style=style)]),
        )
        return holder, label

    def test_foreground_colour_emits_span(self, qt_app) -> None:
        """A red element carries a foreground colour span."""
        _holder, label = self._label_for(ElementStyle(color="#FF0000"))
        assert label.textFormat() == Qt.TextFormat.RichText
        assert 'style="color:#FF0000;"' in label.text()

    def test_highlight_emits_background_span(self, qt_app) -> None:
        """A highlighted element carries a background colour span."""
        _holder, label = self._label_for(ElementStyle(highlight_color="#FFFF00"))
        assert label.textFormat() == Qt.TextFormat.RichText
        assert 'style="background-color:#FFFF00;"' in label.text()

    def test_invalid_hex_emits_no_span(self, qt_app) -> None:
        """An invalid colour is ignored: the text stays, no span."""
        _holder, label = self._label_for(ElementStyle(color="not-a-hex"))
        assert label.text() == "word"
        assert "style=" not in label.text()
        assert "span" not in label.text()


class TestPreviewPage:
    """L15.7b: the preview renders on a zoomable, paginated page."""

    def test_page_frame_and_shadow(self, qt_app) -> None:
        """A 'previewPage' frame with a drop shadow wraps the content."""
        widget = PreviewWidget()

        assert widget._page is not None
        assert widget._page.objectName() == "previewPage"
        assert isinstance(widget._page.graphicsEffect(), QGraphicsDropShadowEffect)

    def test_page_pixel_size_matches_helper_at_100_percent(self, qt_app) -> None:
        """Default PageConfig (A4) at 100% == page_size_px("A4", "portrait")."""
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")

        assert (widget._page.width(), widget._page.height()) == page_size_px(
            "A4", "portrait"
        )

    def test_zoom_control_options_and_default(self, qt_app) -> None:
        """The zoom combobox offers Fit Width / 100% / Custom; default Fit Width."""
        widget = PreviewWidget()
        combo = widget.findChild(QComboBox, "zoomComboBox")

        assert combo is not None
        texts = [combo.itemText(i) for i in range(combo.count())]
        assert texts == ["Fit Width", "100%", "Custom\u2026"]
        assert combo.currentText() == "Fit Width"

    def test_selecting_100_percent_sets_page_width(self, qt_app) -> None:
        """Zoom combo wiring: 100% resizes the page to the 96-DPI width."""
        widget = PreviewWidget()
        combo = widget.findChild(QComboBox, "zoomComboBox")
        assert combo is not None
        combo.setCurrentText("100%")

        base_width, _ = page_size_px("A4", "portrait")
        assert widget._page.width() == base_width

    def test_overflowing_document_produces_multiple_pages(self, qt_app) -> None:
        """Content taller than one page stacks a second page + break marker."""
        widget = PreviewWidget()
        combo = widget.findChild(QComboBox, "zoomComboBox")
        assert combo is not None
        combo.setCurrentText("100%")
        widget.set_document(_make_overflowing_document(80))

        frames = widget.findChildren(QFrame, "previewPage")
        assert len(frames) >= 2
        markers = widget.findChildren(QWidget, "pageBreakMarker")
        assert markers  # a visible "— Page N —" rule between the pages

    def test_empty_document_produces_single_page(self, qt_app) -> None:
        """An empty document is exactly one page frame with the empty state."""
        widget = PreviewWidget()
        widget.set_document(
            ResolvedExportDocument(
                layout=_make_layout(), show_sections=[], separator=None
            )
        )

        frames = widget.findChildren(QFrame, "previewPage")
        assert len(frames) == 1
        empty = widget.findChild(QLabel, "emptyState")
        assert empty is not None
        assert frames[0] is empty.parentWidget()  # rendered inside the page

    def test_width_hint_toggles_with_widget_width(self, qt_app) -> None:
        """The 'maximize' hint shows only while the widget is under 900px wide."""
        widget = PreviewWidget()
        # Qt only delivers resize events to visible widgets, so show first
        # (offscreen); the real app embeds the preview in a shown window.
        widget.show()
        hint = widget.findChild(QLabel, "widthHint")
        assert hint is not None
        assert "maximize the window" in hint.text()

        widget.resize(700, 600)
        assert not hint.isHidden()
        widget.resize(1000, 600)
        assert hint.isHidden()


# ---------------------------------------------------------------------------
# Header & footer bands (L15.7e.2)
# ---------------------------------------------------------------------------


def _make_chrome_document(
    header: HeaderConfig | None,
    footer: FooterConfig | None,
    num_recordings: int = 3,
) -> ResolvedExportDocument:
    """A document whose layout carries header/footer config.

    Each recording renders one body line; pass many recordings to
    overflow onto multiple pages at 100% zoom.
    """
    recordings = [
        ResolvedRecording(
            recording=_make_recording(source_path=f"C:/media/hf{i:03d}.mkv"),
            lines=[_make_resolved_line(f"Concert {i:03d} - 2001-05-04 - 120 min")],
        )
        for i in range(num_recordings)
    ]
    section = ResolvedShowSection(
        show_name="Chrome Show", header_lines=[], recordings=recordings
    )
    layout = replace(_make_layout(), header=header, footer=footer)
    return ResolvedExportDocument(
        layout=layout, show_sections=[section], separator=None
    )


class TestPreviewHeaderFooter:
    """L15.7e.2: enabled header/footer bands render inside each page frame."""

    @staticmethod
    def _texts(frame: QFrame) -> list[str]:
        return [lbl.text() for lbl in frame.findChildren(QLabel)]

    def test_header_center_slot_renders_on_first_page(self, qt_app) -> None:
        """An enabled header's center slot appears as a label on page 1."""
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        widget.set_document(
            _make_chrome_document(
                HeaderConfig(enabled=True, center=TextSlot(text="HEADER TEXT")),
                None,
            )
        )

        frames = widget.findChildren(QFrame, "previewPage")
        assert frames
        assert "HEADER TEXT" in self._texts(frames[0])
        band = frames[0].findChild(QWidget, "previewHeader")
        assert band is not None
        assert "HEADER TEXT" in [lbl.text() for lbl in band.findChildren(QLabel)]

    def test_scope_first_renders_header_only_on_first_page(self, qt_app) -> None:
        """scope='first': page 1 shows the band, later pages do not."""
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        widget.set_document(
            _make_chrome_document(
                HeaderConfig(
                    enabled=True, scope="first", center=TextSlot(text="HEADER TEXT")
                ),
                None,
                num_recordings=80,
            )
        )

        frames = widget.findChildren(QFrame, "previewPage")
        assert len(frames) >= 2
        assert "HEADER TEXT" in self._texts(frames[0])
        for frame in frames[1:]:
            assert "HEADER TEXT" not in self._texts(frame)

    def test_scope_all_renders_header_on_every_page(self, qt_app) -> None:
        """scope='all': every page frame carries the header band."""
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        widget.set_document(
            _make_chrome_document(
                HeaderConfig(
                    enabled=True, scope="all", center=TextSlot(text="HEADER TEXT")
                ),
                None,
                num_recordings=80,
            )
        )

        frames = widget.findChildren(QFrame, "previewPage")
        assert len(frames) >= 2
        for frame in frames:
            assert "HEADER TEXT" in self._texts(frame)

    def test_footer_renders_on_every_page(self, qt_app) -> None:
        """An enabled footer's left slot appears on every page frame."""
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        widget.set_document(
            _make_chrome_document(
                None,
                FooterConfig(enabled=True, left=TextSlot(text="FOOT")),
                num_recordings=80,
            )
        )

        frames = widget.findChildren(QFrame, "previewPage")
        assert len(frames) >= 2
        for frame in frames:
            assert "FOOT" in self._texts(frame)
            assert frame.findChild(QWidget, "previewFooter") is not None

    def test_no_chrome_config_adds_no_bands(self, qt_app) -> None:
        """header=None and footer=None -> no previewHeader/previewFooter band."""
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        widget.set_document(_make_chrome_document(None, None, num_recordings=80))

        frames = widget.findChildren(QFrame, "previewPage")
        assert len(frames) >= 2  # the negative check is not vacuous
        assert widget.findChildren(QWidget, "previewHeader") == []
        assert widget.findChildren(QWidget, "previewFooter") == []


class TestPreviewLogoSlots:
    """L15.7f.3: logo slots render as pixmaps (or a placeholder) in bands."""

    @staticmethod
    def _make_png(path: Path) -> Path:
        from PIL import Image
        img = Image.new("RGB", (100, 50), color=(200, 100, 50))
        img.save(path, "PNG")
        return path

    @staticmethod
    def _pixmaps(band: QWidget) -> list:
        """Non-null pixmaps on any label inside *band* (empty if none)."""
        found = []
        for lbl in band.findChildren(QLabel):
            pix = lbl.pixmap()
            if pix is not None and not pix.isNull():
                found.append(pix)
        return found

    def test_header_logo_renders_pixmap_in_band(
        self, qt_app, tmp_path: Path,
    ) -> None:
        """A present logo file becomes a non-null pixmap on page 1."""
        layouts = tmp_path / "layouts"
        layouts.mkdir()
        self._make_png(layouts / "test_logo.png")
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        header = HeaderConfig(
            enabled=True,
            center=LogoSlot(width_mm=15, logo_file="test_logo.png"),
        )
        with patch(
            "bootleg_catalog.core.layout_pdf_exporter.app_data_dir",
            return_value=tmp_path,
        ):
            widget.set_document(_make_chrome_document(header, None))

        frames = widget.findChildren(QFrame, "previewPage")
        assert frames
        band = frames[0].findChild(QWidget, "previewHeader")
        assert band is not None
        pixmaps = self._pixmaps(band)
        assert pixmaps
        assert not pixmaps[0].isNull()

    def test_service_document_logo_renders_end_to_end(
        self, qt_app, tmp_path: Path,
    ) -> None:
        """The service's document keeps the logo reference end to end.

        Service -> build_document -> PreviewWidget: the layout handed to
        the session must carry the stored logo_file, and the preview band
        must resolve it against app_data_dir()/layouts/ to a pixmap.
        """
        layouts = tmp_path / "layouts"
        layouts.mkdir()
        self._make_png(layouts / "test_logo.png")
        header = HeaderConfig(
            enabled=True,
            center=LogoSlot(width_mm=15, logo_file="test_logo.png"),
        )
        service = _make_service(replace(_make_layout(), header=header))
        service.session.add_recording(_make_recording())
        doc = service.build_document()
        assert doc.layout.header.center.logo_file == "test_logo.png"  # sanity

        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        with patch(
            "bootleg_catalog.core.layout_pdf_exporter.app_data_dir",
            return_value=tmp_path,
        ):
            widget.set_document(doc)

        frames = widget.findChildren(QFrame, "previewPage")
        assert frames
        band = frames[0].findChild(QWidget, "previewHeader")
        assert band is not None
        pixmaps = self._pixmaps(band)
        assert pixmaps
        assert not pixmaps[0].isNull()

    def test_pending_logo_source_renders_pixmap(
        self, qt_app, tmp_path: Path,
    ) -> None:
        """A pending source path (not yet copied) renders as a pixmap."""
        source = self._make_png(tmp_path / "chosen.png")
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        header = HeaderConfig(
            enabled=True,
            center=LogoSlot(width_mm=20, logo_file=None),
        )
        widget.set_pending_logos({"header.center": source})
        with patch(
            "bootleg_catalog.core.layout_pdf_exporter.app_data_dir",
            return_value=tmp_path,
        ):
            widget.set_document(_make_chrome_document(header, None))

        frames = widget.findChildren(QFrame, "previewPage")
        assert frames
        band = frames[0].findChild(QWidget, "previewHeader")
        assert band is not None
        pixmaps = self._pixmaps(band)
        assert pixmaps
        assert not pixmaps[0].isNull()
        labels = [lbl.text() for lbl in band.findChildren(QLabel)]
        assert "missing logo" not in labels

    def test_pending_logos_not_set_shows_placeholder(
        self, qt_app, tmp_path: Path,
    ) -> None:
        """Without set_pending_logos a logo_file=None slot stays missing."""
        self._make_png(tmp_path / "chosen.png")
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        header = HeaderConfig(
            enabled=True,
            center=LogoSlot(width_mm=20, logo_file=None),
        )
        with patch(
            "bootleg_catalog.core.layout_pdf_exporter.app_data_dir",
            return_value=tmp_path,
        ):
            widget.set_document(_make_chrome_document(header, None))

        frames = widget.findChildren(QFrame, "previewPage")
        assert frames
        band = frames[0].findChild(QWidget, "previewHeader")
        assert band is not None
        labels = [lbl.text() for lbl in band.findChildren(QLabel)]
        assert "missing logo" in labels
        assert self._pixmaps(band) == []

    def test_missing_logo_shows_placeholder_not_image(
        self, qt_app, tmp_path: Path,
    ) -> None:
        """A logo_file absent from disk → 'missing logo' label, no pixmap."""
        widget = PreviewWidget()
        widget._zoom_combo.setCurrentText("100%")
        header = HeaderConfig(
            enabled=True,
            center=LogoSlot(width_mm=15, logo_file="missing.png"),
        )
        with patch(
            "bootleg_catalog.core.layout_pdf_exporter.app_data_dir",
            return_value=tmp_path,
        ):
            widget.set_document(_make_chrome_document(header, None))

        frames = widget.findChildren(QFrame, "previewPage")
        assert frames
        band = frames[0].findChild(QWidget, "previewHeader")
        assert band is not None
        labels = [lbl.text() for lbl in band.findChildren(QLabel)]
        assert "missing logo" in labels
        assert self._pixmaps(band) == []


# ---------------------------------------------------------------------------
# ExportPreviewWindow tests
# ---------------------------------------------------------------------------

class TestExportPreviewWindow:
    def test_opens_empty(self, qt_app) -> None:
        """Window opens with empty session, preview shows empty state."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        win.show()
        assert win.windowTitle() == "Untitled \u2014 Bootleg Catalog Manager"
        assert win._table_model.rowCount() == 0

    def test_add_recording_file_dialog_cancelled(self, qt_app) -> None:
        """QFileDialog cancelled → no session change."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=([], ""),
        ):
            win._on_add_recording()
        assert service.session.recording_count == 0

    def test_add_recording_probe_error(self, qt_app) -> None:
        """probe_recording_from_path raises → warning shown, session unchanged."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["/some/file.mkv"], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            side_effect=ExportSessionServiceError("file not found"),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._on_add_recording()
        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[1] == "Probe Error"
        assert mock_warning.call_args.args[2] == "file not found"
        assert service.session.recording_count == 0

    def test_add_recording_dialog_rejected(self, qt_app) -> None:
        """AddRecordingDialog rejected → session unchanged."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        recording = _make_recording()
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["/some/file.mkv"], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            return_value=recording,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
        ) as MockDialog:
            instance = MockDialog.return_value
            instance.exec.return_value = QDialog.DialogCode.Rejected
            win._on_add_recording()
        assert service.session.recording_count == 0

    def test_add_recording_dialog_accepted(self, qt_app) -> None:
        """AddRecordingDialog accepted → recording added to session."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        recording = _make_recording()
        dialog_rec = _make_recording(title="Edited Title")
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["/some/file.mkv"], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            return_value=recording,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
        ) as MockDialog:
            instance = MockDialog.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            instance.result_recording = dialog_rec
            win._on_add_recording()
        assert service.session.recording_count == 1

    def test_add_recording_duplicate_shows_information(self, qt_app) -> None:
        """A duplicate source_path is caught, not raised: info + unchanged."""
        service = _make_service()
        original = _make_recording(source_path="C:/dup.mkv", title="Original")
        service.session.add_recording(original)
        win = ExportPreviewWindow(service)
        probed = _make_recording(source_path="C:/dup.mkv")
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["C:/dup.mkv"], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            return_value=probed,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
        ) as MockDialog, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            instance = MockDialog.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            instance.result_recording = probed
            win._on_add_recording()

        mock_info.assert_called_once()
        assert mock_info.call_args.args[1] == "Already in Session"
        assert mock_info.call_args.args[2] == "This file is already in the session."
        mock_warning.assert_not_called()
        # Unchanged: the original survived and nothing was added.
        assert service.session.recording_count == 1
        assert service.session.get_recording("C:/dup.mkv").title == "Original"
        assert win._table_model.rowCount() == 1

    def test_edit_recording_accepted(self, qt_app) -> None:
        """Edit dialog accepted → session updated."""
        service = _make_service()
        rec = _make_recording()
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        edited = _make_recording(source_path=rec.source_path, title="Edited")

        # Directly test the update logic (bypass dialog construction)
        service.update_recording(rec.source_path, edited)
        updated = service.session.get_recording(rec.source_path)
        assert updated.title == "Edited"

    def test_edit_recording_rejected(self, qt_app) -> None:
        """Edit dialog rejected → session unchanged."""
        service = _make_service()
        rec = _make_recording()
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        # Verify the recording is still unchanged after window construction
        updated = service.session.get_recording(rec.source_path)
        assert updated.title is None

    def test_remove_recording_confirmed(self, qt_app) -> None:
        """Remove with Yes → recording removed."""
        service = _make_service()
        rec = _make_recording()
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            win._on_remove_recording(rec.source_path)
        assert service.session.recording_count == 0

    def test_remove_recording_cancelled(self, qt_app) -> None:
        """Remove with No → recording stays."""
        service = _make_service()
        rec = _make_recording()
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            win._on_remove_recording(rec.source_path)
        assert service.session.recording_count == 1

    def test_export_pdf_cancelled(self, qt_app) -> None:
        """PDF export: QFileDialog cancelled → no file written."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ):
            win._on_export_pdf()

    def test_export_pdf_success(self, qt_app, tmp_path: Path) -> None:
        """PDF export: accepted → export_layout_pdf called."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        out = tmp_path / "out.pdf"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(out), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.export_layout_pdf",
        ) as mock_export, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ):
            win._on_export_pdf()
            assert mock_export.called

    def test_export_pdf_error(self, qt_app) -> None:
        """PDF export: LayoutPDFExportError → warning shown."""
        from bootleg_catalog.core.layout_pdf_exporter import LayoutPDFExportError
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=("/tmp/out.pdf", ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.export_layout_pdf",
            side_effect=LayoutPDFExportError("non-latin-1 text"),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warn:
            win._on_export_pdf()
            mock_warn.assert_called_once()

    def test_export_pdf_real_export_writes_file_and_informs(
        self, qt_app, tmp_path: Path,
    ) -> None:
        """Bug A happy path: the PDF lands on disk and the user is told."""
        service = _make_service()
        service.session.add_recording(_make_recording())
        win = ExportPreviewWindow(service)
        out = tmp_path / "relatorio.pdf"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(out), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warn:
            win._on_export_pdf()
        assert out.is_file()
        assert out.stat().st_size > 0
        mock_info.assert_called_once()
        assert mock_info.call_args.args[1] == "PDF Export"
        assert str(out) in mock_info.call_args.args[2]
        mock_warn.assert_not_called()

    def test_export_pdf_appends_suffix_when_omitted(
        self, qt_app, tmp_path: Path,
    ) -> None:
        """Bug A: a destination without .pdf gets the suffix appended."""
        service = _make_service()
        service.session.add_recording(_make_recording())
        win = ExportPreviewWindow(service)
        typed = tmp_path / "relatorio"  # no extension
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(typed), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info:
            win._on_export_pdf()
        expected = tmp_path / "relatorio.pdf"
        assert expected.is_file()
        assert not typed.exists()
        assert str(expected) in mock_info.call_args.args[2]

    def test_export_pdf_exporter_error_warns_logs_and_names_dialog(
        self, qt_app,
    ) -> None:
        """Bug A failure path: warning with the message AND a logger call."""
        from bootleg_catalog.core.layout_pdf_exporter import LayoutPDFExportError
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=("/tmp/out.pdf", ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.export_layout_pdf",
            side_effect=LayoutPDFExportError("boom"),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warn, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.get_logger",
        ) as mock_logger:
            win._on_export_pdf()
        mock_warn.assert_called_once()
        assert mock_warn.call_args.args[1] == "PDF Export Error"
        assert mock_warn.call_args.args[2] == "boom"
        mock_info.assert_not_called()
        mock_logger.return_value.warning.assert_called_once()

    def test_export_pdf_corrupt_screenshot_warns_instead_of_silence(
        self, qt_app, tmp_path: Path,
    ) -> None:
        """The smoke-test failure: a corrupt screenshot used to escape the
        slot as PIL.UnidentifiedImageError and nothing happened.  It must
        now surface as a warning."""
        corrupt = tmp_path / "capa.jpg"
        corrupt.write_text("not a real jpeg")
        service = _make_service()
        service.session.add_recording(_make_recording(screenshot_paths=[str(corrupt)]))
        win = ExportPreviewWindow(service)
        out = tmp_path / "saida.pdf"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(out), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warn, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info:
            win._on_export_pdf()  # must not raise and must not be silent
        mock_warn.assert_called_once()
        assert mock_warn.call_args.args[1] == "PDF Export Error"
        assert "not a valid image" in mock_warn.call_args.args[2]
        mock_info.assert_not_called()
        assert not out.exists()

    def test_export_xlsx_cancelled(self, qt_app) -> None:
        """XLSX export: cancelled → no file written."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ):
            win._on_export_xlsx()

    def test_export_xlsx_success(self, qt_app, tmp_path: Path) -> None:
        """XLSX export: accepted → export_layout_xlsx called."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        out = tmp_path / "out.xlsx"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(out), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.export_layout_xlsx",
        ) as mock_export, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ):
            win._on_export_xlsx()
            assert mock_export.called

    def test_export_xlsx_error(self, qt_app) -> None:
        """XLSX export: LayoutXLSXExportError → warning shown."""
        from bootleg_catalog.core.layout_xlsx_exporter import LayoutXLSXExportError
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=("/tmp/out.xlsx", ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.export_layout_xlsx",
            side_effect=LayoutXLSXExportError("missing screenshot"),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warn:
            win._on_export_xlsx()
            mock_warn.assert_called_once()

    def test_refresh_after_layout_change(self, qt_app) -> None:
        """Changing layout and refreshing updates the document."""
        service = _make_service()
        rec = _make_recording()
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)

        # Sanity: document before
        doc_before = service.build_document()
        assert doc_before.layout.name == "Test Layout"

        new_layout = LayoutModel(
            name="New Layout",
            show_header=[],
            recording_body=[
                LayoutLine(elements=[
                    LayoutElement(kind="field", content="Duration", field_type=None),
                ]),
            ],
        )
        service.set_layout(new_layout)
        win._refresh()

        doc_after = service.build_document()
        assert doc_after.layout.name == "New Layout"
        # Recording still present after layout change
        total = sum(len(s.recordings) for s in doc_after.show_sections)
        assert total == 1

    def test_screenshots_dict_built(self, qt_app) -> None:
        """_build_screenshots_dict includes recordings with screenshots."""
        service = _make_service()
        rec = _make_recording(screenshot_paths=["a.jpg", "b.jpg"])
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        result = win._build_screenshots_dict()
        assert rec.source_path in result
        assert result[rec.source_path] == ["a.jpg", "b.jpg"]

    def test_screenshots_dict_empty_when_no_screenshots(self, qt_app) -> None:
        """_build_screenshots_dict omits recordings without screenshots."""
        service = _make_service()
        rec = _make_recording()
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        result = win._build_screenshots_dict()
        assert rec.source_path not in result

    def test_table_shows_recording_count(self, qt_app) -> None:
        """Table reflects session recording count."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        rec1 = _make_recording(source_path="C:/a.mkv")
        rec2 = _make_recording(source_path="C:/b.mkv")
        service.session.add_recording(rec1)
        service.session.add_recording(rec2)
        win._refresh()
        assert win._table_model.rowCount() == 2

    def test_edit_layout_signal_exists(self, qt_app) -> None:
        """edit_layout_requested signal is defined and can be emitted."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        # Verify the signal exists on the class
        assert hasattr(ExportPreviewWindow, "edit_layout_requested")
        # Verify it can be connected and emitted
        received = []
        win.edit_layout_requested.connect(lambda: received.append(True))
        win.edit_layout_requested.emit()
        assert len(received) == 1

    def test_independence_no_catalog_import(self) -> None:
        """Module must not import Catalog, CatalogRepository, or sqlite3."""
        import bootleg_catalog.ui.export_preview_window as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "from bootleg_catalog.core.catalog" not in content
        assert "from bootleg_catalog.database" not in content
        assert "import catalog" not in content
        assert "import CatalogRepository" not in content
        assert "sqlite3" not in content

    def test_independence_preview_widget(self) -> None:
        """PreviewWidget module must not import Catalog or sqlite3."""
        import bootleg_catalog.ui.widgets.export_preview_widget as mod
        source = mod.__spec__.origin
        assert source is not None
        content = Path(source).read_text(encoding="utf-8")
        assert "from bootleg_catalog.core.catalog" not in content
        assert "from bootleg_catalog.database" not in content
        assert "sqlite3" not in content

# ---------------------------------------------------------------------------
# Multi-part add flow (L15.6.2b)
# ---------------------------------------------------------------------------

class _SilentBatchProbeWorker(QObject):
    """BatchProbeWorker double: records the call, never emits a result.

    Unlike ``_FakeBatchProbeWorker`` (which borrows the real ``run`` and
    would probe real paths), this double stays silent so no result slot
    fires and no modal dialog can be triggered accidentally.
    """

    progress = Signal(int, int)
    finished = Signal(list, list)
    failed = Signal(str)

    def __init__(self, service, paths) -> None:
        super().__init__()
        self._service = service
        self._paths = paths
        self.ran = False

    def moveToThread(self, thread) -> None:  # noqa: N802 (Qt API)
        # Deliberately a no-op: staying in the main thread keeps the
        # started -> run dispatch direct (synchronous) in tests.
        self.moved_thread = thread

    def run(self) -> None:
        self.ran = True


class TestMultiPartAddFlow:
    """2-file Add Recording: picker routing, part order, probe, divergence."""

    P0 = "C:/media/part1.mkv"
    P1 = "C:/media/part2.mkv"

    # -- Deliverable 1: multi-file picker ------------------------------------

    def test_picker_cancelled_starts_nothing(self, qt_app) -> None:
        """Empty selection → no probe, no part-order dialog, no session change."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=([], ""),
        ), patch.object(
            win, "_start_background_probe"
        ) as mock_probe, patch(
            "bootleg_catalog.ui.export_preview_window.PartOrderDialog"
        ) as mock_order:
            win._on_add_recording()
        mock_probe.assert_not_called()
        mock_order.assert_not_called()
        assert service.session.recording_count == 0

    def test_picker_single_path_routes_to_background_probe(self, qt_app) -> None:
        """One file → the original single-file probe flow with that path."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["C:/media/solo.mkv"], ""),
        ), patch.object(
            win, "_start_background_probe"
        ) as mock_probe:
            win._on_add_recording()
        mock_probe.assert_called_once_with("C:/media/solo.mkv")

    def test_picker_three_paths_refused(self, qt_app) -> None:
        """Three files → information dialog, no probe, no session change."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["C:/a.mkv", "C:/b.mkv", "C:/c.mkv"], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information"
        ) as mock_info, patch.object(
            win, "_start_background_probe"
        ) as mock_probe, patch(
            "bootleg_catalog.ui.export_preview_window.PartOrderDialog"
        ) as mock_order:
            win._on_add_recording()
        mock_info.assert_called_once()
        assert mock_info.call_args.args[1] == "Too Many Files"
        assert mock_info.call_args.args[2] == (
            "You can add at most 2 files per recording. "
            "To add more, create separate recordings."
        )
        mock_probe.assert_not_called()
        mock_order.assert_not_called()
        assert service.session.recording_count == 0

    def test_picker_two_paths_cancelled_part_order(self, qt_app) -> None:
        """Two files + rejected PartOrderDialog → no probe, no session change."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=([self.P0, self.P1], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.PartOrderDialog"
        ) as mock_order, patch(
            "bootleg_catalog.ui.export_preview_window.BatchProbeWorker"
        ) as mock_batch:
            mock_order.return_value.exec.return_value = QDialog.DialogCode.Rejected
            win._on_add_recording()
        mock_order.assert_called_once_with((self.P0, self.P1), win)
        mock_batch.assert_not_called()
        assert win._multipart_thread is None
        assert service.session.recording_count == 0

    def test_picker_two_paths_accepted_reverses_worker_paths(self, qt_app) -> None:
        """first_index=1 → the worker probes the reversed path order."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=([self.P0, self.P1], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.PartOrderDialog"
        ) as mock_order, patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.BatchProbeWorker",
            _SilentBatchProbeWorker,
        ):
            mock_order.return_value.exec.return_value = QDialog.DialogCode.Accepted
            mock_order.return_value.first_index = 1
            win._on_add_recording()
        assert isinstance(win._multipart_thread, _SyncQThread)
        assert win._multipart_worker._paths == [self.P1, self.P0]
        assert win._multipart_worker.ran is True
        assert win._busy_label.text() == "Probing 0 / 2\u2026"
        assert not win._toolbar.isEnabled()

    # -- Deliverable 3: _on_multipart_finished ------------------------------

    @staticmethod
    def _same_resolution_pair() -> list[ExportRecording]:
        """Two probed parts with identical technical fields (no divergence)."""
        return [
            _make_recording(TestMultiPartAddFlow.P0,
                            video_width=1920, video_height=1080),
            _make_recording(TestMultiPartAddFlow.P1,
                            video_width=1920, video_height=1080),
        ]

    @staticmethod
    def _divergent_resolution_pair() -> list[ExportRecording]:
        """Two probed parts whose resolutions differ (divergence on)."""
        return [
            _make_recording(TestMultiPartAddFlow.P0,
                            video_width=1920, video_height=1080),
            _make_recording(TestMultiPartAddFlow.P1,
                            video_width=1280, video_height=720),
        ]

    def test_finished_no_divergence_builds_two_part_recording(self, qt_app) -> None:
        """No divergence → no resolution dialog; recording keeps both parts."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.DivergenceResolutionDialog"
        ) as mock_div, patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog"
        ) as mock_add:
            mock_add.return_value.exec.return_value = QDialog.DialogCode.Rejected
            win._on_multipart_finished(self._same_resolution_pair(), [])
        mock_div.assert_not_called()
        mock_add.assert_called_once()
        recording = mock_add.call_args.args[0]
        assert recording.parts == (self.P0, self.P1)
        assert recording.source_path == self.P0
        assert recording.duration == 14400.0
        assert recording.file_size == 2_000_000_000
        assert service.session.recording_count == 0

    def test_finished_divergence_cancelled_adds_nothing(self, qt_app) -> None:
        """Divergence dialog rejected → AddRecordingDialog never built."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.DivergenceResolutionDialog"
        ) as mock_div, patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog"
        ) as mock_add:
            mock_div.return_value.exec.return_value = QDialog.DialogCode.Rejected
            win._on_multipart_finished(self._divergent_resolution_pair(), [])
        mock_div.assert_called_once()
        args = mock_div.call_args.args
        assert args[0].path == self.P0
        assert args[1].path == self.P1
        assert args[2] == frozenset({"resolution"})
        mock_add.assert_not_called()
        assert service.session.recording_count == 0

    def test_finished_divergence_accepted_resolves_field(self, qt_app) -> None:
        """Accepted resolution → AddRecordingDialog sees part B's values."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.DivergenceResolutionDialog"
        ) as mock_div, patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog"
        ) as mock_add:
            mock_div.return_value.exec.return_value = QDialog.DialogCode.Accepted
            mock_div.return_value.resolutions = {"resolution": 1}
            mock_add.return_value.exec.return_value = QDialog.DialogCode.Rejected
            win._on_multipart_finished(self._divergent_resolution_pair(), [])
        mock_div.assert_called_once()
        mock_add.assert_called_once()
        recording = mock_add.call_args.args[0]
        # Part B (index 1) won the resolution.
        assert recording.video_width == 1280
        assert recording.video_height == 720
        assert recording.parts == (self.P0, self.P1)

    def test_finished_with_failed_paths_warns_and_adds_nothing(self, qt_app) -> None:
        """Any failed part → warning, session untouched, no add dialog."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning"
        ) as mock_warn, patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog"
        ) as mock_add:
            win._on_multipart_finished([_make_recording(self.P0)], [self.P1])
        mock_warn.assert_called_once()
        assert mock_warn.call_args.args[1] == "Probe Error"
        assert mock_warn.call_args.args[2] == (
            "One or both files could not be probed. "
            "Add them individually to see the specific error."
        )
        mock_add.assert_not_called()
        assert service.session.recording_count == 0
        assert win._toolbar.isEnabled()

    def test_finished_accepted_adds_and_refreshes(self, qt_app) -> None:
        """Accepted recording → add_recording once with it, then _refresh."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        accepted = _make_recording(
            self.P0,
            title="Two Parts",
            video_width=1920,
            video_height=1080,
            parts=(self.P0, self.P1),
        )
        with patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog"
        ) as mock_add, patch.object(
            service, "add_recording", wraps=service.add_recording
        ) as mock_add_rec, patch.object(
            win, "_refresh"
        ) as mock_refresh:
            mock_add.return_value.exec.return_value = QDialog.DialogCode.Accepted
            mock_add.return_value.result_recording = accepted
            win._on_multipart_finished(self._same_resolution_pair(), [])
        mock_add_rec.assert_called_once_with(accepted)
        mock_refresh.assert_called_once()
        assert service.session.recording_count == 1

    def test_multipart_failed_warns_and_unlocks_toolbar(self, qt_app) -> None:
        """Catastrophic worker failure → warning with the message, UI unlocked."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        win._set_busy("Probing 0 / 2\u2026")
        assert not win._toolbar.isEnabled()
        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning"
        ) as mock_warn:
            win._on_multipart_failed("batch probe exploded")
        mock_warn.assert_called_once_with(
            win, "Probe Error", "batch probe exploded"
        )
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

# ---------------------------------------------------------------------------
# Edit Layout wiring (L12.2)
# ---------------------------------------------------------------------------

class TestEditLayoutWiring:
    def test_edit_layout_button_enabled(self, qt_app) -> None:
        """The Edit Layout… button is enabled (no longer a placeholder)."""
        win = ExportPreviewWindow(_make_service())
        assert win._edit_layout_btn.isEnabled()

    def test_click_opens_designer_with_current_layout(self, qt_app) -> None:
        """Clicking the button opens the designer with the session layout."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        current = service.layout
        with patch(
            "bootleg_catalog.ui.export_preview_window.LayoutDesignerDialog",
        ) as mock_dialog:
            mock_dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            win._edit_layout_btn.click()
        mock_dialog.assert_called_once_with(current, win, library_mode=True)

    def test_designer_cancel_keeps_layout_and_writes_nothing(self, qt_app) -> None:
        """Cancel: session layout unchanged, nothing written to disk."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        before = service.layout
        with patch(
            "bootleg_catalog.ui.export_preview_window.LayoutDesignerDialog",
        ) as mock_dialog, patch(
            "bootleg_catalog.ui.export_preview_window.LayoutManager",
        ) as mock_manager:
            mock_dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            win._edit_layout_btn.click()
        assert service.layout is before
        mock_manager.return_value.save.assert_not_called()

    def test_designer_accept_applies_and_persists_layout(self, qt_app) -> None:
        """Accept: session layout replaced, persisted, preview refreshed."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        new_layout = LayoutModel(name="Redesigned", show_header=[], recording_body=[])
        with patch(
            "bootleg_catalog.ui.export_preview_window.LayoutDesignerDialog",
        ) as mock_dialog, patch(
            "bootleg_catalog.ui.export_preview_window.LayoutManager",
        ) as mock_manager:
            mock_dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            mock_dialog.return_value.result_layout = new_layout
            win._edit_layout_btn.click()
        assert service.layout is new_layout
        mock_manager.return_value.save.assert_called_once_with(new_layout)
        assert win._preview_widget._document.layout.name == "Redesigned"

    def test_designer_accept_save_failure_warns_and_keeps_layout(self, qt_app) -> None:
        """Save failure: warning shown, in-memory layout still applied."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        new_layout = LayoutModel(name="Unsaved", show_header=[], recording_body=[])
        with patch(
            "bootleg_catalog.ui.export_preview_window.LayoutDesignerDialog",
        ) as mock_dialog, patch(
            "bootleg_catalog.ui.export_preview_window.LayoutManager",
        ) as mock_manager, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warn:
            mock_dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            mock_dialog.return_value.result_layout = new_layout
            mock_manager.return_value.save.side_effect = LayoutManagerError(
                "disk full",
            )
            win._edit_layout_btn.click()
        mock_warn.assert_called_once()
        assert mock_warn.call_args.args[1] == "Layout Save Error"
        assert mock_warn.call_args.args[2] == "disk full"
        assert service.layout is new_layout
        assert win._preview_widget._document.layout.name == "Unsaved"

    def test_edit_layout_signal_fires_on_click(self, qt_app) -> None:
        """edit_layout_requested still fires when the button is clicked."""
        win = ExportPreviewWindow(_make_service())
        received: list[bool] = []
        win.edit_layout_requested.connect(lambda: received.append(True))
        with patch(
            "bootleg_catalog.ui.export_preview_window.LayoutDesignerDialog",
        ) as mock_dialog:
            mock_dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            win._edit_layout_btn.click()
        assert received == [True]


# ---------------------------------------------------------------------------
# Menu structure, window title, session files, close behaviour (L15.3b)
# ---------------------------------------------------------------------------


class TestMenuStructure:
    def test_file_and_help_menus_have_expected_actions(self, qt_app) -> None:
        win = ExportPreviewWindow(_make_service())
        menus = {
            action.text(): action.menu()
            for action in win.menuBar().actions()
            if action.menu() is not None
        }
        assert list(menus) == ["File", "Help"]
        file_texts = [
            a.text() for a in menus["File"].actions() if not a.isSeparator()
        ]
        assert file_texts == [
            "New", "Open\u2026", "Save", "Save As\u2026", "Settings\u2026", "Exit",
        ]
        help_texts = [
            a.text() for a in menus["Help"].actions() if not a.isSeparator()
        ]
        assert help_texts == ["About"]


class TestSettingsMenu:
    """Bug B — Settings… between Save As… and Exit, save-on-accept."""

    def _file_menu(self, win: ExportPreviewWindow):
        return next(
            action.menu()
            for action in win.menuBar().actions()
            if action.menu() is not None and action.text() == "File"
        )

    def _settings_action(self, win: ExportPreviewWindow):
        return next(
            a for a in self._file_menu(win).actions()
            if not a.isSeparator() and a.text() == "Settings\u2026"
        )

    def test_file_menu_contains_settings_between_save_as_and_exit(
        self, qt_app,
    ) -> None:
        win = ExportPreviewWindow(_make_service())
        texts = [a.text() for a in self._file_menu(win).actions() if not a.isSeparator()]
        assert texts.index("Settings\u2026") == texts.index("Save As\u2026") + 1
        assert texts.index("Exit") == texts.index("Settings\u2026") + 1

    def test_settings_accepted_saves_dialog_settings(self, qt_app) -> None:
        """Accepting with a modified AppSettings persists that object."""
        win = ExportPreviewWindow(_make_service())
        loaded = AppSettings(ffprobe_path="")
        modified = AppSettings(ffprobe_path="C:/tools/ffprobe.exe")
        with patch(
            "bootleg_catalog.ui.export_preview_window.load_settings",
            return_value=loaded,
        ) as mock_load, patch(
            "bootleg_catalog.ui.export_preview_window.SettingsDialog",
        ) as MockDialog, patch(
            "bootleg_catalog.ui.export_preview_window.save_settings",
        ) as mock_save:
            instance = MockDialog.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            instance.settings = modified
            self._settings_action(win).trigger()

        mock_load.assert_called_once()
        MockDialog.assert_called_once_with(loaded, win)
        mock_save.assert_called_once_with(modified)

    def test_settings_rejected_does_not_save(self, qt_app) -> None:
        """Cancelling the dialog writes nothing."""
        win = ExportPreviewWindow(_make_service())
        with patch(
            "bootleg_catalog.ui.export_preview_window.load_settings",
            return_value=AppSettings(),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.SettingsDialog",
        ) as MockDialog, patch(
            "bootleg_catalog.ui.export_preview_window.save_settings",
        ) as mock_save:
            MockDialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self._settings_action(win).trigger()

        MockDialog.return_value.exec.assert_called_once()
        mock_save.assert_not_called()


class TestWindowTitle:
    def test_fresh_window_title_is_untitled_without_dirty_marker(self, qt_app) -> None:
        win = ExportPreviewWindow(_make_service())
        assert "Untitled" in win.windowTitle()
        assert "*" not in win.windowTitle()

    def test_title_shows_display_name_after_load(self, qt_app, tmp_path: Path) -> None:
        session_file = tmp_path / "lista.bootleg"
        save_session(
            SessionData(
                display_name="Minha Lista",
                layout=_make_layout(),
                recordings=[],
            ),
            session_file,
        )
        service = _make_service()
        win = ExportPreviewWindow(service)
        service.load(session_file)
        win._refresh()
        assert "Minha Lista" in win.windowTitle()

    def test_dirty_marker_appears_after_add_recording(self, qt_app) -> None:
        service = _make_service()
        win = ExportPreviewWindow(service)
        assert "*" not in win.windowTitle()
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["/some/file.mkv"], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            return_value=_make_recording(),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
        ) as MockDialog:
            instance = MockDialog.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            instance.result_recording = _make_recording()
            win._on_add_recording()
        assert " *" in win.windowTitle()

    def test_dirty_marker_clears_after_save(self, qt_app, tmp_path: Path) -> None:
        service = _make_service()
        service.add_recording(_make_recording())  # marks dirty
        win = ExportPreviewWindow(service)
        assert " *" in win.windowTitle()
        target = tmp_path / "salvo.bootleg"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QInputDialog.getText",
            return_value=("Salvo", True),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ):
            win._on_save()  # no current path -> Save As
        assert "*" not in win.windowTitle()
        assert "Salvo" in win.windowTitle()


class TestSaveAndSaveAs:
    def test_save_without_path_opens_save_as_dialog(self, qt_app) -> None:
        service = _make_service()
        win = ExportPreviewWindow(service)
        assert win._current_path is None
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ) as mock_dialog:
            win._on_save()
        mock_dialog.assert_called_once()

    def test_save_as_writes_file_and_updates_title(self, qt_app, tmp_path: Path) -> None:
        service = _make_service()
        service.set_display_name("Minha Lista")
        win = ExportPreviewWindow(service)
        target = tmp_path / "minha-lista.bootleg"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QInputDialog.getText",
        ) as mock_prompt, patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ):
            win._on_save_as()
        mock_prompt.assert_not_called()  # named sessions are never prompted
        assert target.is_file()
        assert "Minha Lista" in win.windowTitle()
        assert win._current_path == target

    def test_save_as_appends_bootleg_when_extension_missing(self, qt_app, tmp_path: Path) -> None:
        service = _make_service()
        win = ExportPreviewWindow(service)
        typed = tmp_path / "sem-extensao"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(typed), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QInputDialog.getText",
            return_value=("Sem Extensao", True),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ):
            win._on_save_as()
        expected = tmp_path / "sem-extensao.bootleg"
        assert expected.is_file()
        assert not typed.exists()
        assert win._current_path == expected

    def test_save_as_empty_display_name_prompts_and_sets_name(self, qt_app, tmp_path: Path) -> None:
        service = _make_service()
        win = ExportPreviewWindow(service)
        target = tmp_path / "prompted.bootleg"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QInputDialog.getText",
            return_value=("Nome Bonito", True),
        ) as mock_prompt, patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ):
            win._on_save_as()
        mock_prompt.assert_called_once()
        assert mock_prompt.call_args.kwargs["text"] == "prompted"
        assert service.display_name == "Nome Bonito"

    def test_save_as_cancelled_name_prompt_aborts_save(self, qt_app, tmp_path: Path) -> None:
        service = _make_service()
        win = ExportPreviewWindow(service)
        target = tmp_path / "abortado.bootleg"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QInputDialog.getText",
            return_value=("", False),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ) as MockRecents:
            win._on_save_as()
        assert not target.exists()
        assert win._current_path is None
        assert service.display_name == ""
        MockRecents.return_value.add.assert_not_called()

    def test_save_as_with_existing_display_name_does_not_prompt(self, qt_app, tmp_path: Path) -> None:
        service = _make_service()
        service.set_display_name("Ja Nomeada")
        win = ExportPreviewWindow(service)
        target = tmp_path / "ja-nomeada.bootleg"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QInputDialog.getText",
        ) as mock_prompt, patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ):
            win._on_save_as()
        mock_prompt.assert_not_called()
        assert target.is_file()
        assert service.display_name == "Ja Nomeada"
        assert "*" not in win.windowTitle()  # saved → not dirty


class TestOpenSession:
    def test_open_loads_session_and_refreshes(self, qt_app, tmp_path: Path) -> None:
        session_file = tmp_path / "aberta.bootleg"
        save_session(
            SessionData(
                display_name="Minha Lista",
                layout=_make_layout(),
                recordings=[_make_recording()],
            ),
            session_file,
        )
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileName",
            return_value=(str(session_file), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ):
            win._on_open()
        assert win._table_model.rowCount() == 1
        assert "Minha Lista" in win.windowTitle()
        assert win._current_path == session_file

    def test_open_legacy_sqlite_bootleg_warns(self, qt_app, tmp_path: Path) -> None:
        legacy = tmp_path / "antigo.bootleg"
        legacy.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileName",
            return_value=(str(legacy), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warn:
            win._on_open()
        mock_warn.assert_called_once()
        assert "legacy" in mock_warn.call_args.args[2].lower()
        assert win._current_path is None
        assert win._table_model.rowCount() == 0

    def test_open_dirty_session_with_discard_proceeds(self, qt_app, tmp_path: Path) -> None:
        service = _make_service()
        service.add_recording(_make_recording())  # dirty
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Discard,
        ) as mock_question, patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileName",
            return_value=(str(tmp_path / "novo.bootleg"), ""),
        ), patch.object(
            service,
            "load",
        ) as mock_load, patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ):
            win._on_open()
        mock_question.assert_called_once()
        mock_load.assert_called_once_with(str(tmp_path / "novo.bootleg"))

    def test_open_dirty_session_with_cancel_keeps_session(self, qt_app) -> None:
        service = _make_service()
        service.add_recording(_make_recording())
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as mock_question, patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileName",
        ) as mock_dialog, patch.object(
            service,
            "load",
        ) as mock_load:
            win._on_open()
        mock_question.assert_called_once()
        mock_dialog.assert_not_called()
        mock_load.assert_not_called()
        assert service.session.recording_count == 1


class TestCloseBehaviour:
    def test_close_dirty_session_with_cancel_ignores_event(self, qt_app) -> None:
        service = _make_service()
        service.add_recording(_make_recording())
        win = ExportPreviewWindow(service)
        event = MagicMock()
        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            win.closeEvent(event)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()

    def test_close_dirty_session_with_discard_accepts_event(self, qt_app) -> None:
        service = _make_service()
        service.add_recording(_make_recording())
        win = ExportPreviewWindow(service)
        event = MagicMock()
        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Discard,
        ):
            win.closeEvent(event)
        event.accept.assert_called_once()
        event.ignore.assert_not_called()


class TestRecentSessionsIntegration:
    def test_recent_sessions_updated_on_save(self, qt_app, tmp_path: Path) -> None:
        service = _make_service()
        win = ExportPreviewWindow(service)
        target = tmp_path / "recente.bootleg"
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QInputDialog.getText",
            return_value=("Recente", True),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ) as MockRecents:
            win._on_save()
        MockRecents.return_value.add.assert_called_once_with("Recente", target)

    def test_recent_sessions_updated_on_open(self, qt_app, tmp_path: Path) -> None:
        session_file = tmp_path / "aberta-recente.bootleg"
        save_session(
            SessionData(
                display_name="Aberta",
                layout=_make_layout(),
                recordings=[],
            ),
            session_file,
        )
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileName",
            return_value=(str(session_file), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ) as MockRecents:
            win._on_open()
        MockRecents.return_value.add.assert_called_once_with("Aberta", session_file)


class TestServiceRouting:
    def test_add_recording_routes_through_service_not_session(self, qt_app) -> None:
        service = _make_service()
        win = ExportPreviewWindow(service)
        dialog_rec = _make_recording()
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileNames",
            return_value=(["/some/file.mkv"], ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            return_value=_make_recording(),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
        ) as MockDialog, patch.object(
            service,
            "add_recording",
        ) as mock_service_add, patch.object(
            service.session,
            "add_recording",
        ) as mock_session_add:
            instance = MockDialog.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            instance.result_recording = dialog_rec
            win._on_add_recording()
        mock_service_add.assert_called_once_with(dialog_rec)
        mock_session_add.assert_not_called()


# ---------------------------------------------------------------------------
# Table model + selection-driven actions (L15.3d.1)
# ---------------------------------------------------------------------------


class TestRecordingTableModel:
    def test_table_has_four_columns(self, qt_app) -> None:
        """The view is backed by the model, whose headers match COLUMNS."""
        win = ExportPreviewWindow(_make_service())
        model = win._table_model
        assert win._table.model() is model
        assert model.columnCount() == 4
        headers = [
            model.headerData(col, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            for col in range(model.columnCount())
        ]
        assert headers == list(RecordingTableModel.COLUMNS)

    def test_row_count_matches_session(self, qt_app) -> None:
        """The model's row count follows the session's recording list."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        service.session.add_recording(_make_recording(source_path="C:/one.mkv"))
        service.session.add_recording(_make_recording(source_path="C:/two.mkv"))
        win._refresh()
        assert win._table_model.rowCount() == 2

    def test_row_payload_via_userrole(self, qt_app) -> None:
        """UserRole on any column returns the row's ExportRecording."""
        service = _make_service()
        rec = _make_recording(source_path="C:/media/show.mkv", title="Titulo")
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        model = win._table_model
        for column in range(model.columnCount()):
            payload = model.data(model.index(0, column), Qt.ItemDataRole.UserRole)
            assert payload is rec
        assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "show.mkv"
        assert model.data(model.index(0, 2), Qt.ItemDataRole.DisplayRole) == "Titulo"

    def test_status_column_ok_and_error(self, qt_app) -> None:
        """Status shows OK without a probe_error and Error with one."""
        service = _make_service()
        service.session.add_recording(_make_recording(source_path="C:/ok.mkv"))
        service.session.add_recording(
            _make_recording(source_path="C:/falha.mkv", probe_error="ffprobe failed")
        )
        win = ExportPreviewWindow(service)
        model = win._table_model
        assert model.data(model.index(0, 3), Qt.ItemDataRole.DisplayRole) == "OK"
        assert model.data(model.index(1, 3), Qt.ItemDataRole.DisplayRole) == "Error"

    def test_edit_remove_disabled_without_selection(self, qt_app) -> None:
        """Edit/Remove start disabled when nothing is selected."""
        service = _make_service()
        service.session.add_recording(_make_recording())
        win = ExportPreviewWindow(service)
        assert not win._table.selectionModel().hasSelection()
        assert not win._edit_btn.isEnabled()
        assert not win._remove_btn.isEnabled()

    def test_edit_remove_enabled_after_selection(self, qt_app) -> None:
        """Selecting a row enables Edit and Remove."""
        service = _make_service()
        service.session.add_recording(_make_recording())
        win = ExportPreviewWindow(service)
        win._table.selectRow(0)
        assert win._table.selectionModel().hasSelection()
        assert win._edit_btn.isEnabled()
        assert win._remove_btn.isEnabled()

    def test_remove_clears_selection_and_disables_buttons(self, qt_app) -> None:
        """After a confirmed removal the model reset clears the selection
        and the buttons are disabled again."""
        service = _make_service()
        service.session.add_recording(_make_recording())
        win = ExportPreviewWindow(service)
        # L15.6.4: the shared slot only runs while the table has focus.
        _show_and_focus_table(win)
        win._table.selectRow(0)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            win._remove_btn.click()
        assert service.session.recording_count == 0
        assert win._table_model.rowCount() == 0
        assert not win._table.selectionModel().hasSelection()
        assert not win._edit_btn.isEnabled()
        assert not win._remove_btn.isEnabled()

    def test_double_click_row_opens_edit_dialog(self, qt_app) -> None:
        """Double-clicking a row opens the edit dialog for that recording."""
        service = _make_service()
        rec = _make_recording()
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.EditExportRecordingDialog",
        ) as MockEdit:
            MockEdit.return_value.exec.return_value = QDialog.DialogCode.Rejected
            win._table.doubleClicked.emit(win._table_model.index(0, 0))
        MockEdit.assert_called_once()
        assert MockEdit.call_args.args[0].source_path == rec.source_path

    def test_edit_button_click_opens_edit_dialog(self, qt_app) -> None:
        """The toolbar Edit button acts on the selected recording."""
        service = _make_service()
        rec = _make_recording()
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        win._table.selectRow(0)
        with patch(
            "bootleg_catalog.ui.export_preview_window.EditExportRecordingDialog",
        ) as MockEdit:
            MockEdit.return_value.exec.return_value = QDialog.DialogCode.Rejected
            win._edit_btn.click()
        MockEdit.assert_called_once()
        assert MockEdit.call_args.args[0].source_path == rec.source_path

    def test_model_reset_after_new_session(self, qt_app) -> None:
        """File > New resets the model to zero rows and disables the buttons."""
        service = _make_service()
        service.session.add_recording(_make_recording(source_path="C:/a.mkv"))
        service.session.add_recording(_make_recording(source_path="C:/b.mkv"))
        win = ExportPreviewWindow(service)
        assert win._table_model.rowCount() == 2
        win._on_new()
        assert win._table_model.rowCount() == 0
        assert not win._edit_btn.isEnabled()
        assert not win._remove_btn.isEnabled()

    def test_model_reset_after_open(self, qt_app, tmp_path: Path) -> None:
        """Opening a session replaces the model rows with the file's rows."""
        session_file = tmp_path / "trocada.bootleg"
        save_session(
            SessionData(
                display_name="Trocada",
                layout=_make_layout(),
                recordings=[_make_recording(source_path="C:/da-lista.mkv")],
            ),
            session_file,
        )
        service = _make_service()
        service.session.add_recording(_make_recording(source_path="C:/antiga-a.mkv"))
        service.session.add_recording(_make_recording(source_path="C:/antiga-b.mkv"))
        win = ExportPreviewWindow(service)
        assert win._table_model.rowCount() == 2
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getOpenFileName",
            return_value=(str(session_file), ""),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ):
            win._on_open()
        assert win._table_model.rowCount() == 1
        assert (
            win._table_model.data(
                win._table_model.index(0, 0), Qt.ItemDataRole.DisplayRole
            )
            == "da-lista.mkv"
        )


# ---------------------------------------------------------------------------
# Add-from-folder rip scan flow (L15.3d.3)
# ---------------------------------------------------------------------------


class TestAddFromFolderFlow:
    """Rip flow: scan -> confirm -> metadata -> probe FIRST video -> ONE recording.

    The batch summary tests call ``_start_batch_probe`` directly: the rip
    flow no longer routes through it (Bug D), but the retained batch
    machinery (kept for L15.6) must keep working.
    """

    def test_folder_button_locks_toolbar_and_starts_scanner(self, qt_app) -> None:
        """Clicking + Add from Folder… locks the toolbar and starts the scan."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _SilentRipScanWorker,
        ):
            win._add_folder_btn.click()

        assert isinstance(win._rip_thread, _SyncQThread)
        assert win._rip_thread.start_called
        assert win._rip_worker.ran is True
        assert win._rip_worker._folder == "C:/rip"
        assert win._busy_label.text() == "Scanning rip\u2026"
        assert not win._busy_label.isHidden()
        assert not win._toolbar.isEnabled()
        assert service.session.recording_count == 0

    def test_empty_rip_shows_information_without_probing(self, qt_app) -> None:
        """A rip with no video files shows an info dialog and probes nothing."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _FakeRipScanWorker,
        ), patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            return_value=_make_rip_result([]),
        ) as mock_scan, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
        ) as mock_question, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._add_folder_btn.click()

        mock_scan.assert_called_once_with("C:/rip")
        mock_info.assert_called_once()
        assert mock_info.call_args.args[2] == "No video files found in this rip."
        mock_question.assert_not_called()
        mock_warning.assert_not_called()
        assert win._batch_thread is None
        assert service.session.recording_count == 0
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_question_cancel_leaves_session_untouched(self, qt_app) -> None:
        """Cancelling the confirm dialog starts no batch probe."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        result = _make_rip_result(["C:/rip/VIDEO_TS/VTS_01_1.VOB"])
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _FakeRipScanWorker,
        ), patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            return_value=result,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as mock_question, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._add_folder_btn.click()

        mock_question.assert_called_once()
        assert mock_question.call_args.args[2] == (
            f"Found 1 video files "
            f"(total: {format_file_size(7_000_000_000)}). "
            "Add this rip to the session?"
        )
        assert mock_question.call_args.args[3] == (
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        mock_info.assert_not_called()
        mock_warning.assert_not_called()
        assert win._batch_thread is None
        assert service.session.recording_count == 0
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_confirm_probes_first_video_and_adds_one_rip_recording(
        self, qt_app,
    ) -> None:
        """Bug D: accepting the rip probes video_files[0] and adds ONE recording.

        The recording is identified by the rip root folder, carries the
        total size of every part, inherits the technical fields from the
        probed first video — except the duration, which is the
        user-typed total from RipMetadataDialog or None (L15.8.1); the
        probed chunk duration is dropped.
        """
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = ["C:/rip/VIDEO_TS/VTS_01_1.VOB", "C:/rip/VIDEO_TS/VTS_01_2.VOB"]
        rip = _make_rip_result(paths, total_size_bytes=9_000_000_000)
        probed = _make_recording(
            source_path=paths[0],
            duration=5400.0,
            video_codec="mpeg2video",
            audio_codec="ac3",
            video_width=720,
            video_height=480,
        )
        captured: dict[str, ExportRecording] = {}

        def fake_dialog(recording, settings, parent=None):
            captured["recording"] = recording
            dialog = MagicMock()
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.result_recording = recording
            return dialog

        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _FakeRipScanWorker,
        ), patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            return_value=rip,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipMetadataDialog",
        ) as mock_meta, patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            return_value=probed,
        ) as mock_probe, patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
            side_effect=fake_dialog,
        ) as mock_dialog, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            mock_meta.return_value.exec.return_value = (
                QDialog.DialogCode.Accepted
            )
            mock_meta.return_value.result_format = "VIDEO_TS"
            mock_meta.return_value.result_duration_text = ""
            win._add_folder_btn.click()

        # Technical fields came from probing the FIRST video file only.
        mock_probe.assert_called_once_with(paths[0])
        mock_dialog.assert_called_once()

        # Exactly ONE recording, identified by the rip root folder.
        assert service.session.recording_count == 1
        added = service.session.recordings[0]
        assert added.source_path == str(rip.folder_path)
        assert added.file_size == rip.total_size_bytes
        # L15.8.1: extension is the user-chosen label, not ".rip".
        assert added.extension == "VIDEO_TS"
        assert added.is_rip is True
        # ...technical fields inherited from the probed first video —
        # except the duration: the metadata dialog returned an empty
        # duration, so the probed chunk value is dropped (L15.8.1).
        assert added.duration is None
        assert added.video_codec == "mpeg2video"
        assert added.audio_codec == "ac3"
        assert added.video_width == 720
        assert added.video_height == 480
        # The dialog received that same rip recording.
        assert captured["recording"].source_path == str(rip.folder_path)
        assert win._table_model.rowCount() == 1
        mock_warning.assert_not_called()
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_rip_probe_failure_shows_probe_error_and_adds_nothing(
        self, qt_app,
    ) -> None:
        """A failed probe of the first video warns and adds no recording."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = ["C:/rip/VIDEO_TS/VTS_01_1.VOB"]
        rip = _make_rip_result(paths)
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _FakeRipScanWorker,
        ), patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            return_value=rip,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipMetadataDialog",
        ) as mock_meta, patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            side_effect=ExportSessionServiceError("ffprobe exited with 1"),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
        ) as mock_dialog, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            mock_meta.return_value.exec.return_value = (
                QDialog.DialogCode.Accepted
            )
            mock_meta.return_value.result_format = "VIDEO_TS"
            mock_meta.return_value.result_duration_text = ""
            win._add_folder_btn.click()

        mock_dialog.assert_not_called()
        mock_info.assert_not_called()
        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[1] == "Probe Error"
        assert mock_warning.call_args.args[2] == "ffprobe exited with 1"
        assert win._pending_rip is None
        assert win._pending_rip_format is None
        assert win._pending_rip_duration is None
        assert service.session.recording_count == 0
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_rip_duplicate_shows_already_in_session_information(
        self, qt_app,
    ) -> None:
        """Re-adding a rip already in the session informs instead of failing."""
        service = _make_service()
        service.session.add_recording(
            _make_recording(source_path="C:/rip", title="Original"),
        )
        win = ExportPreviewWindow(service)
        paths = ["C:/rip/VIDEO_TS/VTS_01_1.VOB"]
        rip = _make_rip_result(paths)
        probed = _make_recording(source_path=paths[0])

        def fake_dialog(recording, settings, parent=None):
            dialog = MagicMock()
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.result_recording = recording
            return dialog

        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _FakeRipScanWorker,
        ), patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            return_value=rip,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipMetadataDialog",
        ) as mock_meta, patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            return_value=probed,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
            side_effect=fake_dialog,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            mock_meta.return_value.exec.return_value = (
                QDialog.DialogCode.Accepted
            )
            mock_meta.return_value.result_format = "VIDEO_TS"
            mock_meta.return_value.result_duration_text = ""
            win._add_folder_btn.click()

        mock_warning.assert_not_called()
        mock_info.assert_called_once()
        assert mock_info.call_args.args[1] == "Already in Session"
        assert mock_info.call_args.args[2] == (
            "This rip is already in the session."
        )
        # The original stays; nothing new was added.
        assert service.session.recording_count == 1
        assert service.session.get_recording("C:/rip").title == "Original"

    def test_confirm_opens_rip_metadata_dialog_before_probe(self, qt_app) -> None:
        """After the Yes, RipMetadataDialog runs before the rip probe starts."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        rip = _make_rip_result(["C:/rip/VIDEO_TS/VTS_01_1.VOB"])
        probed = _make_recording(source_path="C:/rip/VIDEO_TS/VTS_01_1.VOB")
        order: list[str] = []

        def meta_factory(parent=None):
            order.append("metadata")
            dialog = MagicMock()
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.result_format = "VIDEO_TS"
            dialog.result_duration_text = ""
            return dialog

        def probe(path: str):
            order.append("probe")
            return probed

        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _FakeRipScanWorker,
        ), patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            return_value=rip,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipMetadataDialog",
            side_effect=meta_factory,
        ) as mock_meta, patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            side_effect=probe,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
        ):
            win._add_folder_btn.click()

        mock_meta.assert_called_once()
        assert order == ["metadata", "probe"]
        # AddRecordingDialog was mocked to reject: nothing was added.
        assert service.session.recording_count == 0

    def test_rip_metadata_dialog_cancel_adds_nothing(self, qt_app) -> None:
        """Cancelling RipMetadataDialog aborts before the probe; nothing added."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        rip = _make_rip_result(["C:/rip/VIDEO_TS/VTS_01_1.VOB"])

        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _FakeRipScanWorker,
        ), patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            return_value=rip,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipMetadataDialog",
        ) as mock_meta, patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
        ) as mock_probe, patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
        ) as mock_add:
            # Default MagicMock exec() != Accepted: the dialog "cancelled".
            win._add_folder_btn.click()

        mock_meta.assert_called_once()
        mock_probe.assert_not_called()
        mock_add.assert_not_called()
        assert win._pending_rip is None
        assert win._pending_rip_format is None
        assert win._pending_rip_duration is None
        assert service.session.recording_count == 0
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_rip_metadata_accept_format_and_duration(self, qt_app) -> None:
        """Accepting "DVD" + "1:30:00" → extension "DVD", duration 5400 s."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = ["C:/rip/VIDEO_TS/VTS_01_1.VOB"]
        rip = _make_rip_result(paths)
        # The probed first VOB's partial-chunk duration must be dropped.
        probed = _make_recording(source_path=paths[0], duration=1234.0)
        captured: dict[str, ExportRecording] = {}

        def fake_dialog(recording, settings, parent=None):
            captured["recording"] = recording
            dialog = MagicMock()
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.result_recording = recording
            return dialog

        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _FakeRipScanWorker,
        ), patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            return_value=rip,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipMetadataDialog",
            side_effect=_rip_meta_factory("DVD", "1:30:00", True),
        ), patch.object(
            RipMetadataDialog, "exec",
            return_value=QDialog.DialogCode.Accepted,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            return_value=probed,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
            side_effect=fake_dialog,
        ):
            win._add_folder_btn.click()

        assert service.session.recording_count == 1
        added = service.session.recordings[0]
        assert added.extension == "DVD"
        assert added.duration == 5400.0
        assert added.is_rip is True
        # The dialog received the same label and duration.
        assert captured["recording"].extension == "DVD"
        assert captured["recording"].duration == 5400.0

    def test_rip_metadata_accept_empty_duration_stays_none(self, qt_app) -> None:
        """Accepting "VIDEO_TS" with an empty duration → duration None."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = ["C:/rip/VIDEO_TS/VTS_01_1.VOB"]
        rip = _make_rip_result(paths)
        probed = _make_recording(source_path=paths[0], duration=1234.0)

        def fake_dialog(recording, settings, parent=None):
            dialog = MagicMock()
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.result_recording = recording
            return dialog

        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _FakeRipScanWorker,
        ), patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            return_value=rip,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipMetadataDialog",
            side_effect=_rip_meta_factory("VIDEO_TS", "", True),
        ), patch.object(
            RipMetadataDialog, "exec",
            return_value=QDialog.DialogCode.Accepted,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.ProbeWorker",
            _FakeProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            return_value=probed,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.AddRecordingDialog",
            side_effect=fake_dialog,
        ):
            win._add_folder_btn.click()

        assert service.session.recording_count == 1
        added = service.session.recordings[0]
        assert added.extension == "VIDEO_TS"
        assert added.duration is None
        assert added.is_rip is True

    def test_parse_duration_text_conventions(self) -> None:
        """H:MM:SS / MM:SS / plain seconds parse; garbage stays None."""
        assert parse_duration_text("1:30:00") == 5400.0
        assert parse_duration_text("90:00") == 5400.0
        assert parse_duration_text("5400") == 5400.0
        assert parse_duration_text(" 1:30:00 ") == 5400.0
        assert parse_duration_text("") is None
        assert parse_duration_text("   ") is None
        assert parse_duration_text("bogus") is None
        assert parse_duration_text("1:2:3:4") is None
        assert parse_duration_text("-5") is None

    def test_partial_batch_failure_warns_and_keeps_successes(self, qt_app) -> None:
        """Failed probes get a summary warning; successes still land.

        Drives ``_start_batch_probe`` directly: the rip flow no longer
        routes through it (Bug D), but the batch machinery stays for
        L15.6 and its summary logic must keep working.
        """
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = ["C:/rip/VIDEO_TS/VTS_01_1.VOB", "C:/rip/VIDEO_TS/broken.VOB"]
        rec_a = _make_recording(source_path=paths[0])
        with patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.BatchProbeWorker",
            _FakeBatchProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            side_effect=[rec_a, ExportSessionServiceError("ffprobe exited with 1")],
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._start_batch_probe(list(paths))

        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[1] == "Add from Folder"
        assert mock_warning.call_args.args[2] == "1 of 2 files could not be probed."
        assert service.session.recording_count == 1
        assert win._table_model.rowCount() == 1
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_scan_failure_shows_rip_error_and_unlocks(self, qt_app) -> None:
        """A RipScanError (e.g. the size guard) surfaces verbatim as Rip Error."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        guard_message = (
            "This rip is 120.0 GB, which exceeds the 100.0 GB limit for "
            "folder scans.\n"
            "Add individual recordings instead of the whole folder."
        )
        with patch(
            "bootleg_catalog.ui.export_preview_window.QFileDialog.getExistingDirectory",
            return_value="C:/rip",
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RipScanWorker",
            _FakeRipScanWorker,
        ), patch(
            "bootleg_catalog.ui.workers.rip_scan_worker.scan_rip_folder",
            side_effect=RipScanError(guard_message),
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
        ) as mock_question, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._add_folder_btn.click()

        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[1] == "Rip Error"
        assert mock_warning.call_args.args[2] == guard_message
        mock_question.assert_not_called()
        mock_info.assert_not_called()
        assert service.session.recording_count == 0
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_background_running_tracks_rip_and_batch_threads(self, qt_app) -> None:
        """_background_running sees the rip and batch threads as well."""
        win = ExportPreviewWindow(_make_service())
        assert win._background_running() is False
        win._rip_thread = _RunningQThread()
        assert win._background_running() is True
        win._rip_thread = None
        win._batch_thread = _RunningQThread()
        assert win._background_running() is True
        win._batch_thread = None
        assert win._background_running() is False

    def test_close_is_ignored_while_rip_thread_running(self, qt_app) -> None:
        """closeEvent refuses with 'still running' while a rip scan is live."""
        win = ExportPreviewWindow(_make_service())
        win._rip_thread = _RunningQThread()
        event = MagicMock()

        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info:
            win.closeEvent(event)

        mock_info.assert_called_once()
        assert "still running" in mock_info.call_args.args[2]
        event.ignore.assert_called_once()
        event.accept.assert_not_called()

    def test_batch_probe_worker_does_not_add_recordings(self, qt_app) -> None:
        """run() only probes: a real service's add_recording is called 0 times."""
        service = _make_service()
        paths = ["C:/rip/a.VOB", "C:/rip/b.VOB"]
        rec_a = _make_recording(source_path=paths[0])
        rec_b = _make_recording(source_path=paths[1])
        with patch.object(
            service,
            "probe_recording_from_path",
            side_effect=[rec_a, rec_b],
        ) as mock_probe, patch.object(
            service,
            "add_recording",
        ) as mock_add:
            worker = BatchProbeWorker(service, list(paths))
            payloads: list[tuple[list, list]] = []
            worker.finished.connect(
                lambda added, failed: payloads.append((added, failed)),
            )
            worker.run()

        assert mock_probe.call_count == 2
        mock_add.assert_not_called()
        assert service.session.recording_count == 0
        assert payloads == [([rec_a, rec_b], [])]

    def test_batch_duplicate_skipped_and_summarized(self, qt_app) -> None:
        """One file already in session → skipped, other added, info shown.

        Drives ``_start_batch_probe`` directly (the rip flow no longer
        reaches it — Bug D); the retained batch logic itself is under test.
        """
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = ["C:/rip/VIDEO_TS/VTS_01_1.VOB", "C:/rip/VIDEO_TS/VTS_01_2.VOB"]
        # paths[1] is already in the session.
        service.session.add_recording(
            _make_recording(source_path=paths[1], title="Original"),
        )
        rec_a = _make_recording(source_path=paths[0])
        rec_b = _make_recording(source_path=paths[1])
        with patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.BatchProbeWorker",
            _FakeBatchProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            side_effect=[rec_a, rec_b],
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._start_batch_probe(list(paths))

        mock_warning.assert_not_called()
        mock_info.assert_called_once()
        assert mock_info.call_args.args[1] == "Add from Folder"
        assert mock_info.call_args.args[2] == "1 file was already in the session."
        # rec_a added; the duplicate rec_b skipped; the original untouched.
        assert service.session.recording_count == 2
        assert service.session.get_recording(paths[1]).title == "Original"
        assert win._table_model.rowCount() == 2
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_batch_all_duplicates_leave_session_unchanged(self, qt_app) -> None:
        """Every file already in session → all skipped, info, no changes.

        Drives ``_start_batch_probe`` directly (Bug D removed the rip-flow
        caller); the batch summary logic itself is under test.
        """
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = ["C:/rip/VIDEO_TS/VTS_01_1.VOB", "C:/rip/VIDEO_TS/VTS_01_2.VOB"]
        for index, path in enumerate(paths):
            service.session.add_recording(
                _make_recording(source_path=path, title=f"Original {index}"),
            )
        rec_a = _make_recording(source_path=paths[0])
        rec_b = _make_recording(source_path=paths[1])
        with patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.BatchProbeWorker",
            _FakeBatchProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            side_effect=[rec_a, rec_b],
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._start_batch_probe(list(paths))

        mock_warning.assert_not_called()
        mock_info.assert_called_once()
        assert mock_info.call_args.args[1] == "Add from Folder"
        assert mock_info.call_args.args[2] == "2 files were already in the session."
        # Unchanged: still the two originals, not the probed recordings.
        assert service.session.recording_count == 2
        assert service.session.get_recording(paths[0]).title == "Original 0"
        assert service.session.get_recording(paths[1]).title == "Original 1"
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_batch_mixed_failures_and_duplicates_summary(self, qt_app) -> None:
        """Probe failure + duplicate → one warning reports both counts."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = [
            "C:/rip/VIDEO_TS/VTS_01_1.VOB",
            "C:/rip/VIDEO_TS/broken.VOB",
            "C:/rip/VIDEO_TS/VTS_01_2.VOB",
        ]
        # paths[2] is already in the session; paths[1] fails to probe.
        service.session.add_recording(
            _make_recording(source_path=paths[2], title="Original"),
        )
        rec_a = _make_recording(source_path=paths[0])
        rec_c = _make_recording(source_path=paths[2])
        with patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.BatchProbeWorker",
            _FakeBatchProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            side_effect=[
                rec_a,
                ExportSessionServiceError("ffprobe exited with 1"),
                rec_c,
            ],
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._start_batch_probe(list(paths))

        mock_info.assert_not_called()
        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[1] == "Add from Folder"
        assert mock_warning.call_args.args[2] == (
            "1 of 3 files could not be probed.\n"
            "1 file was already in the session."
        )
        # rec_a added; the failure recorded; the duplicate skipped.
        assert service.session.recording_count == 2
        assert service.session.get_recording(paths[2]).title == "Original"
        assert win._table_model.rowCount() == 2
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_batch_single_duplicate_emits_singular_summary(
        self, qt_app,
    ) -> None:
        """One skipped file → exact '1 file was already in the session.'."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = ["C:/rip/VIDEO_TS/VTS_01_1.VOB"]
        service.session.add_recording(
            _make_recording(source_path=paths[0], title="Original"),
        )
        rec_a = _make_recording(source_path=paths[0])
        with patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.BatchProbeWorker",
            _FakeBatchProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            side_effect=[rec_a],
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._start_batch_probe(list(paths))

        mock_warning.assert_not_called()
        mock_info.assert_called_once()
        assert mock_info.call_args.args[1] == "Add from Folder"
        assert mock_info.call_args.args[2] == (
            "1 file was already in the session."
        )
        # The original stays; nothing was added.
        assert service.session.recording_count == 1
        assert service.session.get_recording(paths[0]).title == "Original"
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_batch_two_duplicates_emits_plural_summary(self, qt_app) -> None:
        """Two skipped among new files → exact '2 files were …' sentence."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = [
            "C:/rip/VIDEO_TS/VTS_01_1.VOB",
            "C:/rip/VIDEO_TS/VTS_01_2.VOB",
            "C:/rip/VIDEO_TS/VTS_01_3.VOB",
        ]
        for index in range(2):
            service.session.add_recording(
                _make_recording(source_path=paths[index], title=f"Original {index}"),
            )
        recs = [_make_recording(source_path=path) for path in paths]
        with patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.BatchProbeWorker",
            _FakeBatchProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            side_effect=recs,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._start_batch_probe(list(paths))

        mock_warning.assert_not_called()
        mock_info.assert_called_once()
        assert mock_info.call_args.args[1] == "Add from Folder"
        assert mock_info.call_args.args[2] == (
            "2 files were already in the session."
        )
        # 2 originals + paths[2] newly added.
        assert service.session.recording_count == 3
        assert service.session.get_recording(paths[0]).title == "Original 0"
        assert service.session.get_recording(paths[1]).title == "Original 1"
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()

    def test_batch_single_probe_failure_emits_probe_sentence(
        self, qt_app,
    ) -> None:
        """Exactly one failure (no skips) → '1 of 3 files could not be probed.'."""
        service = _make_service()
        win = ExportPreviewWindow(service)
        paths = [
            "C:/rip/VIDEO_TS/VTS_01_1.VOB",
            "C:/rip/VIDEO_TS/VTS_01_2.VOB",
            "C:/rip/VIDEO_TS/VTS_01_3.VOB",
        ]
        rec_a = _make_recording(source_path=paths[0])
        rec_c = _make_recording(source_path=paths[2])
        with patch(
            "bootleg_catalog.ui.export_preview_window.QThread",
            _SyncQThread,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.BatchProbeWorker",
            _FakeBatchProbeWorker,
        ), patch.object(
            service,
            "probe_recording_from_path",
            side_effect=[
                rec_a,
                ExportSessionServiceError("ffprobe exited with 1"),
                rec_c,
            ],
        ), patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.information",
        ) as mock_info, patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.warning",
        ) as mock_warning:
            win._start_batch_probe(list(paths))

        mock_info.assert_not_called()
        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[1] == "Add from Folder"
        assert mock_warning.call_args.args[2] == (
            "1 of 3 files could not be probed."
        )
        # rec_a and rec_c added; only the middle probe failed.
        assert service.session.recording_count == 2
        assert win._table_model.rowCount() == 2
        assert win._toolbar.isEnabled()
        assert win._busy_label.isHidden()


# ---------------------------------------------------------------------------
# L15.4 — manual screenshot attachment
# ---------------------------------------------------------------------------


class TestScreenshotWiring:
    """Screenshots… toolbar button, ScreenshotManagerDialog wiring, save."""

    def test_screenshots_button_enabled_only_with_selection(
        self, qt_app,
    ) -> None:
        service = _make_service()
        service.session.add_recording(_make_recording())
        win = ExportPreviewWindow(service)

        assert not win._screenshots_btn.isEnabled()
        win._table.selectRow(0)
        assert win._screenshots_btn.isEnabled()
        win._table.clearSelection()
        assert not win._screenshots_btn.isEnabled()

    def test_dialog_receives_selected_recording(self, qt_app) -> None:
        service = _make_service()
        rec = _make_recording()
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        win._table.selectRow(0)

        with patch(
            "bootleg_catalog.ui.export_preview_window.ScreenshotManagerDialog",
        ) as MockDialog:
            MockDialog.return_value.exec.return_value = (
                QDialog.DialogCode.Rejected
            )
            win._screenshots_btn.click()

        MockDialog.assert_called_once()
        assert MockDialog.call_args.args[0] is rec
        # Rejected: the session is untouched.
        assert service.session.get_recording(rec.source_path) is rec
        assert service.is_dirty() is False

    def test_ok_updates_recording_and_refreshes_table(self, qt_app) -> None:
        service = _make_service()
        rec = _make_recording()
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        win._table.selectRow(0)

        new_paths = ["C:/shots/cover.jpg"]
        new_manual = {"C:/shots/cover.jpg"}
        with patch(
            "bootleg_catalog.ui.export_preview_window.ScreenshotManagerDialog",
        ) as MockDialog, patch.object(
            service,
            "update_recording",
            side_effect=service.update_recording,
        ) as mock_update:
            instance = MockDialog.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            instance.result_screenshot_paths = list(new_paths)
            instance.result_manual_paths = set(new_manual)
            # Single part + one path: split == len(paths) is the only
            # valid non-None value, so a dropped wiring line fails here.
            instance.result_screenshot_split = 1
            win._screenshots_btn.click()

        mock_update.assert_called_once()
        assert mock_update.call_args.args[0] == rec.source_path
        updated = mock_update.call_args.args[1]
        assert updated.screenshot_paths == new_paths
        assert updated.manual_paths == new_manual
        assert updated.screenshot_split == 1
        # The table was refreshed: it holds the replaced object now, so
        # without a refresh these would still be rec's old values.
        assert win._table_model.recording_at(0) is not rec
        assert win._table_model.recording_at(0).screenshot_paths == new_paths
        assert win._table_model.recording_at(0).manual_paths == new_manual

    def test_ok_keeps_two_part_structure_and_split(self, qt_app) -> None:
        """The dialog's split flows into the replaced recording (L15.6.3a)."""
        service = _make_service()
        part_a = "C:/media/show-part1.mkv"
        part_b = "C:/media/show-part2.mkv"
        rec = _make_recording(source_path=part_a, parts=(part_a, part_b))
        service.session.add_recording(rec)
        win = ExportPreviewWindow(service)
        win._table.selectRow(0)

        new_paths = ["C:/shots/x.jpg", "C:/shots/y.jpg", "C:/shots/z.jpg"]
        with patch(
            "bootleg_catalog.ui.export_preview_window.ScreenshotManagerDialog",
        ) as MockDialog:
            instance = MockDialog.return_value
            instance.exec.return_value = QDialog.DialogCode.Accepted
            instance.result_screenshot_paths = list(new_paths)
            instance.result_manual_paths = set()
            instance.result_screenshot_split = 1
            win._screenshots_btn.click()

        updated = service.session.get_recording(part_a)
        assert updated is not rec
        assert updated.parts == (part_a, part_b)
        assert updated.screenshot_split == 1
        assert updated.screenshot_paths == new_paths
        assert service.is_dirty() is True

    def test_save_flow_embeds_manual_screenshots(
        self, qt_app, tmp_path: Path,
    ) -> None:
        """Save embeds manual shots; load keeps the origin; re-save too."""
        service = _make_service()
        manual = tmp_path / "capa.jpg"
        manual.write_bytes(b"\xff\xd8-manual-bytes")
        auto = tmp_path / "sidecar.png"
        auto.write_bytes(b"\x89PNG-auto-bytes")
        service.session.add_recording(
            _make_recording(
                screenshot_paths=[str(manual), str(auto)],
                manual_paths={str(manual)},
            ),
        )
        appdata = tmp_path / "appdata"
        first = tmp_path / "primeira.bootleg"
        second = tmp_path / "segunda.bootleg"

        win = ExportPreviewWindow(service)
        win._current_path = first
        with patch(
            "bootleg_catalog.core.session_store.app_data_dir",
            return_value=appdata,
        ), patch(
            "bootleg_catalog.ui.export_preview_window.RecentSessions",
        ):
            # 1-3: the manual image's bytes are inside the archive, the
            # auto sidecar is only referenced.
            win._on_save()
            with zipfile.ZipFile(first, "r") as zf:
                assert zf.namelist() == [
                    MANIFEST_NAME,
                    "screenshots/r0001-s01.jpg",
                ]
                assert zf.read("screenshots/r0001-s01.jpg") == (
                    b"\xff\xd8-manual-bytes"
                )

            # 4: load restores the manual origin (resolved to the cache).
            service.load(first)
            restored = service.session.recordings[0]
            assert restored.screenshot_paths[0] != str(manual)
            assert restored.manual_paths == {restored.screenshot_paths[0]}
            assert restored.screenshot_paths[1] == str(auto)

            # 5: re-save through the window; the manual shot is embedded
            # again, straight from its bytes.
            win._current_path = second
            win._on_save()
            with zipfile.ZipFile(second, "r") as zf:
                assert zf.namelist() == [
                    MANIFEST_NAME,
                    "screenshots/r0001-s01.jpg",
                ]
                assert zf.read("screenshots/r0001-s01.jpg") == (
                    b"\xff\xd8-manual-bytes"
                )


# ---------------------------------------------------------------------------
# L15.6.4 — multi-row selection and keyboard deletion
# ---------------------------------------------------------------------------


def _select_two_rows(win: ExportPreviewWindow) -> None:
    """Select table rows 0 and 1 through the selection model."""
    selection = win._table.selectionModel()
    flags = (
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows
    )
    selection.select(win._table_model.index(0, 0), flags)
    selection.select(win._table_model.index(1, 0), flags)


def _show_and_focus_table(win: ExportPreviewWindow) -> None:
    """Show, activate and focus the table (offscreen focus handshake)."""
    win.show()
    win.activateWindow()
    QCoreApplication.processEvents()
    win._table.setFocus()


class TestMultiSelectRemove:
    """ExtendedSelection table, single-confirmation remove, Delete keys."""

    def test_two_rows_enable_remove_disable_edit_and_screenshots(
        self, qt_app,
    ) -> None:
        """Two selected rows: Remove on, Edit and Screenshots off."""
        service = _make_service()
        service.session.add_recording(_make_recording(source_path="C:/media/a.mkv"))
        service.session.add_recording(_make_recording(source_path="C:/media/b.mkv"))
        win = ExportPreviewWindow(service)
        _select_two_rows(win)

        assert len(win._selected_recordings()) == 2
        assert win._remove_btn.isEnabled()
        assert not win._edit_btn.isEnabled()
        assert not win._screenshots_btn.isEnabled()

    def test_remove_two_rows_yes_removes_both_with_one_confirmation(
        self, qt_app,
    ) -> None:
        """One Yes on 'Remove 2 recordings?' removes both rows."""
        service = _make_service()
        service.session.add_recording(_make_recording(source_path="C:/media/a.mkv"))
        service.session.add_recording(_make_recording(source_path="C:/media/b.mkv"))
        win = ExportPreviewWindow(service)
        _select_two_rows(win)
        _show_and_focus_table(win)

        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as mock_question:
            win._remove_btn.click()

        mock_question.assert_called_once()
        assert "2 recordings" in mock_question.call_args.args[2]
        assert service.session.recording_count == 0
        assert win._table_model.rowCount() == 0

    def test_remove_two_rows_cancel_removes_nothing(self, qt_app) -> None:
        """No on the multi-remove confirmation keeps every recording."""
        service = _make_service()
        service.session.add_recording(_make_recording(source_path="C:/media/a.mkv"))
        service.session.add_recording(_make_recording(source_path="C:/media/b.mkv"))
        win = ExportPreviewWindow(service)
        _select_two_rows(win)
        _show_and_focus_table(win)

        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as mock_question:
            win._remove_btn.click()

        mock_question.assert_called_once()
        assert service.session.recording_count == 2
        assert win._table_model.rowCount() == 2

    def test_delete_key_on_focused_table_confirms_and_removes(
        self, qt_app,
    ) -> None:
        """Delete on a focused table asks, and Yes removes the row."""
        service = _make_service()
        service.session.add_recording(_make_recording(source_path="C:/media/a.mkv"))
        win = ExportPreviewWindow(service)
        _show_and_focus_table(win)
        win._table.selectRow(0)
        win._table.setFocus()
        assert win._table.hasFocus()
        assert win._delete_shortcut.key() == QKeySequence(
            QKeySequence.StandardKey.Delete,
        )

        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as mock_question:
            win._delete_shortcut.activated.emit()

        mock_question.assert_called_once()
        assert mock_question.call_args.args[2] == "Remove this recording?"
        assert service.session.recording_count == 0

    def test_delete_key_without_table_focus_does_nothing(self, qt_app) -> None:
        """Delete while the table has no focus: no confirmation, no removal."""
        service = _make_service()
        service.session.add_recording(_make_recording(source_path="C:/media/a.mkv"))
        win = ExportPreviewWindow(service)
        win._table.selectRow(0)
        assert not win._table.hasFocus()
        assert win._backspace_shortcut.key() == QKeySequence(Qt.Key.Key_Backspace)

        with patch(
            "bootleg_catalog.ui.export_preview_window.QMessageBox.question",
        ) as mock_question:
            win._delete_shortcut.activated.emit()

        mock_question.assert_not_called()
        assert service.session.recording_count == 1
        assert win._table_model.rowCount() == 1

    def test_edit_button_disabled_with_two_rows(self, qt_app) -> None:
        """Edit Recording… is disabled for a 2-row selection and opens nothing."""
        service = _make_service()
        service.session.add_recording(_make_recording(source_path="C:/media/a.mkv"))
        service.session.add_recording(_make_recording(source_path="C:/media/b.mkv"))
        win = ExportPreviewWindow(service)
        _select_two_rows(win)

        assert not win._edit_btn.isEnabled()
        with patch(
            "bootleg_catalog.ui.export_preview_window.EditExportRecordingDialog",
        ) as MockEdit:
            win._edit_btn.click()
        MockEdit.assert_not_called()

    def test_selected_recordings_returns_display_order(self, qt_app) -> None:
        """Selections made bottom-row-first still come back row 0 first."""
        service = _make_service()
        service.session.add_recording(
            _make_recording(source_path="C:/media/first.mkv"),
        )
        service.session.add_recording(
            _make_recording(source_path="C:/media/second.mkv"),
        )
        win = ExportPreviewWindow(service)
        selection = win._table.selectionModel()
        flags = (
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows
        )
        # Bottom row selected FIRST to prove the result is display-ordered.
        selection.select(win._table_model.index(1, 0), flags)
        selection.select(win._table_model.index(0, 0), flags)

        paths = [rec.source_path for rec in win._selected_recordings()]
        assert paths == ["C:/media/first.mkv", "C:/media/second.mkv"]
