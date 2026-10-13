"""Rip scanner — detect physical-media rip folders and enumerate streams.

Pure module: no Qt, no catalog layer, no repository, no SQLite.

Detects two physical-media rip layouts:

- DVD: a ``VIDEO_TS/`` directory containing ``.vob`` streams.
- Blu-ray: a ``BDMV/`` directory containing ``.m2ts`` streams under
  ``BDMV/STREAM/``.

:func:`detect_rip` is a total predicate — it returns ``None`` instead of
raising for anything that is not a rip and never enforces a size limit.
:func:`scan_rip_folder` is the strict variant for callers that must have
a rip, adding existence and the folder-size guard.

``.iso`` files are single files, not folders: they go through the normal
single-recording add flow and are intentionally out of scope here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from bootleg_catalog.utils.paths import format_file_size

FOLDER_SCAN_LIMIT_BYTES = 100 * 1024**3  # 100 GiB


class RipScanError(Exception):
    """Raised when a folder cannot be scanned or exceeds the size guard."""


@dataclass
class RipScanResult:
    """A detected physical-media rip."""

    folder_path: str                 # absolute path of the top folder the user picked
    rip_kind: str                    # "dvd" | "bluray"
    video_files: list[str]           # absolute paths of the video streams, sorted
    total_size_bytes: int            # sum of every file under the rip, not just videos


def detect_rip(folder_path: str | Path) -> RipScanResult | None:
    """Return a RipScanResult if *folder_path* looks like a rip, else None.

    Detection rules (in order):

    1. The folder itself contains a VIDEO_TS/ subdirectory with at least
       one .vob file -> rip_kind == "dvd", video_files = sorted .vob paths.
    2. The folder itself contains a BDMV/ subdirectory with at least one
       .m2ts file under BDMV/STREAM/ -> rip_kind == "bluray",
       video_files = sorted .m2ts paths.
    3. The folder IS a VIDEO_TS/ (its name equals "VIDEO_TS") with .vob
       files -> rip_kind == "dvd".
    4. The folder IS a BDMV/ (its name equals "BDMV") -> rip_kind ==
       "bluray" (look in STREAM/ for .m2ts files).

    Returns None for anything else. Never raises on the "not a rip" case.
    The size guard is not enforced here — see :func:`scan_rip_folder`.
    """
    try:
        folder = Path(folder_path).absolute()
        if not folder.is_dir():
            return None

        vobs = _video_files(folder / "VIDEO_TS", ".vob")
        if vobs:
            return RipScanResult(str(folder), "dvd", vobs, _total_size(folder))

        m2ts = _video_files(folder / "BDMV" / "STREAM", ".m2ts")
        if m2ts:
            return RipScanResult(str(folder), "bluray", m2ts, _total_size(folder))

        if folder.name == "VIDEO_TS":
            own_vobs = _video_files(folder, ".vob")
            if own_vobs:
                return RipScanResult(str(folder), "dvd", own_vobs, _total_size(folder))

        if folder.name == "BDMV":
            own_m2ts = _video_files(folder / "STREAM", ".m2ts")
            if own_m2ts:
                return RipScanResult(str(folder), "bluray", own_m2ts, _total_size(folder))

        return None
    except OSError:
        return None


def scan_rip_folder(
    folder_path: str | Path,
    *,
    size_limit: int = FOLDER_SCAN_LIMIT_BYTES,
) -> RipScanResult:
    """Like detect_rip, but raises when the folder is not a rip or is too big.

    Raises
    ------
    RipScanError
        - Folder does not exist or is not a directory.
        - Folder is not a rip (detect_rip returned None).
        - Rip has zero video files.
        - total_size_bytes exceeds *size_limit*.
    """
    folder = Path(folder_path).absolute()

    if not folder.exists():
        raise RipScanError(f"The folder does not exist:\n{folder}")
    if not folder.is_dir():
        raise RipScanError(f"Not a folder:\n{folder}")

    result = detect_rip(folder)
    if result is None:
        if _has_rip_structure(folder):
            raise RipScanError(
                f"The rip contains no video files (.vob/.m2ts):\n{folder}"
            )
        raise RipScanError(
            f"Not a rip folder — no VIDEO_TS/ or BDMV/ structure was found:\n{folder}"
        )

    if result.total_size_bytes > size_limit:
        raise RipScanError(
            f"This rip is {format_file_size(result.total_size_bytes)}, "
            f"which exceeds the {format_file_size(size_limit)} limit for folder scans.\n"
            "Add individual recordings instead of the whole folder."
        )

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _video_files(directory: Path, suffix: str) -> list[str]:
    """Return sorted absolute paths of regular *suffix* files in *directory*.

    Matching is case-insensitive (rips commonly use ``.VOB``/``.M2TS``).
    An unreadable or missing directory yields ``[]``; unreadable entries
    are skipped.  Sorting is plain lexicographic — VOB numbering is
    zero-padded, so this is the natural playback order.
    """
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    found: list[str] = []
    for entry in entries:
        try:
            if entry.is_file() and entry.suffix.lower() == suffix:
                found.append(str(entry))
        except OSError:
            continue
    return sorted(found)


def _total_size(root: Path) -> int:
    """Recursive sum of every file under *root* (approximate).

    Symlinked directories are never followed (``followlinks=False`` plus
    an explicit ``dirnames[:]`` prune).  Missing or unreadable files are
    skipped silently — the size is an approximation for the guard, not
    an audit figure.
    """
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if not Path(dirpath, d).is_symlink()
        ]
        for name in filenames:
            try:
                total += Path(dirpath, name).stat().st_size
            except OSError:
                continue
    return total


def _has_rip_structure(folder: Path) -> bool:
    """True when the folder is rip-shaped even though no streams were found.

    Distinguishes "a DVD rip with no .vob yet" (VIDEO_TS/ holding only
    .ifo/.bup) from "not a rip at all", so callers can show the right
    error.
    """
    return (
        (folder / "VIDEO_TS").is_dir()
        or (folder / "BDMV").is_dir()
        or folder.name == "VIDEO_TS"
        or folder.name == "BDMV"
    )
