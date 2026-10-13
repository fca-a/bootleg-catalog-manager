"""Tests for ``resolve_ffmpeg`` and the layout service's screenshot helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.core.layout_service import (
    GridCell,
    ScreenshotLayout,
    compute_grid,
    row_count,
    screenshots_for_recording,
)
from bootleg_catalog.utils.ffmpeg import FFmpegError, resolve_ffmpeg


# ---------------------------------------------------------------------------
# resolve_ffmpeg
# ---------------------------------------------------------------------------

class TestResolveFFmpeg:
    def test_explicit_path(self, tmp_path: Path) -> None:
        exe = tmp_path / "ffmpeg"
        exe.touch()
        assert resolve_ffmpeg(exe) == str(exe)

    def test_explicit_path_not_found(self) -> None:
        with pytest.raises(FFmpegError, match="not found"):
            resolve_ffmpeg("/nonexistent/ffmpeg")

    def test_env_variable(self, tmp_path: Path) -> None:
        ffmpeg = tmp_path / "ffmpeg"
        ffmpeg.touch()
        with patch.dict("os.environ", {"FFMPEG_PATH": str(ffmpeg)}):
            result = resolve_ffmpeg()
            assert result == str(ffmpeg)

    def test_not_found_on_path(self) -> None:
        with patch("shutil.which", return_value=None):
            with patch.dict("os.environ", {"FFMPEG_PATH": ""}, clear=False):
                with pytest.raises(FFmpegError, match="not found"):
                    resolve_ffmpeg()

    def test_shutil_which_fallback(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            with patch.dict("os.environ", {"FFMPEG_PATH": ""}, clear=False):
                result = resolve_ffmpeg()
                assert result == "/usr/bin/ffmpeg"


# ===========================================================================
# LAYOUT SERVICE
# ===========================================================================

class TestScreenshotLayout:
    def test_default(self) -> None:
        layout = ScreenshotLayout()
        assert layout.photos_per_row == 3

    def test_custom(self) -> None:
        layout = ScreenshotLayout(photos_per_row=5)
        assert layout.photos_per_row == 5

    def test_invalid(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            ScreenshotLayout(photos_per_row=0)


class TestComputeGrid:
    def test_three_per_row_six_items(self) -> None:
        layout = ScreenshotLayout(photos_per_row=3)
        paths = [f"/media/screenshot.{i:02d}.jpg" for i in range(1, 7)]
        cells = compute_grid(paths, layout)

        assert len(cells) == 6
        assert cells[0] == GridCell(0, 0, paths[0])
        assert cells[1] == GridCell(0, 1, paths[1])
        assert cells[2] == GridCell(0, 2, paths[2])
        assert cells[3] == GridCell(1, 0, paths[3])
        assert cells[4] == GridCell(1, 1, paths[4])
        assert cells[5] == GridCell(1, 2, paths[5])

    def test_two_per_row(self) -> None:
        layout = ScreenshotLayout(photos_per_row=2)
        paths = ["/a.jpg", "/b.jpg", "/c.jpg"]
        cells = compute_grid(paths, layout)

        assert cells[0] == GridCell(0, 0, "/a.jpg")
        assert cells[1] == GridCell(0, 1, "/b.jpg")
        assert cells[2] == GridCell(1, 0, "/c.jpg")

    def test_one_per_row(self) -> None:
        layout = ScreenshotLayout(photos_per_row=1)
        paths = ["/a.jpg", "/b.jpg", "/c.jpg"]
        cells = compute_grid(paths, layout)

        assert cells[0] == GridCell(0, 0, "/a.jpg")
        assert cells[1] == GridCell(1, 0, "/b.jpg")
        assert cells[2] == GridCell(2, 0, "/c.jpg")

    def test_four_per_row(self) -> None:
        layout = ScreenshotLayout(photos_per_row=4)
        paths = [f"/{i}.jpg" for i in range(7)]
        cells = compute_grid(paths, layout)

        assert len(cells) == 7
        # Last row has 3 items (incomplete)
        assert cells[4] == GridCell(1, 0, "/4.jpg")
        assert cells[5] == GridCell(1, 1, "/5.jpg")
        assert cells[6] == GridCell(1, 2, "/6.jpg")

    def test_empty_list(self) -> None:
        layout = ScreenshotLayout(photos_per_row=3)
        cells = compute_grid([], layout)
        assert cells == []

    def test_fewer_than_per_row(self) -> None:
        layout = ScreenshotLayout(photos_per_row=5)
        paths = ["/a.jpg", "/b.jpg"]
        cells = compute_grid(paths, layout)

        assert len(cells) == 2
        assert cells[0] == GridCell(0, 0, "/a.jpg")
        assert cells[1] == GridCell(0, 1, "/b.jpg")


class TestRowCount:
    def test_empty(self) -> None:
        assert row_count(0, ScreenshotLayout(3)) == 0

    def test_exact(self) -> None:
        assert row_count(6, ScreenshotLayout(3)) == 2

    def test_incomplete_last_row(self) -> None:
        assert row_count(7, ScreenshotLayout(3)) == 3

    def test_one_per_row(self) -> None:
        assert row_count(5, ScreenshotLayout(1)) == 5

    def test_one_item(self) -> None:
        assert row_count(1, ScreenshotLayout(3)) == 1


class TestScreenshotsForRecording:
    def test_multi_frame_found(self, tmp_path: Path) -> None:
        media = tmp_path / "concert.mkv"
        media.touch()
        for i in range(1, 4):
            (tmp_path / f"concert.screenshot.{i:02d}.jpg").touch()

        result = screenshots_for_recording(str(media))
        assert len(result) == 3
        assert result[0].endswith("concert.screenshot.01.jpg")
        assert result[2].endswith("concert.screenshot.03.jpg")

    def test_single_fallback(self, tmp_path: Path) -> None:
        media = tmp_path / "concert.mkv"
        media.touch()
        (tmp_path / "concert.screenshot.jpg").touch()

        result = screenshots_for_recording(str(media))
        assert len(result) == 1
        assert result[0].endswith("concert.screenshot.jpg")

    def test_none_found(self, tmp_path: Path) -> None:
        media = tmp_path / "concert.mkv"
        media.touch()

        result = screenshots_for_recording(str(media))
        assert result == []

    def test_multi_frame_takes_priority(self, tmp_path: Path) -> None:
        media = tmp_path / "concert.mkv"
        media.touch()
        # Both patterns exist
        (tmp_path / "concert.screenshot.jpg").touch()
        for i in range(1, 3):
            (tmp_path / f"concert.screenshot.{i:02d}.jpg").touch()

        result = screenshots_for_recording(str(media))
        assert len(result) == 2  # Only multi-frame returned

    def test_ignores_non_numeric_names(self, tmp_path: Path) -> None:
        """Files like concert.screenshot.old.jpg are ignored."""
        media = tmp_path / "concert.mkv"
        media.touch()
        # Valid
        (tmp_path / "concert.screenshot.01.jpg").touch()
        (tmp_path / "concert.screenshot.02.jpg").touch()
        # Invalid — should be ignored
        (tmp_path / "concert.screenshot.old.jpg").touch()
        (tmp_path / "concert.screenshot.foo.jpg").touch()
        (tmp_path / "concert.screenshot.jpg").touch()  # single pattern, ignored when multi found

        result = screenshots_for_recording(str(media))
        assert len(result) == 2
        assert result[0].endswith("concert.screenshot.01.jpg")
        assert result[1].endswith("concert.screenshot.02.jpg")

    def test_numeric_sorting(self, tmp_path: Path) -> None:
        """Screenshots sorted by numeric index, not lexicographic."""
        media = tmp_path / "concert.mkv"
        media.touch()
        # Create in reverse order to verify numeric sorting
        for i in [3, 1, 2]:
            (tmp_path / f"concert.screenshot.{i:02d}.jpg").touch()

        result = screenshots_for_recording(str(media))
        assert len(result) == 3
        assert result[0].endswith("concert.screenshot.01.jpg")
        assert result[1].endswith("concert.screenshot.02.jpg")
        assert result[2].endswith("concert.screenshot.03.jpg")


