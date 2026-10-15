"""Tests for the .bootleg session store (zip format v2) and service persistence."""

from __future__ import annotations

import inspect
import json
import os
import zipfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.core import session_store
from bootleg_catalog.core.export_session_service import ExportSessionService
from bootleg_catalog.core.session_store import (
    CACHE_EVICTION_AGE_DAYS,
    FORMAT_VERSION,
    MANIFEST_NAME,
    SessionData,
    SessionStoreError,
    evict_cache,
    is_session_file,
    load_session,
    save_session,
)
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FieldType,
    LayoutElement,
    LayoutLine,
    LayoutModel,
    PageConfig,
    ScreenshotConfig,
)
from bootleg_catalog.utils.ffmpeg import ProbeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SOURCE_PATH = "D:/media/show.mkv"
_CREATED_AT = "2026-09-23T14:00:00+00:00"


def _layout(name: str = "Lista Teste") -> LayoutModel:
    """A non-trivial layout exercising every serialisable corner."""
    return LayoutModel(
        name=name,
        show_header=[
            LayoutLine(elements=[
                LayoutElement(
                    kind="field",
                    content="Show",
                    field_type=FieldType.ARCHIVAL,
                    style=ElementStyle(font_size=14, bold=True, color="#FF0000"),
                ),
                LayoutElement(kind="text", content=" - "),
                LayoutElement(
                    kind="field",
                    content="Duration",
                    field_type=FieldType.TECHNICAL,
                ),
            ]),
        ],
        recording_body=[
            LayoutLine(elements=[
                LayoutElement(
                    kind="field",
                    content="Venue",
                    field_type=FieldType.ARCHIVAL,
                ),
            ]),
        ],
        separator="=",
        page_config=PageConfig(
            width_mm=297.0,
            height_mm=210.0,
            margin_mm=12.0,
            body_font="Courier",
            header_font="Arial",
        ),
        screenshot_config=ScreenshotConfig(
            enabled=True,
            max_width_px=320,
            max_height_px=240,
            layout="grid",
        ),
    )


def _full_recording(screenshots: list[str] | None = None) -> ExportRecording:
    """An ExportRecording with every technical and archival field populated."""
    return ExportRecording(
        source_path=_SOURCE_PATH,
        extension=".mkv",
        file_size=4_200_000_000,
        duration=7342.5,
        container_format="matroska,webm",
        video_codec="h264",
        video_width=1920,
        video_height=1080,
        frame_rate=29.97,
        pixel_format="yuv420p",
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate=320000,
        title="Night Show",
        year=2024,
        date="2024-05-01",
        venue="Arena Metropolitan",
        location="São Paulo",
        country="Brazil",
        tour="Tour 2024",
        show="SP 2024",
        source="Audience",
        type="Audience",
        generation="1st generation",
        quality="Excellent",
        audio_source="Audience",
        video_source="Pro-shot",
        master="No",
        notes="Clean transfer",
        description="Excellent audience recording",
        screenshot_paths=list(screenshots or []),
        probe_error=None,
    )


def _session_data(
    display_name: str = "Lista de Gravações do Fulano",
    *,
    screenshots: list[str] | None = None,
    created_at: str = _CREATED_AT,
) -> SessionData:
    return SessionData(
        display_name=display_name,
        layout=_layout(),
        recordings=[_full_recording(screenshots=screenshots)],
        created_at=created_at,
    )


def _probe_value() -> ProbeResult:
    return ProbeResult(
        duration=7200.0,
        container_format="matroska,webm",
        video_codec="h264",
        video_width=1920,
        video_height=1080,
        frame_rate=29.97,
        pixel_format="yuv420p",
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate=192000,
    )


# ---------------------------------------------------------------------------
# Writing / sniffing
# ---------------------------------------------------------------------------

def test_save_creates_file_with_pk_magic(tmp_path: Path) -> None:
    path = save_session(_session_data(), tmp_path / "session.bootleg")
    assert path.read_bytes()[:2] == b"PK"


def test_is_session_file_true_for_saved_file(tmp_path: Path) -> None:
    path = save_session(_session_data(), tmp_path / "session.bootleg")
    assert is_session_file(path) is True


def test_is_session_file_false_for_sqlite_file(tmp_path: Path) -> None:
    path = tmp_path / "old.bootleg"
    path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    assert is_session_file(path) is False


def test_is_session_file_false_for_random_bytes(tmp_path: Path) -> None:
    path = tmp_path / "junk.bootleg"
    path.write_bytes(bytes([0x00, 0x01, 0x02, 0x03]) * 8)
    assert is_session_file(path) is False


def test_load_sqlite_file_raises_legacy_message(tmp_path: Path) -> None:
    path = tmp_path / "old.bootleg"
    path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    with pytest.raises(
        SessionStoreError,
        match="This is a legacy catalog file, not an export session.",
    ):
        load_session(path)


