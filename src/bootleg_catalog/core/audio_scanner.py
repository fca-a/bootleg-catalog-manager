"""Audio folder scanner — the album tracks behind one audio entry.

Deliberately simpler than :mod:`bootleg_catalog.core.rip_scanner`:
albums are FLAT, so this only lists audio files directly inside the
given folder — no recursion, no structure detection.  Pure filesystem:
no FFprobe, no Qt, no side effects beyond reading the directory.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Extensions treated as audio files, matched case-insensitively
#: (``entry.suffix.lower()``) against regular files.
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus",
    ".wav", ".wma", ".aiff", ".ape", ".alac",
}


class AudioScanError(Exception):
    """Raised when a folder cannot be scanned or holds no audio files."""


def _natural_key(name: str) -> tuple[object, ...]:
    """Case-insensitive natural sort key: digit runs compare numerically.

    ``"2 - x.mp3"`` sorts before ``"10 - y.mp3"`` (plain lexicographic
    would reverse them).  ``re.split`` on ``\\d+`` alternates non-digit
    and digit runs, so every key puts ``str`` at even indices and ``int``
    at odd ones — comparisons never meet two types at the same index.
    """
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", name)
    )


def scan_audio_folder(folder: str | Path) -> tuple[str, ...]:
    """Return sorted absolute paths of the audio files directly in *folder*.

    No recursion — albums are flat; subdirectories are ignored (only
    regular files whose suffix is in :data:`AUDIO_EXTENSIONS` count).
    Sorted naturally and case-insensitively by filename, with the
    original name as a tie-break for case-only differences.

    Raises
    ------
    AudioScanError
        If *folder* does not exist / is not a directory, or contains
        zero audio files.
    """
    root = Path(folder)
    if not root.is_dir():
        raise AudioScanError(f"Folder does not exist:\n{root}")
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise AudioScanError(f"Cannot read folder:\n{root}") from exc

    found: list[Path] = []
    for entry in entries:
        try:
            if entry.is_file() and entry.suffix.lower() in AUDIO_EXTENSIONS:
                found.append(entry)
        except OSError:
            continue
    if not found:
        raise AudioScanError(f"No audio files found in:\n{root}")

    found.sort(key=lambda p: (_natural_key(p.name), p.name))
    return tuple(str(p.resolve()) for p in found)
