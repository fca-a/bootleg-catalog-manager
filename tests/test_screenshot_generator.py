"""Tests for the FFmpeg screenshot generator core (L15.9.2, bugs A/D)."""

from __future__ import annotations

import random
from pathlib import Path
from unittest.mock import patch

import pytest

from bootleg_catalog.core.screenshot_generator import (
    ScreenshotError,
    ScreenshotPlan,
    generate_screenshots,
    generate_screenshots_for_recording,
    random_timestamps,
)
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.utils.ffmpeg import ProbeResult


# ---------------------------------------------------------------------------
# random_timestamps
# ---------------------------------------------------------------------------


class TestRandomTimestamps:
    def test_five_picks_inside_the_5_95_percent_window(self) -> None:
        """Five random picks on an hour, inside 5–95% of the runtime."""
        timestamps = random_timestamps(3600, 5)

        assert len(timestamps) == 5
        assert all(isinstance(ts, float) for ts in timestamps)
        assert timestamps[0] >= 180.0    # 5% of 3600
        assert timestamps[-1] <= 3420.0  # 95% of 3600

    def test_zero_duration_raises_screenshot_error(self) -> None:
        """No duration means nothing to seek into."""
        with pytest.raises(ScreenshotError):
            random_timestamps(0, 5)

    def test_zero_count_raises_value_error(self) -> None:
        """A count below 1 is a caller bug, hence ValueError."""
        with pytest.raises(ValueError):
            random_timestamps(3600, 0)

    def test_one_random_timestamp_per_window_segment(self) -> None:
        """Bug D: one pick per 1/5 slice of the 5–95% window."""
        timestamps = random_timestamps(3600, 5)

        slice_width = 3240.0 / 5  # 95% - 5% of 3600
        for index, ts in enumerate(timestamps):
            low = 180.0 + index * slice_width
            assert low <= ts <= low + slice_width

    def test_two_runs_with_different_seeds_differ(self) -> None:
        """Bug D: each run draws fresh values (different seed, different
        output — same-input runs are random, so the seeds make this
        deterministic instead of flaky)."""
        random.seed(20261014)
        first = random_timestamps(3600, 5)
        random.seed(20261015)
        second = random_timestamps(3600, 5)

        assert first != second


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


# ---------------------------------------------------------------------------
# generate_screenshots_for_recording (Bug A: multi-part auto generation)
# ---------------------------------------------------------------------------


class TestGenerateScreenshotsForRecording:
    """Auto generation spans both parts of a multi-part recording."""

    @staticmethod
    def _two_part(tmp_path: Path) -> tuple[Path, Path, ExportRecording]:
        part1 = tmp_path / "concert.mkv"
        part2 = tmp_path / "concert2.mkv"
        recording = ExportRecording(
            source_path=str(part1),
            parts=(str(part1), str(part2)),
            duration=7200.0,  # aggregate: part1 3600 + part2 3600
        )
        return part1, part2, recording

    def test_single_part_recording_behaves_as_before(
        self, tmp_path: Path,
    ) -> None:
        """1 part: same file, same seeded plan, no probing (regression)."""
        video = tmp_path / "concert.mkv"
        recording = ExportRecording(source_path=str(video), duration=3600.0)

        random.seed(4242)
        expected = random_timestamps(3600, 3)
        random.seed(4242)
        with patch(
            "bootleg_catalog.core.screenshot_generator.subprocess.run",
        ) as mock_run, patch(
            "bootleg_catalog.core.screenshot_generator.probe",
        ) as mock_probe:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            written = generate_screenshots_for_recording(recording, 3)

        mock_probe.assert_not_called()
        assert mock_run.call_count == 3
        for call, timestamp in zip(mock_run.call_args_list, expected):
            cmd = call.args[0]
            assert cmd[cmd.index("-i") + 1] == str(video)
            assert cmd[cmd.index("-ss") + 1] == str(timestamp)
        assert all(Path(p).parent == tmp_path for p in written)

    def test_two_part_splits_counts_and_runs_part1_then_part2(
        self, tmp_path: Path,
    ) -> None:
        """5 frames -> 3 from part 1 then 2 from part 2; probe once each."""
        part1, part2, recording = self._two_part(tmp_path)

        with patch(
            "bootleg_catalog.core.screenshot_generator.subprocess.run",
        ) as mock_run, patch(
            "bootleg_catalog.core.screenshot_generator.probe",
        ) as mock_probe:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            mock_probe.return_value = ProbeResult(duration=3600.0)
            generate_screenshots_for_recording(recording, 5)

        assert mock_run.call_count == 5
        sources = [
            call.args[0][call.args[0].index("-i") + 1]
            for call in mock_run.call_args_list
        ]
        assert sources == [str(part1)] * 3 + [str(part2)] * 2
        # Each part is re-probed exactly once, with its own path.
        assert sorted(
            [call.args[0] for call in mock_probe.call_args_list]
        ) == sorted([str(part1), str(part2)])

    def test_two_part_outputs_stay_next_to_their_own_part(
        self, tmp_path: Path,
    ) -> None:
        """Part 1 sidecars beside part 1; part 2 restarts at 01."""
        part1, part2, recording = self._two_part(tmp_path)

        with patch(
            "bootleg_catalog.core.screenshot_generator.subprocess.run",
        ) as mock_run, patch(
            "bootleg_catalog.core.screenshot_generator.probe",
        ) as mock_probe:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            mock_probe.return_value = ProbeResult(duration=3600.0)
            written = generate_screenshots_for_recording(recording, 5)

        assert [Path(p).name for p in written] == [
            "concert.screenshot.01.jpg",
            "concert.screenshot.02.jpg",
            "concert.screenshot.03.jpg",
            "concert2.screenshot.01.jpg",
            "concert2.screenshot.02.jpg",
        ]
        assert all(Path(p).parent == tmp_path for p in written)