def test_load_random_bytes_raises_invalid_message(tmp_path: Path) -> None:
    path = tmp_path / "junk.bootleg"
    path.write_bytes(bytes([0x00, 0x01, 0x02, 0x03]) * 8)
    with pytest.raises(SessionStoreError, match="Not a valid .bootleg session file."):
        load_session(path)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_round_trip_preserves_display_name_layout_and_recordings(tmp_path: Path) -> None:
    data = _session_data()
    path = save_session(data, tmp_path / "session.bootleg")

    loaded = load_session(path)

    assert loaded.display_name == "Lista de Gravações do Fulano"
    assert loaded.layout == data.layout
    assert loaded.recordings == data.recordings

    # Spot-check individual fields concretely.
    rec = loaded.recordings[0]
    assert rec.year == 2024
    assert rec.location == "São Paulo"
    assert rec.file_size == 4_200_000_000
    assert rec.duration == 7342.5
    assert rec.probe_error is None
    assert rec.screenshot_paths == []


def test_round_trip_preserves_format_version_and_created_at(tmp_path: Path) -> None:
    data = _session_data()
    path = save_session(data, tmp_path / "session.bootleg")

    loaded = load_session(path)

    assert loaded.format_version == FORMAT_VERSION == 4
    assert loaded.created_at == _CREATED_AT


def test_manifest_layout_is_exact_to_dict_output(tmp_path: Path) -> None:
    data = _session_data()
    path = save_session(data, tmp_path / "session.bootleg")

    with zipfile.ZipFile(path, "r") as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME))

    assert manifest["layout"] == data.layout.to_dict()
    assert set(manifest) == {
        "format_version",
        "display_name",
        "created_at",
        "modified_at",
        "layout",
        "recordings",
        "screenshots",
    }


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------

def test_referenced_screenshots_round_trip_unchanged(tmp_path: Path) -> None:
    shot = tmp_path / "sidecar.jpg"
    shot.write_bytes(b"\xff\xd8")
    other = tmp_path / "other.png"
    other.write_bytes(b"\x89PNG")

    data = _session_data(screenshots=[str(shot), str(other)])
    path = save_session(data, tmp_path / "session.bootleg")

    # Referenced screenshots add no zip entries.
    with zipfile.ZipFile(path, "r") as zf:
        assert zf.namelist() == [MANIFEST_NAME]
        manifest = json.loads(zf.read(MANIFEST_NAME))
    assert manifest["screenshots"] == [
        {
            "recording": _SOURCE_PATH,
            "mode": "referenced",
            "path": str(shot),
            "origin": "auto",
        },
        {
            "recording": _SOURCE_PATH,
            "mode": "referenced",
            "path": str(other),
            "origin": "auto",
        },
    ]

    loaded = load_session(path)
    assert loaded.recordings[0].screenshot_paths == [str(shot), str(other)]


def test_missing_referenced_screenshot_still_listed(tmp_path: Path) -> None:
    missing = tmp_path / "gone.jpg"  # never created
    data = _session_data(screenshots=[str(missing)])
    path = save_session(data, tmp_path / "session.bootleg")

    loaded = load_session(path)

    assert loaded.recordings[0].screenshot_paths == [str(missing)]


def test_embedded_screenshots_extracted_to_cache(tmp_path: Path) -> None:
    shot = tmp_path / "manual.jpg"
    shot.write_bytes(b"\xff\xd8\xff\xe0 embedded-jpeg-bytes")
    data = _session_data(screenshots=[str(shot)])
    appdata = tmp_path / "appdata"

    with patch(
        "bootleg_catalog.core.session_store.app_data_dir",
        return_value=appdata,
    ):
        path = save_session(
            data, tmp_path / "session.bootleg", embed_paths={str(shot)}
        )
        with zipfile.ZipFile(path, "r") as zf:
            assert zf.namelist() == [MANIFEST_NAME, "screenshots/r0001-s01.jpg"]
        loaded = load_session(path)

    resolved = loaded.recordings[0].screenshot_paths
    assert len(resolved) == 1
    assert resolved[0] != str(shot)  # resolved to the cache, not the source

    extracted = Path(resolved[0])
    assert extracted.is_file()
    assert extracted.read_bytes() == shot.read_bytes()
    assert appdata in extracted.parents
    assert "cache" in extracted.relative_to(appdata).parts


def test_embedded_screenshots_not_reextracted_on_second_load(tmp_path: Path) -> None:
    shot = tmp_path / "manual.jpg"
    shot.write_bytes(b"\xff\xd8 second-copy")
    data = _session_data(screenshots=[str(shot)])
    appdata = tmp_path / "appdata"

    with patch(
        "bootleg_catalog.core.session_store.app_data_dir",
        return_value=appdata,
    ):
        path = save_session(
            data, tmp_path / "session.bootleg", embed_paths={str(shot)}
        )
        first = load_session(path)

        with patch("zipfile.ZipFile.extract") as extract_mock:
            second = load_session(path)
        extract_mock.assert_not_called()

    assert second.recordings[0].screenshot_paths == first.recordings[0].screenshot_paths
    assert Path(second.recordings[0].screenshot_paths[0]).is_file()


