"""Layout model for visual export of screenshots.

Provides a simple data structure and grid-computation helper for
arranging screenshots in a grid (e.g. for future PDF/XLSX export).
The layout is intentionally decoupled from both FFmpeg and any
specific output format.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ScreenshotLayout:
    """Configuration for arranging screenshots in a grid.

    Attributes
    ----------
    photos_per_row:
        Number of images per row (must be >= 1).
    """

    photos_per_row: int = 3

    def __post_init__(self) -> None:
        if self.photos_per_row < 1:
            raise ValueError(f"photos_per_row must be >= 1, got {self.photos_per_row}")


@dataclass(slots=True)
class GridCell:
    """A single cell in the screenshot grid.

    Attributes
    ----------
    row:
        Zero-based row index.
    col:
        Zero-based column index.
    screenshot_path:
        Absolute path to the screenshot file.
    """

    row: int
    col: int
    screenshot_path: str


def compute_grid(
    screenshot_paths: list[str],
    layout: ScreenshotLayout,
) -> list[GridCell]:
    """Map a flat list of screenshot paths into a row×col grid.

    Parameters
    ----------
    screenshot_paths:
        Ordered list of screenshot file paths.
    layout:
        Grid configuration (photos per row).

    Returns
    -------
    list[GridCell]
        One cell per screenshot, with computed row/col positions.
    """
    cells: list[GridCell] = []
    for idx, path in enumerate(screenshot_paths):
        row, col = divmod(idx, layout.photos_per_row)
        cells.append(GridCell(row=row, col=col, screenshot_path=path))
    return cells


def row_count(screenshot_count: int, layout: ScreenshotLayout) -> int:
    """Return the number of rows needed to display *screenshot_count* images.

    The last row may be incomplete.
    """
    if screenshot_count <= 0:
        return 0
    return -(-screenshot_count // layout.photos_per_row)  # ceil division


def screenshots_for_recording(recording_path: str) -> list[str]:
    """Discover existing screenshot sidecar files for a recording.

    Returns ordered list of paths matching the naming convention::

        concert.screenshot.01.jpg
        concert.screenshot.02.jpg

    Only files with a numeric index (zero-padded) are recognised.
    Falls back to the single-screenshot convention if multi-frame
    files are not found.

    Parameters
    ----------
    recording_path:
        Absolute path to the source video file.

    Returns
    -------
    list[str]
        Existing screenshot paths, in order.  Empty list if none found.
    """
    import re

    p = Path(recording_path)
    parent = p.parent

    # Match pattern: stem.screenshot.NN.jpg where NN is digits only
    stem_escaped = re.escape(p.stem)
    pattern = re.compile(
        rf"^{stem_escaped}\.screenshot\.(\d+)\.jpg$",
        re.IGNORECASE,
    )

    matches: list[tuple[int, str]] = []
    for entry in parent.iterdir():
        if not entry.is_file():
            continue
        m = pattern.match(entry.name)
        if m:
            matches.append((int(m.group(1)), str(entry)))

    if matches:
        matches.sort(key=lambda x: x[0])
        return [path for _, path in matches]

    # Fall back to single-screenshot pattern
    single = parent / f"{p.stem}.screenshot.jpg"
    if single.is_file():
        return [str(single)]

    return []
