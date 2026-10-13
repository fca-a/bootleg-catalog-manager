"""Tests for the FFmpeg screenshot generator core (L15.9.2)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.core.screenshot_generator import (
    ScreenshotError,
    ScreenshotPlan,
    auto_timestamps,
    generate_screenshots,
)


# ---------------------------------------------------------------------------
# auto_timestamps
# ---------------------------------------------------------------------------


class TestAutoTimestamps:
    def test_five_spaced_inside_the_95_percent_window(self) -> None:
        """Five points across an hour, strictly increasing, in 5–95%."""
        timestamps = auto_timestamps(3600, 5)

        assert len(timestamps) == 5
        assert all(isinstance(ts, float) for ts in timestamps)
        assert all(
            later > earlier
            for earlier, later in zip(timestamps, timestamps[1:])
        )
        assert timestamps[0] >= 180.0    # 5% of 3600
        assert timestamps[-1] <= 3420.0  # 95% of 3600

    def test_zero_duration_raises_screenshot_error(self) -> None:
        """No duration means nothing to seek into."""
        with pytest.raises(ScreenshotError):
            auto_timestamps(0, 5)

    def test_zero_count_raises_value_error(self) -> None:
        """A count below 1 is a caller bug, hence ValueError."""
        with pytest.raises(ValueError):
            auto_timestamps(3600, 0)


# ---------------------------------------------------------------------------
# generate_screenshots
# ---------------------------------------------------------------------------


class TestGenerateScreenshots:
    def test_one_ffmpeg_call_per_timestamp(self, tmp_path: Path) -> None:
        """Three timestamps -> three subprocess calls, each -ss its own."""
        video = tmp_path / "concert.mkv"
        plan = ScreenshotPlan((10.0, 20.0, 30.0))

        with patch(
            "bootleg_catalog.core.screenshot_generator.subprocess.run",
        ) as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            result = generate_screenshots(video, plan)

        assert mock_run.call_count == 3
        for call, timestamp in zip(
            mock_run.call_args_list, (10.0, 20.0, 30.0),
        ):
            cmd = call.args[0]
            ss_index = cmd.index("-ss")
            assert cmd[ss_index + 1] == str(timestamp)
        assert len(result) == 3

    def test_existing_output_refuses_before_running_ffmpeg(
        self, tmp_path: Path,
    ) -> None:
        """overwrite=False raises before any process is spawned."""
        video = tmp_path / "concert.mkv"
        plan = ScreenshotPlan((10.0,))
        existing = tmp_path / "concert.screenshot.01.jpg"
        existing.write_bytes(b"already here")

        with patch(
            "bootleg_catalog.core.screenshot_generator.subprocess.run",
        ) as mock_run:
            with pytest.raises(ScreenshotError):
                generate_screenshots(video, plan, overwrite=False)

        mock_run.assert_not_called()