def test_embedded_cache_refreshed_when_bytes_change(tmp_path: Path) -> None:
    shot_a = tmp_path / "shot-a.jpg"
    shot_a.write_bytes(b"AAAA-bytes-from-first-save")
    shot_b = tmp_path / "shot-b.jpg"
    shot_b.write_bytes(b"BBBB-different-bytes-second-save")

    data = _session_data(screenshots=[str(shot_a)])
    appdata = tmp_path / "appdata"

    with patch(
        "bootleg_catalog.core.session_store.app_data_dir",
        return_value=appdata,
    ):
        path = save_session(
            data, tmp_path / "session.bootleg", embed_paths={str(shot_a)}
        )
        first = load_session(path)
        first_cached = Path(first.recordings[0].screenshot_paths[0])
        assert first_cached.read_bytes() == b"AAAA-bytes-from-first-save"

        # Same recording, same entry slot (r0001-s01.jpg), new source bytes.
        data.recordings[0].screenshot_paths = [str(shot_b)]
        save_session(data, path, embed_paths={str(shot_b)})
        second = load_session(path)

    second_cached = Path(second.recordings[0].screenshot_paths[0])

    assert second_cached == first_cached  # same cache slot...
    assert second_cached.read_bytes() == b"BBBB-different-bytes-second-save"  # ...refreshed


# ---------------------------------------------------------------------------
# Embed-all-screenshots (save-time choice; no format change)
# ---------------------------------------------------------------------------

def test_embed_all_screenshots_embeds_every_path(tmp_path: Path) -> None:
    """embed_paths = every screenshot embeds them all, not just manual.

    A spy on the zip writer confirms each path is written into the
    archive (auto sidecars included), and the manifest marks them all
    embedded.
    """
    shots = [tmp_path / f"shot{i}.jpg" for i in range(5)]
    for i, s in enumerate(shots):
        s.write_bytes(b"\xff\xd8 embedded-%d" % i)
    rec = _full_recording(screenshots=[str(s) for s in shots])
    rec.manual_paths = {str(shots[0])}  # only one is manual
    data = SessionData(display_name="All", layout=_layout(), recordings=[rec])

    real_write = zipfile.ZipFile.write
    written: list[str] = []

    def spy_write(self, filename, arcname=None, compress_type=None):
        written.append(arcname)
        return real_write(
            self, filename, arcname=arcname, compress_type=compress_type
        )

    with patch("zipfile.ZipFile.write", spy_write):
        path = save_session(
            data, tmp_path / "session.bootleg",
            embed_paths={str(s) for s in shots},
        )

    assert len(written) == len(shots)  # every screenshot written
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
    embedded = [n for n in names if n.startswith("screenshots/")]
    assert len(embedded) == len(shots)


def test_embed_all_reload_resolves_to_cache_dir(tmp_path: Path) -> None:
    """40+ embedded screenshots all resolve into the cache and exist.

    Verifies the load side at volume: every resolved path lands under
    the per-session cache directory, exists, and matches the source
    bytes.
    """
    shots = [tmp_path / f"auto{i:02d}.jpg" for i in range(42)]
    for i, s in enumerate(shots):
        s.write_bytes(b"\xff\xd8 auto-%d" % i)
    rec = _full_recording(screenshots=[str(s) for s in shots])
    data = SessionData(display_name="Many", layout=_layout(), recordings=[rec])
    appdata = tmp_path / "appdata"

    with patch(
        "bootleg_catalog.core.session_store.app_data_dir",
        return_value=appdata,
    ):
        path = save_session(
            data, tmp_path / "session.bootleg",
            embed_paths={str(s) for s in shots},
        )
        loaded = load_session(path)

    resolved = loaded.recordings[0].screenshot_paths
    assert len(resolved) == 42
    for source, cached_str in zip(shots, resolved):
        cached = Path(cached_str)
        assert cached != source
        assert appdata in cached.parents
        assert "cache" in cached.relative_to(appdata).parts
        assert cached.is_file()
        assert cached.read_bytes() == source.read_bytes()


def test_manual_only_embed_references_auto_paths(tmp_path: Path) -> None:
    """embed_paths = manual set keeps auto sidecars referenced by path.

    The current behaviour: only the manual attachment is embedded; the
    referenced auto paths survive a reload unchanged.
    """
    manual = tmp_path / "cover.jpg"
    manual.write_bytes(b"\xff\xd8 manual")
    auto1 = tmp_path / "auto1.jpg"
    auto1.write_bytes(b"\xff\xd8 auto1")
    auto2 = tmp_path / "auto2.jpg"
    auto2.write_bytes(b"\xff\xd8 auto2")
    rec = _full_recording(screenshots=[str(manual), str(auto1), str(auto2)])
    rec.manual_paths = {str(manual)}
    data = SessionData(display_name="Manual", layout=_layout(), recordings=[rec])
    appdata = tmp_path / "appdata"

    with patch(
        "bootleg_catalog.core.session_store.app_data_dir",
        return_value=appdata,
    ):
        path = save_session(
            data, tmp_path / "session.bootleg", embed_paths={str(manual)}
        )
        loaded = load_session(path)

    resolved = loaded.recordings[0].screenshot_paths
    # manual -> extracted into the cache; auto -> preserved as-is.
    assert resolved[0] != str(manual)
    assert Path(resolved[0]).is_file()
    assert resolved[1] == str(auto1)
    assert resolved[2] == str(auto2)
    assert loaded.recordings[0].manual_paths == {resolved[0]}


