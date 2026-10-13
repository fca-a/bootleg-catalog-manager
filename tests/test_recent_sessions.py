"""Tests for the recent sessions store (L15.3a)."""

from __future__ import annotations

import inspect
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.core import recent_sessions
from bootleg_catalog.core.recent_sessions import (
    RecentEntry,
    RecentSessions,
    RecentSessionsError,
)

_TIMESTAMP = "2026-09-23T20:15:00.000000+00:00"


def _recents_file(tmp_path: Path) -> Path:
    return tmp_path / "recent_sessions.json"


def _write_payload(path: Path, entries: list[object]) -> None:
    path.write_text(
        json.dumps({"format_version": 1, "entries": entries}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Fresh instance / read behaviour
# ---------------------------------------------------------------------------


def test_fresh_instance_is_empty_and_read_does_not_create_file(
    tmp_path: Path,
) -> None:
    path = _recents_file(tmp_path)

    recents = RecentSessions(path)

    assert recents.list() == []
    assert not path.exists()


def test_add_then_list_returns_iso8601_utc_entry(tmp_path: Path) -> None:
    recents = RecentSessions(_recents_file(tmp_path))
    target = tmp_path / "minha-lista.bootleg"

    recents.add("Minha Lista", target)

    entries = recents.list()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.display_name == "Minha Lista"
    assert entry.path == str(target)  # absolute
    parsed = datetime.fromisoformat(entry.last_opened)
    assert parsed.utcoffset() == timedelta(0)  # UTC


def test_add_same_path_replaces_name_and_updates_timestamp(
    tmp_path: Path,
) -> None:
    recents = RecentSessions(_recents_file(tmp_path))
    target = tmp_path / "lista.bootleg"

    recents.add("Nome Antigo", target)
    first_timestamp = recents.list()[0].last_opened
    time.sleep(0.01)
    recents.add("Nome Novo", target)

    entries = recents.list()
    assert len(entries) == 1
    assert entries[0].display_name == "Nome Novo"
    assert entries[0].last_opened > first_timestamp


def test_add_beyond_max_entries_evicts_oldest(tmp_path: Path) -> None:
    recents = RecentSessions(_recents_file(tmp_path))
    targets: list[str] = []
    for index in range(12):
        target = tmp_path / f"lista-{index:02d}.bootleg"
        recents.add(f"Lista {index}", str(target))
        targets.append(str(target))
        time.sleep(0.01)

    entries = recents.list()

    assert len(entries) == 10
    # The two oldest are evicted; survivors are newest-first.
    assert [e.path for e in entries] == list(reversed(targets[2:]))


def test_list_returns_newest_first(tmp_path: Path) -> None:
    recents = RecentSessions(_recents_file(tmp_path))
    for name in ("Primeira", "Segunda", "Terceira"):
        recents.add(name, tmp_path / f"{name}.bootleg")
        time.sleep(0.01)

    assert [e.display_name for e in recents.list()] == [
        "Terceira",
        "Segunda",
        "Primeira",
    ]


# ---------------------------------------------------------------------------
# remove / clear
# ---------------------------------------------------------------------------


def test_remove_existing_entry_persists_across_instances(
    tmp_path: Path,
) -> None:
    path = _recents_file(tmp_path)
    keep = tmp_path / "keep.bootleg"
    drop = tmp_path / "drop.bootleg"
    recents = RecentSessions(path)
    recents.add("Manter", keep)
    recents.add("Sair", drop)

    recents.remove(drop)

    assert [e.path for e in recents.list()] == [str(keep)]
    reopened = RecentSessions(path)
    assert [e.path for e in reopened.list()] == [str(keep)]


def test_remove_missing_path_is_noop(tmp_path: Path) -> None:
    path = _recents_file(tmp_path)
    recents = RecentSessions(path)
    recents.add("Unica", tmp_path / "unica.bootleg")
    before = path.read_text(encoding="utf-8")

    recents.remove(tmp_path / "nunca-existe.bootleg")

    assert [e.display_name for e in recents.list()] == ["Unica"]
    assert path.read_text(encoding="utf-8") == before  # not even rewritten


def test_clear_empties_list_and_rewrites_file(tmp_path: Path) -> None:
    path = _recents_file(tmp_path)
    recents = RecentSessions(path)
    recents.add("Um", tmp_path / "a.bootleg")

    recents.clear()

    assert recents.list() == []
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format_version"] == 1
    assert data["entries"] == []


# ---------------------------------------------------------------------------
# Persistence / storage format
# ---------------------------------------------------------------------------


def test_round_trip_across_instances(tmp_path: Path) -> None:
    path = _recents_file(tmp_path)
    recents = RecentSessions(path)
    recents.add("Uma", tmp_path / "1.bootleg")
    time.sleep(0.01)
    recents.add("Duas", tmp_path / "2.bootleg")

    reopened = RecentSessions(path)

    assert reopened.list() == recents.list()


def test_corrupt_json_returns_empty_and_keeps_file(tmp_path: Path) -> None:
    path = _recents_file(tmp_path)
    path.write_text("{not json !!", encoding="utf-8")

    recents = RecentSessions(path)

    assert recents.list() == []
    # Not deleted, not rewritten — recoverable manually.
    assert path.read_text(encoding="utf-8") == "{not json !!"


def test_non_dict_top_level_returns_empty(tmp_path: Path) -> None:
    path = _recents_file(tmp_path)
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert RecentSessions(path).list() == []


def test_entries_not_a_list_returns_empty(tmp_path: Path) -> None:
    path = _recents_file(tmp_path)
    path.write_text(
        json.dumps({"format_version": 1, "entries": "oops"}),
        encoding="utf-8",
    )

    assert RecentSessions(path).list() == []


def test_entry_with_missing_fields_is_skipped_valid_retained(
    tmp_path: Path,
) -> None:
    path = _recents_file(tmp_path)
    valid = {
        "display_name": "Valida",
        "path": str(tmp_path / "ok.bootleg"),
        "last_opened": _TIMESTAMP,
    }
    no_path = {"display_name": "Sem Path", "last_opened": _TIMESTAMP}
    _write_payload(path, [valid, "not-a-dict", no_path])

    assert RecentSessions(path).list() == [
        RecentEntry("Valida", str(tmp_path / "ok.bootleg"), _TIMESTAMP)
    ]


def test_entry_with_non_absolute_path_is_skipped(tmp_path: Path) -> None:
    # Documented choice: skip (we always write absolute paths).
    path = _recents_file(tmp_path)
    relative = {
        "display_name": "Relativa",
        "path": "relative/lista.bootleg",
        "last_opened": _TIMESTAMP,
    }
    _write_payload(path, [relative])

    assert RecentSessions(path).list() == []


# ---------------------------------------------------------------------------
# Atomic writes / default location / constants
# ---------------------------------------------------------------------------


def test_atomic_save_failure_raises_and_cleans_temp(tmp_path: Path) -> None:
    path = _recents_file(tmp_path)
    recents = RecentSessions(path)

    with patch.object(Path, "replace", side_effect=OSError("disco cheio")):
        with pytest.raises(RecentSessionsError) as excinfo:
            recents.add("Falha", tmp_path / "falha.bootleg")

    assert isinstance(excinfo.value.__cause__, OSError)
    assert not path.exists()  # no partial file at destination
    assert list(tmp_path.iterdir()) == []  # temp cleaned up


def test_default_location_uses_app_data_dir(tmp_path: Path) -> None:
    fake_app_data = tmp_path / "appdata"

    with patch(
        "bootleg_catalog.core.recent_sessions.app_data_dir",
        return_value=fake_app_data,
    ):
        recents = RecentSessions()
        assert recents.list() == []
        assert not (fake_app_data / "recent_sessions.json").exists()
        recents.add("Padrao", tmp_path / "padrao.bootleg")

    assert (fake_app_data / "recent_sessions.json").is_file()


def test_max_entries_is_ten() -> None:
    assert RecentSessions.MAX_ENTRIES == 10


def test_recent_sessions_module_is_pure() -> None:
    source = inspect.getsource(recent_sessions)
    for token in (
        "PySide6",
        "Catalog",
        "CatalogRepository",
        "sqlite3",
        "QtCore",
        "QtWidgets",
        "bootleg_catalog.ui",
        "bootleg_catalog.core.catalog",
        "bootleg_catalog.database",
    ):
        assert token not in source, token
