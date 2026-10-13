"""FFmpeg screenshot generation — one still beside the source video.

Pure module: a :class:`ScreenshotPlan` is plain data and the only side
effect is the explicitly called ``subprocess.run`` per timestamp, which
callers can patch.  Outputs follow the sidecar naming already
discovered by
:func:`bootleg_catalog.core.layout_service.screenshots_for_recording`::

    concert.mkv  ->  concert.screenshot.01.jpg
                     concert.screenshot.02.jpg

Public API
----------
- :class:`ScreenshotError`
- :class:`ScreenshotPlan`
- :func:`auto_timestamps`
- :func:`generate_screenshots`
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# One frame extraction gets this long before it is abandoned; the value
# mirrors the generous side of utils.ffmpeg's per-probe timeout (a
# seek into a large file on slow storage must not hang forever).
_FFMPEG_TIMEOUT_SECONDS = 300


class ScreenshotError(Exception):
    """Raised when screenshot generation cannot run or ffmpeg fails."""


@dataclass(frozen=True)
class ScreenshotPlan:
    """The timestamps, in seconds, at which to extract one frame each."""

    timestamps: tuple[float, ...]


def _output_path(video_path: Path, index: int) -> Path:
    """Sidecar path for the 1-based frame *index* beside *video_path*."""
    return video_path.with_name(f"{video_path.stem}.screenshot.{index:02d}.jpg")


def auto_timestamps(duration_seconds: float, count: int) -> tuple[float, ...]:
    """Distribute *count* timestamps across *duration_seconds*.

    The first and last 5% of the runtime are skipped so no frame lands
    in a fade-in or fade-out.  The points sit at the centres of
    *count* equal slices of the remaining 90% window, which makes them
    structurally strictly increasing: every point adds the same
    strictly positive step, so values can never tie.  Nothing is
    rounded — full precision is what preserves that guarantee, and
    ffmpeg parses any float for ``-ss``.

    Raises :class:`ScreenshotError` when *duration_seconds* is not
    positive, and ``ValueError`` when *count* is below 1.
    """
    if duration_seconds <= 0:
        raise ScreenshotError(
            f"Cannot plan screenshots for a duration of {duration_seconds}s"
        )
    if count < 1:
        raise ValueError("count must be at least 1")

    start = duration_seconds * 0.05
    step = (duration_seconds * 0.90) / count
    return tuple(start + (index + 0.5) * step for index in range(count))


def generate_screenshots(
    video_path: str | Path,
    plan: ScreenshotPlan,
    ffmpeg_path: str = "ffmpeg",
    *,
    overwrite: bool = False,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[str]:
    """Extract one JPEG per planned timestamp with ffmpeg.

    Outputs land next to *video_path* as ``<stem>.screenshot.NN.jpg``
    (1-based, zero-padded); the returned absolute paths are in
    timestamp order.  Exactly one subprocess call per timestamp.

    With ``overwrite=False`` (the default) an already-existing output
    raises :class:`ScreenshotError` BEFORE any ffmpeg process runs.
    A failing ffmpeg run raises :class:`ScreenshotError` carrying the
    last 200 characters of its stderr; a missing executable raises the
    same with the offending path.  *progress_cb*, when given, is
    called as ``(completed, total)`` after each successful extraction.
    """
    source = Path(video_path)
    outputs = [
        _output_path(source, index)
        for index in range(1, len(plan.timestamps) + 1)
    ]

    if not overwrite:
        # Checked up front so nothing is extracted and then refused.
        existing = [path for path in outputs if path.exists()]
        if existing:
            names = ", ".join(path.name for path in existing)
            raise ScreenshotError(
                f"Screenshot(s) already exist: {names}. "
                "Enable overwrite to replace them."
            )

    total = len(plan.timestamps)
    written: list[str] = []
    for index, (timestamp, output) in enumerate(zip(plan.timestamps, outputs)):
        cmd = [
            ffmpeg_path,
            "-ss", str(timestamp),
            "-i", str(source),
            "-frames:v", "1",
            "-q:v", "2",
            "-y",
            str(output),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=_FFMPEG_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            raise ScreenshotError(
                f"FFmpeg executable not found: {ffmpeg_path}"
            ) from None
        except subprocess.TimeoutExpired:
            raise ScreenshotError(
                f"FFmpeg timed out after {_FFMPEG_TIMEOUT_SECONDS}s "
                f"while extracting {timestamp}s"
            )
        except OSError as exc:
            raise ScreenshotError(f"Failed to run FFmpeg: {exc}") from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            tail = stderr[-200:]
            raise ScreenshotError(
                f"FFmpeg failed at {timestamp}s"
                + (f": {tail}" if tail else f" (exit {result.returncode})")
            )

        written.append(str(output.resolve()))
        if progress_cb is not None:
            progress_cb(index + 1, total)

    return written