# ---------------------------------------------------------------------------
# Manual vs auto screenshot origins (L15.4, manifest v2)
# ---------------------------------------------------------------------------

def test_manual_embedded_auto_referenced_with_origins(tmp_path: Path) -> None:
    manual = tmp_path / "capa.jpg"
    manual.write_bytes(b"\xff\xd8 manual-bytes")
    auto = tmp_path / "sidecar.png"
    auto.write_bytes(b"\x89PNG auto-bytes")

    rec = _full_recording(screenshots=[str(manual), str(auto)])
    rec.manual_paths = {str(manual)}
    data = SessionData(
        display_name="Origens",
        layout=_layout(),
        recordings=[rec],
    )
    appdata = tmp_path / "appdata"

    with patch(
        "bootleg_catalog.core.session_store.app_data_dir",
        return_value=appdata,
    ):
        path = save_session(
            data, tmp_path / "session.bootleg", embed_paths={str(manual)}
        )
        with zipfile.ZipFile(path, "r") as zf:
            manifest = json.loads(zf.read(MANIFEST_NAME))
            names = zf.namelist()

        # v2 manifest: every entry carries an origin.
        assert manifest["format_version"] == FORMAT_VERSION == 4
        assert manifest["screenshots"] == [
            {
                "recording": _SOURCE_PATH,
                "mode": "embedded",
                "entry": "screenshots/r0001-s01.jpg",
                "origin": "manual",
            },
            {
                "recording": _SOURCE_PATH,
                "mode": "referenced",
                "path": str(auto),
                "origin": "auto",
            },
        ]
        # The manual bytes travelled inside the archive; the auto shot
        # stayed a plain path on disk.
        assert "screenshots/r0001-s01.jpg" in names
        assert not any(name.endswith("s02.png") for name in names)
        # The recordings blob serialises the manual subset as a list.
        assert manifest["recordings"][0]["manual_paths"] == [str(manual)]

        loaded = load_session(path)

    restored = loaded.recordings[0]
    assert restored.screenshot_paths[1] == str(auto)
    assert restored.screenshot_paths[0] != str(manual)  # resolved to the cache
    assert restored.manual_paths == {restored.screenshot_paths[0]}


def test_v1_manifest_without_origin_loads_as_all_auto(tmp_path: Path) -> None:
    shot = tmp_path / "sidecar.jpg"
    shot.write_bytes(b"\xff\xd8 v1-referenced")
    rec_blob = {
        key: value
        for key, value in asdict(_full_recording(screenshots=[str(shot)])).items()
        if key != "manual_paths"  # v1 predates the field
    }
    manifest = {
        "format_version": 1,
        "display_name": "Legado",
        "created_at": _CREATED_AT,
        "modified_at": _CREATED_AT,
        "layout": _layout().to_dict(),
        "recordings": [rec_blob],
        "screenshots": [
            # v1 entries have neither origin nor manual bookkeeping: one
            # referenced and one embedded shot, both loading as auto.
            {"recording": _SOURCE_PATH, "mode": "referenced", "path": str(shot)},
            {
                "recording": _SOURCE_PATH,
                "mode": "embedded",
                "entry": "screenshots/r0001-s02.jpg",
            },
        ],
    }
    path = tmp_path / "v1.bootleg"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False))
        zf.writestr("screenshots/r0001-s02.jpg", b"\xff\xd8 v1-embedded")

    appdata = tmp_path / "appdata"
    with patch(
        "bootleg_catalog.core.session_store.app_data_dir",
        return_value=appdata,
    ):
        loaded = load_session(path)

    assert loaded.format_version == 1  # the file's own version, not rewritten
    restored = loaded.recordings[0]
    assert restored.screenshot_paths[0] == str(shot)
    assert Path(restored.screenshot_paths[1]).is_file()  # extracted to cache
    assert restored.manual_paths == set()


