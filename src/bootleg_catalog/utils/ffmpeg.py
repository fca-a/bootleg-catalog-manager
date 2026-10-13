"""FFprobe subprocess wrapper for extracting media technical metadata."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFprobeError(Exception):
    """Raised when FFprobe is unavailable or fails."""


class FFmpegError(Exception):
    """Raised when FFmpeg is unavailable or fails."""


@dataclass(slots=True)
class ProbeResult:
    """Technical metadata extracted by FFprobe.

    ``None`` means the value was not available in the probe output.
    """

    duration: float | None = None
    container_format: str | None = None
    video_codec: str | None = None
    video_width: int | None = None
    video_height: int | None = None
    frame_rate: float | None = None
    pixel_format: str | None = None
    video_bitrate: int | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    audio_sample_rate: int | None = None
    audio_bitrate: int | None = None
    stream_count: int | None = None
    media_created_at: str | None = None


# ---------------------------------------------------------------------------
# Executable resolution
# ---------------------------------------------------------------------------

def resolve_ffprobe(path: str | Path | None = None) -> str:
    """Return the path to the FFprobe executable.

    Resolution order:

    1. Explicit *path* argument.
    2. ``FFPROBE_PATH`` environment variable.
    3. ``shutil.which("ffprobe")`` on the system PATH.

    Raises :class:`FFprobeError` when the executable cannot be found.
    """
    if path is not None:
        candidate = str(path)
        if Path(candidate).is_file():
            return candidate
        raise FFprobeError(f"FFprobe executable not found: {candidate}")

    env_path = os.environ.get("FFPROBE_PATH")
    if env_path:
        candidate = env_path
        if Path(candidate).is_file():
            return candidate
        raise FFprobeError(
            f"FFprobe executable not found (FFPROBE_PATH): {candidate}"
        )

    found = shutil.which("ffprobe")
    if found is not None:
        return found

    raise FFprobeError(
        "FFprobe was not found. Install FFmpeg and ensure 'ffprobe' is on "
        "the system PATH, or set the FFPROBE_PATH environment variable."
    )


def resolve_ffmpeg(path: str | Path | None = None) -> str:
    """Return the path to the FFmpeg executable.

    Resolution order:

    1. Explicit *path* argument.
    2. ``FFMPEG_PATH`` environment variable.
    3. ``shutil.which("ffmpeg")`` on the system PATH.

    Raises :class:`FFmpegError` when the executable cannot be found.
    """
    if path is not None:
        candidate = str(path)
        if Path(candidate).is_file():
            return candidate
        raise FFmpegError(f"FFmpeg executable not found: {candidate}")

    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        candidate = env_path
        if Path(candidate).is_file():
            return candidate
        raise FFmpegError(
            f"FFmpeg executable not found (FFMPEG_PATH): {candidate}"
        )

    found = shutil.which("ffmpeg")
    if found is not None:
        return found

    raise FFmpegError(
        "FFmpeg was not found. Install FFmpeg and ensure 'ffmpeg' is on "
        "the system PATH, or set the FFMPEG_PATH environment variable."
    )


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_frame_rate(value: str | None) -> float | None:
    """Parse an FFprobe frame-rate string such as ``'30000/1001'``."""
    if not value:
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            return float(num) / float(den)
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


def _text(value: str | None) -> str | None:
    """Return *value* as-is, or ``None`` for empty / blank strings."""
    if not value:
        return None
    return value


def _extract(data: dict) -> ProbeResult:
    """Build a :class:`ProbeResult` from parsed FFprobe JSON."""
    streams: list[dict] = data.get("streams", [])
    fmt: dict = data.get("format", {})

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    video = video_streams[0] if video_streams else {}
    audio = audio_streams[0] if audio_streams else {}

    return ProbeResult(
        duration=_safe_float(fmt.get("duration")),
        container_format=_text(fmt.get("format_name")),
        video_codec=_text(video.get("codec_name")),
        video_width=_safe_int(video.get("width")),
        video_height=_safe_int(video.get("height")),
        frame_rate=_parse_frame_rate(video.get("r_frame_rate")),
        pixel_format=_text(video.get("pix_fmt")),
        video_bitrate=_safe_int(video.get("bit_rate")),
        audio_codec=_text(audio.get("codec_name")),
        audio_channels=_safe_int(audio.get("channels")),
        audio_sample_rate=_safe_int(audio.get("sample_rate")),
        audio_bitrate=_safe_int(audio.get("bit_rate")),
        stream_count=len(streams) or None,
        media_created_at=_text(fmt.get("creation_time")),
    )


# ---------------------------------------------------------------------------
# Public probe API
# ---------------------------------------------------------------------------

def probe(
    file_path: str | Path,
    *,
    ffprobe_path: str | Path | None = None,
    timeout: int = 30,
) -> ProbeResult:
    """Probe *file_path* with FFprobe and return technical metadata.

    Parameters
    ----------
    file_path:
        Media file to probe.
    ffprobe_path:
        Optional explicit path to the ``ffprobe`` executable.
    timeout:
        Subprocess timeout in seconds (default 30).

    Returns
    -------
    ProbeResult
        Parsed technical metadata.  Missing values are ``None``.

    Raises
    ------
    FFprobeError
        If the file does not exist, FFprobe cannot be found, the
        subprocess times out, returns a non-zero exit code, or
        produces unparseable output.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FFprobeError(f"File not found: {path}")

    resolved = resolve_ffprobe(ffprobe_path)

    cmd = [
        resolved,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except FileNotFoundError:
        raise FFprobeError(
            f"FFprobe executable disappeared: {resolved}"
        )
    except subprocess.TimeoutExpired:
        raise FFprobeError(
            f"FFprobe timed out after {timeout}s while probing {path.name}"
        )
    except OSError as exc:
        raise FFprobeError(f"Failed to run FFprobe: {exc}") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise FFprobeError(
            f"FFprobe exited with code {result.returncode}"
            + (f": {stderr}" if stderr else "")
        )

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise FFprobeError(
            f"FFprobe produced invalid JSON for {path.name}"
        ) from exc

    return _extract(data)
