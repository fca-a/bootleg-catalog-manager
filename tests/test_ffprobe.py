"""Tests for the FFprobe wrapper (bootleg_catalog.utils.ffmpeg).

All subprocess calls are mocked so these tests never require FFmpeg.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bootleg_catalog.utils.ffmpeg import FFprobeError, ProbeResult, probe, resolve_ffprobe

# ---------------------------------------------------------------------------
# Sample FFprobe JSON payloads
# ---------------------------------------------------------------------------

SAMPLE_VIDEO_JSON: dict = {
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "r_frame_rate": "30000/1001",
            "pix_fmt": "yuv420p",
            "bit_rate": "5000000",
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "channels": 2,
            "sample_rate": "48000",
            "bit_rate": "192000",
        },
    ],
    "format": {
        "format_name": "matroska,webm",
        "duration": "7200.000000",
        "bit_rate": "5200000",
        "nb_streams": 2,
    },
}

SAMPLE_AUDIO_ONLY_JSON: dict = {
    "streams": [
        {
            "index": 0,
            "codec_type": "audio",
            "codec_name": "flac",
            "channels": 2,
            "sample_rate": "44100",
        },
    ],
    "format": {
        "format_name": "flac",
        "duration": "342.5",
        "nb_streams": 1,
    },
}

SAMPLE_NO_AUDIO_JSON: dict = {
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "mpeg2video",
            "width": 720,
            "height": 480,
            "r_frame_rate": "30/1",
            "pix_fmt": "yuv420p",
        },
    ],
    "format": {
        "format_name": "mpeg",
        "duration": "5400.0",
        "nb_streams": 1,
    },
}

SAMPLE_EMPTY_JSON: dict = {
    "streams": [],
    "format": {},
}

SAMPLE_PARTIAL_JSON: dict = {
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h265",
            "width": 3840,
            "height": 2160,
            "pix_fmt": "yuv420p10le",
            # no bit_rate, no r_frame_rate
        },
    ],
    "format": {
        "format_name": "mp4",
        "duration": "120.0",
        "nb_streams": 1,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_run(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


# ---------------------------------------------------------------------------
# resolve_ffprobe tests
# ---------------------------------------------------------------------------

class TestResolveFFprobe:
    def test_explicit_path(self, tmp_path: Path) -> None:
        exe = tmp_path / "ffprobe"
        exe.touch()
        assert resolve_ffprobe(exe) == str(exe)

    def test_explicit_path_not_found(self) -> None:
        with pytest.raises(FFprobeError, match="not found"):
            resolve_ffprobe("/nonexistent/ffprobe")

    @patch.dict("os.environ", {"FFPROBE_PATH": ""})
    @patch("bootleg_catalog.utils.ffmpeg.shutil.which", return_value="/usr/bin/ffprobe")
    def test_shutil_which_fallback(self, mock_which: MagicMock) -> None:
        result = resolve_ffprobe()
        assert result == "/usr/bin/ffprobe"
        mock_which.assert_called_once_with("ffprobe")

    @patch.dict("os.environ", {"FFPROBE_PATH": "/opt/ffprobe"})
    @patch("bootleg_catalog.utils.ffmpeg.Path.is_file", return_value=True)
    def test_env_variable(self, mock_is_file: MagicMock) -> None:
        result = resolve_ffprobe()
        assert result == "/opt/ffprobe"

    @patch.dict("os.environ", {"FFPROBE_PATH": "/opt/bad_ffprobe"})
    @patch("bootleg_catalog.utils.ffmpeg.Path.is_file", return_value=False)
    @patch("bootleg_catalog.utils.ffmpeg.shutil.which", return_value=None)
    def test_env_variable_not_found(
        self, mock_which: MagicMock, mock_is_file: MagicMock
    ) -> None:
        with pytest.raises(FFprobeError, match="FFPROBE_PATH"):
            resolve_ffprobe()

    @patch("bootleg_catalog.utils.ffmpeg.shutil.which", return_value=None)
    def test_not_found_on_path(self, mock_which: MagicMock) -> None:
        with pytest.raises(FFprobeError, match="not found"):
            resolve_ffprobe()


# ---------------------------------------------------------------------------
# probe – success cases
# ---------------------------------------------------------------------------

class TestProbeSuccess:
    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_video_with_audio(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "concert.mkv"
        media.touch()
        mock_run.return_value = _mock_run(stdout=json.dumps(SAMPLE_VIDEO_JSON))

        r = probe(media)

        assert r.duration == 7200.0
        assert r.container_format == "matroska,webm"
        assert r.video_codec == "h264"
        assert r.video_width == 1920
        assert r.video_height == 1080
        assert abs(r.frame_rate - 29.97) < 0.01
        assert r.pixel_format == "yuv420p"
        assert r.video_bitrate == 5000000
        assert r.audio_codec == "aac"
        assert r.audio_channels == 2
        assert r.audio_sample_rate == 48000
        assert r.audio_bitrate == 192000
        assert r.stream_count == 2

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_audio_only(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "track.flac"
        media.touch()
        mock_run.return_value = _mock_run(stdout=json.dumps(SAMPLE_AUDIO_ONLY_JSON))

        r = probe(media)

        assert r.duration == 342.5
        assert r.container_format == "flac"
        assert r.video_codec is None
        assert r.video_width is None
        assert r.audio_codec == "flac"
        assert r.audio_channels == 2
        assert r.audio_sample_rate == 44100
        assert r.stream_count == 1

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_video_no_audio(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "silent.mpg"
        media.touch()
        mock_run.return_value = _mock_run(stdout=json.dumps(SAMPLE_NO_AUDIO_JSON))

        r = probe(media)

        assert r.video_codec == "mpeg2video"
        assert r.frame_rate == 30.0
        assert r.audio_codec is None
        assert r.audio_channels is None
        assert r.stream_count == 1

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_partial_data(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "partial.mp4"
        media.touch()
        mock_run.return_value = _mock_run(stdout=json.dumps(SAMPLE_PARTIAL_JSON))

        r = probe(media)

        assert r.video_codec == "h265"
        assert r.video_width == 3840
        assert r.frame_rate is None  # missing r_frame_rate
        assert r.video_bitrate is None  # missing bit_rate
        assert r.audio_codec is None  # no audio stream

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_empty_streams(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "empty.dat"
        media.touch()
        mock_run.return_value = _mock_run(stdout=json.dumps(SAMPLE_EMPTY_JSON))

        r = probe(media)

        assert r.duration is None
        assert r.container_format is None
        assert r.video_codec is None
        assert r.stream_count is None

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_explicit_ffprobe_path(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "show.mkv"
        media.touch()
        custom = tmp_path / "custom-ffprobe"
        custom.touch()
        mock_run.return_value = _mock_run(stdout=json.dumps(SAMPLE_VIDEO_JSON))

        probe(media, ffprobe_path=custom)

        mock_resolve.assert_called_once_with(custom)

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_custom_timeout(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "big.mkv"
        media.touch()
        mock_run.return_value = _mock_run(stdout=json.dumps(SAMPLE_VIDEO_JSON))

        probe(media, timeout=120)

        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("timeout") == 120 or call_kwargs[1].get("timeout") == 120

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_subprocess_command_structure(
        self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        media = tmp_path / "test.mp4"
        media.touch()
        mock_run.return_value = _mock_run(stdout=json.dumps(SAMPLE_EMPTY_JSON))

        probe(media)

        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "/usr/bin/ffprobe"
        assert "-print_format" in cmd
        assert "json" in cmd
        assert "-show_format" in cmd
        assert "-show_streams" in cmd
        assert str(media) in cmd

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_creation_time_transferred(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        """Format-level creation_time maps to ProbeResult.media_created_at."""
        payload = {
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
            "format": {
                "format_name": "mp4",
                "creation_time": "2023-08-20T14:00:00.000000Z",
            },
        }
        media = tmp_path / "dated.mp4"
        media.touch()
        mock_run.return_value = _mock_run(stdout=json.dumps(payload))

        r = probe(media)

        assert r.media_created_at == "2023-08-20T14:00:00.000000Z"


# ---------------------------------------------------------------------------
# probe – failure cases
# ---------------------------------------------------------------------------

class TestProbeFailure:
    def test_file_not_found(self) -> None:
        with pytest.raises(FFprobeError, match="not found"):
            probe(Path("/nonexistent/media.mkv"))

    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", side_effect=FFprobeError("no ffprobe"))
    def test_ffprobe_not_found(self, mock_resolve: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "show.mkv"
        media.touch()
        with pytest.raises(FFprobeError, match="no ffprobe"):
            probe(media)

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 30))
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_timeout(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "huge.mkv"
        media.touch()
        with pytest.raises(FFprobeError, match="timed out"):
            probe(media)

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_non_zero_exit(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "corrupt.mkv"
        media.touch()
        mock_run.return_value = _mock_run(
            returncode=1, stderr="Invalid data found"
        )
        with pytest.raises(FFprobeError, match="code 1"):
            probe(media)

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run")
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_invalid_json(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "weird.bin"
        media.touch()
        mock_run.return_value = _mock_run(stdout="not json at all {{{")
        with pytest.raises(FFprobeError, match="invalid JSON"):
            probe(media)

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run", side_effect=FileNotFoundError)
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_executable_vanished(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "show.mkv"
        media.touch()
        with pytest.raises(FFprobeError, match="disappeared"):
            probe(media)

    @patch("bootleg_catalog.utils.ffmpeg.subprocess.run", side_effect=OSError("permission denied"))
    @patch("bootleg_catalog.utils.ffmpeg.resolve_ffprobe", return_value="/usr/bin/ffprobe")
    def test_os_error(self, mock_resolve: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        media = tmp_path / "show.mkv"
        media.touch()
        with pytest.raises(FFprobeError, match="Failed to run"):
            probe(media)


# ---------------------------------------------------------------------------
# ProbeResult dataclass
# ---------------------------------------------------------------------------

class TestProbeResult:
    def test_defaults_are_none(self) -> None:
        r = ProbeResult()
        assert r.duration is None
        assert r.container_format is None
        assert r.video_codec is None
        assert r.audio_codec is None
        assert r.stream_count is None

    def test_construction(self) -> None:
        r = ProbeResult(duration=120.0, video_codec="h264", audio_codec="aac")
        assert r.duration == 120.0
        assert r.video_codec == "h264"
        assert r.audio_codec == "aac"
        assert r.video_width is None