def test_origin_round_trip_survives_save_load_save(tmp_path: Path) -> None:
    manual = tmp_path / "capa.jpg"
    manual.write_bytes(b"\xff\xd8 keep-me-manual")
    auto = tmp_path / "sidecar.png"
    auto.write_bytes(b"\x89PNG keep-me-auto")

    rec = _full_recording(screenshots=[str(manual), str(auto)])
    rec.manual_paths = {str(manual)}
    data = SessionData(
        display_name="Round Trip",
        layout=_layout(),
        recordings=[rec],
    )
    appdata = tmp_path / "appdata"

    with patch(
        "bootleg_catalog.core.session_store.app_data_dir",
        return_value=appdata,
    ):
        first = save_session(
            data, tmp_path / "first.bootleg", embed_paths={str(manual)}
        )
        loaded1 = load_session(first)
        rec1 = loaded1.recordings[0]
        assert rec1.screenshot_paths[0] != str(manual)  # resolved to the cache
        assert rec1.manual_paths == {rec1.screenshot_paths[0]}

        # Re-save the loaded session: the manual shot's source is now the
        # cache path, which the caller passes as embed_paths again.
        second = save_session(
            loaded1,
            tmp_path / "second.bootleg",
            embed_paths={rec1.screenshot_paths[0]},
        )
        with zipfile.ZipFile(second, "r") as zf:
            names = zf.namelist()
            manifest = json.loads(zf.read(MANIFEST_NAME))
        assert "screenshots/r0001-s01.jpg" in names
        assert manifest["format_version"] == FORMAT_VERSION == 4
        assert manifest["screenshots"][0]["origin"] == "manual"
        assert manifest["screenshots"][1]["origin"] == "auto"

        loaded2 = load_session(second)

    rec2 = loaded2.recordings[0]
    assert rec2.screenshot_paths[1] == str(auto)
    assert rec2.manual_paths == {rec2.screenshot_paths[0]}
    # The second file has its own cache directory, so the manual path
    # was re-extracted there rather than reused from the first session.
    assert rec2.screenshot_paths[0] != rec1.screenshot_paths[0]
    assert Path(rec2.screenshot_paths[0]).read_bytes() == b"\xff\xd8 keep-me-manual"


# ---------------------------------------------------------------------------
# Cache eviction (L15.5.1)
# ---------------------------------------------------------------------------

_EVICTION_NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
_EVICTION_OLD = _EVICTION_NOW - timedelta(days=CACHE_EVICTION_AGE_DAYS + 1)
_EVICTION_FRESH = _EVICTION_NOW - timedelta(days=1)


def _age_dir(path: Path, when: datetime) -> None:
    """Pin a directory's mtime to a fixed instant (no wall clock)."""
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


def _evict(appdata: Path, **kwargs) -> int:
    """Run evict_cache against *appdata* as the app data directory."""
    with patch(
        "bootleg_catalog.core.session_store.app_data_dir",
        return_value=appdata,
    ):
        return evict_cache(**kwargs)


def test_evict_cache_missing_root_returns_zero(tmp_path: Path) -> None:
    # tmp_path / "cache" is never created.
    assert _evict(tmp_path, now=_EVICTION_NOW) == 0


def test_evict_cache_preserves_fresh_subdirectory(tmp_path: Path) -> None:
    fresh = tmp_path / "cache" / "0123456789abcdef"
    fresh.mkdir(parents=True)
    (fresh / "screenshots").mkdir()
    _age_dir(fresh, _EVICTION_FRESH)  # after child creation: pins the mtime

    removed = _evict(tmp_path, now=_EVICTION_NOW)

    assert removed == 0
    assert fresh.is_dir()
    assert (fresh / "screenshots").is_dir()


def test_evict_cache_deletes_old_subdirectory_and_counts(tmp_path: Path) -> None:
    old = tmp_path / "cache" / "aaaaaaaaaaaaaaaa"
    old.mkdir(parents=True)
    shot = old / "screenshots" / "r0001-s01.jpg"
    shot.parent.mkdir()
    shot.write_bytes(b"\xff\xd8")
    _age_dir(old, _EVICTION_OLD)

    removed = _evict(tmp_path, now=_EVICTION_NOW)

    assert removed == 1
    assert not old.exists()


def test_evict_cache_preserves_old_subdirectory_in_keep_paths(
    tmp_path: Path,
) -> None:
    old = tmp_path / "cache" / "bbbbbbbbbbbbbbbb"
    old.mkdir(parents=True)
    shot = old / "screenshots" / "r0001-s01.jpg"
    shot.parent.mkdir()
    shot.write_bytes(b"\xff\xd8")
    _age_dir(old, _EVICTION_OLD)

    removed = _evict(tmp_path, keep_paths={str(shot)}, now=_EVICTION_NOW)

    assert removed == 0
    assert shot.is_file()


