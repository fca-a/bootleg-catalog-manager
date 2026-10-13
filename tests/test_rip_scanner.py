"""Tests for the rip scanner (physical-media rip detection)."""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from bootleg_catalog.core import rip_scanner
from bootleg_catalog.core.rip_scanner import (
    FOLDER_SCAN_LIMIT_BYTES,
    RipScanError,
    RipScanResult,
    detect_rip,
    scan_rip_folder,
)
from bootleg_catalog.utils.paths import format_file_size


# ---------------------------------------------------------------------------
# Helpers — tiny fake rips (a few bytes per file)
# ---------------------------------------------------------------------------

def _make_dvd_rip(root: Path) -> Path:
    """root/VIDEO_TS with two .vob streams plus .ifo/.bup companions.

    Creation order is intentionally vob2 before vob1 to prove the result
    order comes from sorting, not discovery order.
    Total on-disk bytes: 10 + 20 + 5 + 5 = 40.
    """
    video_ts = root / "VIDEO_TS"
    video_ts.mkdir(parents=True)
    (video_ts / "VTS_01_2.VOB").write_bytes(b"V" * 20)
    (video_ts / "VTS_01_1.VOB").write_bytes(b"V" * 10)
    (video_ts / "VTS_01_1.IFO").write_bytes(b"I" * 5)
    (video_ts / "VTS_01_1.BUP").write_bytes(b"B" * 5)
    return root


def _make_bluray_rip(root: Path) -> Path:
    """root/BDMV with STREAM/*.m2ts plus PLAYLIST/ and CLIPINF/.
    Total on-disk bytes: 30 + 40 + 7 + 3 = 80.
    """
    stream = root / "BDMV" / "STREAM"
    stream.mkdir(parents=True)
    (stream / "00001.m2ts").write_bytes(b"M" * 40)
    (stream / "00000.m2ts").write_bytes(b"M" * 30)
    playlist = root / "BDMV" / "PLAYLIST"
    playlist.mkdir(parents=True)
    (playlist / "00000.mpls").write_bytes(b"P" * 7)
    clipinf = root / "BDMV" / "CLIPINF"
    clipinf.mkdir(parents=True)
    (clipinf / "00000.clpi").write_bytes(b"C" * 3)
    return root


# ---------------------------------------------------------------------------
# detect_rip: not-a-rip cases
# ---------------------------------------------------------------------------

def test_detect_returns_none_for_empty_folder(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert detect_rip(tmp_path / "empty") is None


def test_detect_returns_none_for_unrelated_files(tmp_path: Path) -> None:
    folder = tmp_path / "misc"
    folder.mkdir()
    (folder / "readme.txt").write_bytes(b"hello")
    (folder / "a.mkv").write_bytes(b"\x00" * 8)
    assert detect_rip(folder) is None


def test_detect_returns_none_for_missing_folder(tmp_path: Path) -> None:
    assert detect_rip(tmp_path / "does-not-exist") is None


# ---------------------------------------------------------------------------
# detect_rip: rip detection
# ---------------------------------------------------------------------------

def test_detect_dvd_video_ts_folder(tmp_path: Path) -> None:
    rip = _make_dvd_rip(tmp_path / "dvd")

    result = detect_rip(rip)

    assert result is not None
    assert result.rip_kind == "dvd"
    assert result.folder_path == str(rip)
    assert len(result.video_files) == 2
    assert result.total_size_bytes == 40


def test_detect_bluray_bdmv_folder(tmp_path: Path) -> None:
    rip = _make_bluray_rip(tmp_path / "bd")

    result = detect_rip(rip)

    assert result is not None
    assert result.rip_kind == "bluray"
    assert result.folder_path == str(rip)
    assert [Path(p).name for p in result.video_files] == [
        "00000.m2ts",
        "00001.m2ts",
    ]
    assert result.total_size_bytes == 80


def test_detect_when_folder_itself_is_video_ts(tmp_path: Path) -> None:
    rip = _make_dvd_rip(tmp_path / "disc")

    result = detect_rip(rip / "VIDEO_TS")

    assert result is not None
    assert result.rip_kind == "dvd"
    assert result.folder_path == str(rip / "VIDEO_TS")
    assert len(result.video_files) == 2
    assert result.total_size_bytes == 40  # the whole VIDEO_TS walked


def test_detect_when_folder_itself_is_bdmv(tmp_path: Path) -> None:
    rip = _make_bluray_rip(tmp_path / "disc")

    result = detect_rip(rip / "BDMV")

    assert result is not None
    assert result.rip_kind == "bluray"
    assert result.folder_path == str(rip / "BDMV")
    assert len(result.video_files) == 2
    assert result.total_size_bytes == 80  # STREAM + PLAYLIST + CLIPINF


# ---------------------------------------------------------------------------
# detect_rip: video selection and size accounting
# ---------------------------------------------------------------------------

def test_video_files_are_absolute_sorted_and_stream_only(tmp_path: Path) -> None:
    dvd = _make_dvd_rip(tmp_path / "dvd")
    bd = _make_bluray_rip(tmp_path / "bd")

    dvd_result = detect_rip(dvd)
    assert dvd_result is not None
    # Plain sorted(); no .ifo/.bup.
    assert dvd_result.video_files == [
        str(dvd / "VIDEO_TS" / "VTS_01_1.VOB"),
        str(dvd / "VIDEO_TS" / "VTS_01_2.VOB"),
    ]
    assert dvd_result.video_files == sorted(dvd_result.video_files)
    assert all(Path(p).is_absolute() for p in dvd_result.video_files)
    assert all(Path(p).suffix.lower() == ".vob" for p in dvd_result.video_files)

    bd_result = detect_rip(bd)
    assert bd_result is not None
    # No .mpls/.clpi.
    assert bd_result.video_files == [
        str(bd / "BDMV" / "STREAM" / "00000.m2ts"),
        str(bd / "BDMV" / "STREAM" / "00001.m2ts"),
    ]
    assert all(Path(p).suffix.lower() == ".m2ts" for p in bd_result.video_files)


def test_total_size_includes_all_files_not_only_videos(tmp_path: Path) -> None:
    dvd = _make_dvd_rip(tmp_path / "dvd")
    bd = _make_bluray_rip(tmp_path / "bd")

    dvd_result = detect_rip(dvd)
    assert dvd_result is not None
    # 10 + 20 (.vob) + 5 (.ifo) + 5 (.bup); videos alone would be 30.
    assert dvd_result.total_size_bytes == 40

    bd_result = detect_rip(bd)
    assert bd_result is not None
    # 30 + 40 (.m2ts) + 7 (.mpls) + 3 (.clpi); videos alone would be 70.
    assert bd_result.total_size_bytes == 80


def test_symlinked_subdirectory_not_followed(tmp_path: Path) -> None:
    rip = _make_dvd_rip(tmp_path / "dvd")  # 40 bytes
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "bulk.bin").write_bytes(b"X" * 4096)

    link = rip / "EXTRA"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this system")

    result = detect_rip(rip)

    assert result is not None
    assert result.total_size_bytes == 40  # nothing from behind the symlink


