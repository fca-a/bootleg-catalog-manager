from __future__ import annotations

import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / "BootlegCatalogManager"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "BootlegCatalogManager"
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        path = Path(base) / "bootleg-catalog-manager"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def same_folder_path(left: str, right: str) -> bool:
    """Compare two folder paths without rewriting stored values."""
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def format_file_size(size: int | None) -> str:
    if size is None:
        return ""
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