def test_evict_cache_never_raises_when_unreadable(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    root.mkdir()
    old = root / "cccccccccccccccc"
    old.mkdir()
    _age_dir(old, _EVICTION_OLD)

    with patch(
        "bootleg_catalog.core.session_store.app_data_dir",
        return_value=tmp_path,
    ), patch(
        "pathlib.Path.iterdir",
        side_effect=OSError("permission denied"),
    ):
        assert evict_cache(now=_EVICTION_NOW) == 0


def test_evict_cache_empty_keep_paths_equals_none(tmp_path: Path) -> None:
    def run(tag: str, **keep_kwargs) -> tuple[int, bool]:
        appdata = tmp_path / tag
        old = appdata / "cache" / "old00000000000000"
        old.mkdir(parents=True)
        _age_dir(old, _EVICTION_OLD)
        removed = _evict(appdata, now=_EVICTION_NOW, **keep_kwargs)
        return removed, not old.exists()

    # keep_paths omitted (None) and keep_paths=set() behave identically.
    assert run("withoutkeep") == (1, True)
    assert run("emptykeep", keep_paths=set()) == (1, True)


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

def test_save_is_atomic_on_replace_failure(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    target = out_dir / "session.bootleg"
    data = _session_data()

    with patch("pathlib.Path.replace", side_effect=OSError("replace failed")):
        with pytest.raises(SessionStoreError, match="Could not save"):
            save_session(data, target)

    assert not target.exists()
    assert list(out_dir.iterdir()) == []


def test_save_failure_chains_original_exception(tmp_path: Path) -> None:
    shot = tmp_path / "missing.jpg"  # never created, but must be embedded
    target = tmp_path / "session.bootleg"
    data = _session_data(screenshots=[str(shot)])

    with pytest.raises(SessionStoreError) as excinfo:
        save_session(data, target, embed_paths={str(shot)})

    assert isinstance(excinfo.value.__cause__, FileNotFoundError)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_load_truncated_zip_raises_session_store_error(tmp_path: Path) -> None:
    path = tmp_path / "truncated.bootleg"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 12)

    with pytest.raises(SessionStoreError) as excinfo:
        load_session(path)

    assert isinstance(excinfo.value.__cause__, zipfile.BadZipFile)


# ---------------------------------------------------------------------------
# ExportSessionService: dirty state and persistence
# ---------------------------------------------------------------------------

def test_service_dirty_flag_lifecycle(tmp_path: Path) -> None:
    svc = ExportSessionService(_layout())
    assert svc.is_dirty() is False

    media = tmp_path / "show.mkv"
    media.write_bytes(b"\x1a\x45\xdfa")
    with patch(
        "bootleg_catalog.core.export_session_service.probe",
        return_value=_probe_value(),
    ):
        added = svc.add_recording_from_path(media)
    assert added.duration == 7200.0
    assert svc.is_dirty() is True
    assert svc.session.recording_count == 1

    saved = svc.save(tmp_path / "service.bootleg")
    assert saved == tmp_path / "service.bootleg"
    assert svc.is_dirty() is False

    svc.set_layout(_layout("Layout Alternativo"))
    assert svc.is_dirty() is True
    assert svc.layout.name == "Layout Alternativo"

    svc.load(saved)
    assert svc.is_dirty() is False
    assert svc.layout.name == "Lista Teste"
    assert svc.session.recording_count == 1
    assert svc.session.recordings[0].source_path == str(media.resolve())
    assert svc.session.recordings[0].duration == 7200.0


def test_service_add_recording_marks_dirty_and_increments_count() -> None:
    svc = ExportSessionService(_layout())
    assert svc.is_dirty() is False
    assert svc.session.recording_count == 0

    svc.add_recording(
        ExportRecording(source_path="D:/media/prebuilt.mkv", title="Pre-built")
    )

    assert svc.is_dirty() is True
    assert svc.session.recording_count == 1
    assert svc.session.recordings[0].title == "Pre-built"


def test_service_display_name_round_trip(tmp_path: Path) -> None:
    svc = ExportSessionService(_layout())
    assert svc.display_name == ""

    svc.set_display_name("Minha Lista de Gravações")
    assert svc.display_name == "Minha Lista de Gravações"
    assert svc.is_dirty() is True

    saved = svc.save(tmp_path / "named.bootleg")
    assert svc.is_dirty() is False

    fresh = ExportSessionService(_layout())
    fresh.load(saved)
    assert fresh.display_name == "Minha Lista de Gravações"
    assert fresh.is_dirty() is False


def test_service_dirty_after_remove_update_clear() -> None:
    # remove_recording
    remover = ExportSessionService(_layout())
    remover.session.add_recording(ExportRecording(source_path="D:/media/a.mkv"))
    remover.mark_clean()
    remover.remove_recording("D:/media/a.mkv")
    assert remover.is_dirty() is True
    assert remover.session.recording_count == 0

    # update_recording
    updater = ExportSessionService(_layout())
    updater.session.add_recording(
        ExportRecording(source_path="D:/media/b.mkv", title="Old")
    )
    updater.mark_clean()
    updater.update_recording(
        "D:/media/b.mkv",
        ExportRecording(source_path="D:/media/b.mkv", title="New"),
    )
    assert updater.is_dirty() is True
    assert updater.session.recordings[0].title == "New"

    # clear
    clearer = ExportSessionService(_layout())
    clearer.session.add_recording(ExportRecording(source_path="D:/media/c.mkv"))
    clearer.mark_clean()
    clearer.clear()
    assert clearer.is_dirty() is True
    assert clearer.session.recording_count == 0


# ---------------------------------------------------------------------------
# Manifest v3: recording parts (L15.6.1b)
# ---------------------------------------------------------------------------

_PART_A = "D:/media/show-part1.mkv"
_PART_B = "D:/media/show-part2.mkv"


def _write_manifest_zip(path: Path, manifest: dict) -> Path:
    """Write a hand-crafted manifest zip (fixtures for v1/v2 loading)."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False))
    return path


def _v2_recording_blob() -> dict:
    """A v2 recording dict: no ``parts`` (v3) and no ``screenshot_split`` (v4)."""
    blob = {
        key: value
        for key, value in asdict(_full_recording()).items()
        if key not in ("parts", "screenshot_split")
    }
    blob["manual_paths"] = sorted(blob["manual_paths"])
    return blob


def _v3_recording_blob() -> dict:
    """A v3 recording dict: ``parts`` present, no ``screenshot_split``."""
    blob = {
        key: value
        for key, value in asdict(_full_recording()).items()
        if key != "screenshot_split"
    }
    blob["manual_paths"] = sorted(blob["manual_paths"])
    return blob


def test_v3_two_part_recording_round_trips(tmp_path: Path) -> None:
    rec = ExportRecording(source_path=_PART_A, parts=(_PART_A, _PART_B))
    data = SessionData(
        display_name="Duas Partes",
        layout=_layout(),
        recordings=[rec],
    )

    path = save_session(data, tmp_path / "multipart.bootleg")
    with zipfile.ZipFile(path, "r") as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME))
    assert manifest["recordings"][0]["parts"] == [_PART_A, _PART_B]

    loaded = load_session(path)
    got = loaded.recordings[0]
    assert got.parts == (_PART_A, _PART_B)
    assert got.source_path == _PART_A


def test_v3_recording_without_explicit_parts_loads_as_single_part(
    tmp_path: Path,
) -> None:
    rec = ExportRecording(source_path=_SOURCE_PATH)  # parts never set
    data = SessionData(
        display_name="Simples",
        layout=_layout(),
        recordings=[rec],
    )

    path = save_session(data, tmp_path / "single.bootleg")
    loaded = load_session(path)

    assert loaded.recordings[0].parts == (_SOURCE_PATH,)


def test_v2_manifest_without_parts_loads_as_single_part(tmp_path: Path) -> None:
    manifest = {
        "format_version": 2,
        "display_name": "V2",
        "created_at": _CREATED_AT,
        "modified_at": _CREATED_AT,
        "layout": _layout().to_dict(),
        "recordings": [_v2_recording_blob()],
        "screenshots": [],
    }
    path = _write_manifest_zip(tmp_path / "v2.bootleg", manifest)

    loaded = load_session(path)

    assert loaded.format_version == 2  # the file's own version, not rewritten
    assert loaded.recordings[0].parts == (_SOURCE_PATH,)


def test_v1_manifest_loads_parts_and_manual_paths(tmp_path: Path) -> None:
    blob = {
        key: value
        for key, value in _v2_recording_blob().items()
        if key != "manual_paths"  # v1 predates the field
    }
    manifest = {
        "format_version": 1,
        "display_name": "V1",
        "created_at": _CREATED_AT,
        "modified_at": _CREATED_AT,
        "layout": _layout().to_dict(),
        "recordings": [blob],
        "screenshots": [],
    }
    path = _write_manifest_zip(tmp_path / "v1.bootleg", manifest)

    loaded = load_session(path)

    assert loaded.format_version == 1
    assert loaded.recordings[0].parts == (_SOURCE_PATH,)
    assert loaded.recordings[0].manual_paths == set()


def test_format_version_is_four() -> None:
    assert FORMAT_VERSION == 4


def test_v2_file_resaved_upgrades_to_v4_with_parts(tmp_path: Path) -> None:
    manifest = {
        "format_version": 2,
        "display_name": "Regravar",
        "created_at": _CREATED_AT,
        "modified_at": _CREATED_AT,
        "layout": _layout().to_dict(),
        "recordings": [_v2_recording_blob()],
        "screenshots": [],
    }
    v2_path = _write_manifest_zip(tmp_path / "v2.bootleg", manifest)

    loaded = load_session(v2_path)
    upgraded = save_session(loaded, tmp_path / "upgraded.bootleg")

    with zipfile.ZipFile(upgraded, "r") as zf:
        new_manifest = json.loads(zf.read(MANIFEST_NAME))
    assert new_manifest["format_version"] == 4
    assert new_manifest["recordings"]  # fixture has exactly one recording
    for rec_blob in new_manifest["recordings"]:
        assert rec_blob["parts"] == [_SOURCE_PATH]


# ---------------------------------------------------------------------------
# Manifest v4: screenshot_split (L15.6.3a)
# ---------------------------------------------------------------------------

def test_v4_round_trip_preserves_screenshot_split(tmp_path: Path) -> None:
    none_rec = ExportRecording(source_path=_SOURCE_PATH)
    int_rec = ExportRecording(
        source_path=_PART_A,
        parts=(_PART_A, _PART_B),
        screenshot_paths=["D:/shots/p1.jpg", "D:/shots/p2.jpg", "D:/shots/p3.jpg"],
        screenshot_split=1,
    )
    data = SessionData(
        display_name="Split",
        layout=_layout(),
        recordings=[none_rec, int_rec],
    )

    path = save_session(data, tmp_path / "split.bootleg")
    with zipfile.ZipFile(path, "r") as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME))
    assert manifest["recordings"][0]["screenshot_split"] is None
    assert manifest["recordings"][1]["screenshot_split"] == 1

    loaded = load_session(path)
    assert loaded.recordings[0].screenshot_split is None
    assert loaded.recordings[1].screenshot_split == 1
    assert loaded.recordings[1].parts == (_PART_A, _PART_B)


def test_v3_manifest_without_screenshot_split_loads_as_none(
    tmp_path: Path,
) -> None:
    manifest = {
        "format_version": 3,
        "display_name": "V3",
        "created_at": _CREATED_AT,
        "modified_at": _CREATED_AT,
        "layout": _layout().to_dict(),
        "recordings": [_v3_recording_blob()],
        "screenshots": [],
    }
    path = _write_manifest_zip(tmp_path / "v3.bootleg", manifest)

    loaded = load_session(path)

    assert loaded.format_version == 3  # the file's own version, not rewritten
    assert loaded.recordings[0].screenshot_split is None
    # v3 recordings do carry parts; the split key is the only v4 addition.
    assert loaded.recordings[0].parts == (_SOURCE_PATH,)


# ---------------------------------------------------------------------------
# is_rip (L15.8.1)
# ---------------------------------------------------------------------------

def test_is_rip_round_trips_and_defaults_false(tmp_path: Path) -> None:
    rip_rec = ExportRecording(
        source_path="D:/rips/concert",
        extension="VIDEO_TS",
        is_rip=True,
    )
    plain_rec = ExportRecording(source_path=_SOURCE_PATH)
    data = SessionData(
        display_name="Rips",
        layout=_layout(),
        recordings=[rip_rec, plain_rec],
    )

    path = save_session(data, tmp_path / "rips.bootleg")
    with zipfile.ZipFile(path, "r") as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME))
    assert manifest["recordings"][0]["is_rip"] is True
    assert manifest["recordings"][1]["is_rip"] is False

    loaded = load_session(path)
    assert loaded.recordings[0].is_rip is True
    assert loaded.recordings[0].extension == "VIDEO_TS"
    assert loaded.recordings[1].is_rip is False


def test_v4_manifest_without_is_rip_loads_as_false(tmp_path: Path) -> None:
    """Pre-L15.8.1 manifests lack the key: they load as False, which is
    why the format version stays 4 (backward-compatible extra field)."""
    manifest = {
        "format_version": 4,
        "display_name": "V4",
        "created_at": _CREATED_AT,
        "modified_at": _CREATED_AT,
        "layout": _layout().to_dict(),
        "recordings": [_v3_recording_blob()],
        "screenshots": [],
    }
    path = _write_manifest_zip(tmp_path / "v4-no-rip.bootleg", manifest)

    loaded = load_session(path)

    assert loaded.format_version == 4
    assert loaded.recordings[0].is_rip is False


def test_trade_status_round_trips_and_defaults_none(tmp_path: Path) -> None:
    """Trade status survives save/load; manifests written before the
    field existed load as None (backward-compatible extra field,
    format version stays 4)."""
    marked = ExportRecording(source_path=_SOURCE_PATH, trade_status="NFTF")
    unmarked = ExportRecording(source_path="D:/media/encore.mkv")
    data = SessionData(
        display_name="Trade",
        layout=_layout(),
        recordings=[marked, unmarked],
    )

    path = save_session(data, tmp_path / "trade.bootleg")
    with zipfile.ZipFile(path, "r") as zf:
        manifest = json.loads(zf.read(MANIFEST_NAME))
    assert manifest["recordings"][0]["trade_status"] == "NFTF"
    assert manifest["recordings"][1]["trade_status"] is None

    loaded = load_session(path)
    assert loaded.recordings[0].trade_status == "NFTF"
    assert loaded.recordings[1].trade_status is None

    # A v4 manifest without the key loads as None via the dataclass default.
    old_manifest = {
        "format_version": 4,
        "display_name": "Old",
        "created_at": _CREATED_AT,
        "modified_at": _CREATED_AT,
        "layout": _layout().to_dict(),
        "recordings": [_v3_recording_blob()],
        "screenshots": [],
    }
    old_path = _write_manifest_zip(tmp_path / "v4-no-trade.bootleg", old_manifest)
    old_loaded = load_session(old_path)
    assert old_loaded.recordings[0].trade_status is None


# ---------------------------------------------------------------------------
# Module independence
# ---------------------------------------------------------------------------

def test_session_store_module_is_pure() -> None:
    source = inspect.getsource(session_store)
    for token in (
        "PySide6",
        "Catalog",
        "CatalogRepository",
        "sqlite3",
        "bootleg_catalog.core.catalog",
        "bootleg_catalog.database",
    ):
        assert token not in source, token