# ---------------------------------------------------------------------------
# scan_rip_folder: errors
# ---------------------------------------------------------------------------

def test_scan_raises_when_folder_is_not_a_rip(tmp_path: Path) -> None:
    folder = tmp_path / "not_a_rip"
    folder.mkdir()
    (folder / "a.mkv").write_bytes(b"\x00")

    with pytest.raises(RipScanError) as excinfo:
        scan_rip_folder(folder)

    msg = str(excinfo.value)
    assert "Not a rip folder" in msg
    assert str(folder) in msg  # message mentions the folder path


def test_scan_raises_when_folder_does_not_exist(tmp_path: Path) -> None:
    missing = tmp_path / "gone"

    with pytest.raises(RipScanError) as excinfo:
        scan_rip_folder(missing)

    msg = str(excinfo.value)
    assert "does not exist" in msg
    assert str(missing) in msg


def test_scan_raises_when_rip_has_zero_video_files(tmp_path: Path) -> None:
    rip = tmp_path / "empty_dvd"
    video_ts = rip / "VIDEO_TS"
    video_ts.mkdir(parents=True)
    (video_ts / "VIDEO_TS.IFO").write_bytes(b"I" * 4)  # structure, no .vob

    with pytest.raises(RipScanError) as excinfo:
        scan_rip_folder(rip)

    msg = str(excinfo.value)
    assert "no video files" in msg
    assert str(rip) in msg


def test_scan_raises_when_total_size_exceeds_limit(tmp_path: Path) -> None:
    rip = tmp_path / "big_dvd"
    video_ts = rip / "VIDEO_TS"
    video_ts.mkdir(parents=True)
    (video_ts / "VTS_01_1.VOB").write_bytes(b"V" * 2048)

    detected = detect_rip(rip)
    assert detected is not None
    assert detected.total_size_bytes > 1024

    with pytest.raises(RipScanError) as excinfo:
        scan_rip_folder(rip, size_limit=1024)

    msg = str(excinfo.value)
    assert format_file_size(detected.total_size_bytes) in msg
    assert format_file_size(1024) in msg
    assert "exceeds" in msg
    assert "Add individual recordings instead of the whole folder." in msg


# ---------------------------------------------------------------------------
# scan_rip_folder: success
# ---------------------------------------------------------------------------

def test_scan_succeeds_for_small_rip_under_limit(tmp_path: Path) -> None:
    rip = _make_dvd_rip(tmp_path / "dvd")

    result = scan_rip_folder(rip)

    assert isinstance(result, RipScanResult)
    assert result.rip_kind == "dvd"
    assert result.folder_path == str(rip)
    assert len(result.video_files) == 2
    assert result.total_size_bytes == 40


def test_scan_succeeds_with_generous_size_limit(tmp_path: Path) -> None:
    rip = _make_bluray_rip(tmp_path / "bd")

    result = scan_rip_folder(rip, size_limit=10**9)

    assert result.rip_kind == "bluray"
    assert result.total_size_bytes == 80


def test_scan_succeeds_when_size_equals_limit(tmp_path: Path) -> None:
    rip = _make_dvd_rip(tmp_path / "dvd")
    detected = detect_rip(rip)
    assert detected is not None

    # Equal to the limit: "exceeds" is strictly greater, so this passes.
    result = scan_rip_folder(rip, size_limit=detected.total_size_bytes)

    assert result.total_size_bytes == detected.total_size_bytes


# ---------------------------------------------------------------------------
# Constants and module independence
# ---------------------------------------------------------------------------

def test_folder_scan_limit_is_100_gib() -> None:
    assert FOLDER_SCAN_LIMIT_BYTES == 100 * 1024**3


def test_rip_scanner_module_is_pure() -> None:
    source = inspect.getsource(rip_scanner)
    for token in (
        "PySide6",
        "Catalog",
        "CatalogRepository",
        "sqlite3",
        "bootleg_catalog.core.catalog",
        "bootleg_catalog.database",
    ):
        assert token not in source, token
