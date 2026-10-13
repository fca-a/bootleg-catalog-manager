"""Tests for search and filter functionality."""

from __future__ import annotations

import os

import pytest

from bootleg_catalog.app.application import BootlegCatalogApplication
from bootleg_catalog.core.filters import RecordingFilter
from bootleg_catalog.ui.widgets.search_bar import SearchBar


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def qt_app() -> BootlegCatalogApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = BootlegCatalogApplication.instance()
    if app is None:
        app = BootlegCatalogApplication([])
    return app


# ---------------------------------------------------------------------------
# RecordingFilter dataclass
# ---------------------------------------------------------------------------

class TestRecordingFilter:
    def test_default_values(self) -> None:
        f = RecordingFilter()
        assert f.query == ""
        assert f.year is None
        assert f.source is None
        assert f.quality is None
        assert f.master is None
        assert f.missing is None
        assert f.folder_id is None

    def test_custom_values(self) -> None:
        f = RecordingFilter(query="oakland", year=1977, source="Audience")
        assert f.query == "oakland"
        assert f.year == 1977
        assert f.source == "Audience"


# ---------------------------------------------------------------------------
# SearchBar widget
# ---------------------------------------------------------------------------

class TestSearchBar:
    def test_default_filter(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        f = bar.current_filter()
        assert f.query == ""
        assert f.year is None
        assert f.source is None
        assert f.quality is None
        assert f.master is None
        assert f.missing is None

    def test_text_query(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        bar._search_edit.setText("oakland")
        f = bar.current_filter()
        assert f.query == "oakland"

    def test_year_filter(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        bar._year_edit.setText("1977")
        f = bar.current_filter()
        assert f.year == 1977

    def test_year_filter_invalid(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        bar._year_edit.setText("abc")
        f = bar.current_filter()
        assert f.year is None

    def test_source_filter(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        bar._source_combo.addItem("Audience")
        bar._source_combo.setCurrentText("Audience")
        f = bar.current_filter()
        assert f.source == "Audience"

    def test_quality_filter(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        bar._quality_combo.addItem("Excellent")
        bar._quality_combo.setCurrentText("Excellent")
        f = bar.current_filter()
        assert f.quality == "Excellent"

    def test_master_filter(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        bar._master_combo.addItem("Yes")
        bar._master_combo.setCurrentText("Yes")
        f = bar.current_filter()
        assert f.master == "Yes"

    def test_missing_filter(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        bar._missing_combo.setCurrentText("Yes")
        f = bar.current_filter()
        assert f.missing is True

        bar._missing_combo.setCurrentText("No")
        f = bar.current_filter()
        assert f.missing is False

    def test_clear_all(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        bar._search_edit.setText("test")
        bar._year_edit.setText("1977")
        bar._source_combo.addItem("Audience")
        bar._source_combo.setCurrentText("Audience")

        bar.clear_all()

        f = bar.current_filter()
        assert f.query == ""
        assert f.year is None
        assert f.source is None

    def test_filter_changed_signal(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        received = []
        bar.filter_changed.connect(lambda f: received.append(f))
        bar._search_edit.setText("test")
        assert len(received) == 1
        assert received[0].query == "test"

    def test_set_filter_values(self, qt_app: BootlegCatalogApplication) -> None:
        bar = SearchBar()
        bar.set_filter_values("source", ["Audience", "Soundboard"])
        items = [bar._source_combo.itemText(i) for i in range(bar._source_combo.count())]
        assert "" in items
        assert "Audience" in items
        assert "Soundboard" in items
