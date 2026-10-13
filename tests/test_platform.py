import sys
from pathlib import Path

from bootleg_catalog.utils.platform import platform_name


def test_platform_name_is_known() -> None:
    assert platform_name() in {"windows", "linux", "macos"}


def test_app_data_dir_on_all_platforms(tmp_path, monkeypatch) -> None:
    """app_data_dir() picks the right base directory per platform.

    app_data_dir() recomputes its result on every call (it does not
    cache), so each case only patches ``sys.platform`` plus the env
    vars that branch reads.  HOME and USERPROFILE both point at a tmp
    home so the ``mkdir()`` inside app_data_dir() can never create
    anything in the real profile, and the Linux app directory keeps its
    hyphenated name (``bootleg-catalog-manager``).
    """
    from bootleg_catalog.utils import paths

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    # Windows without LOCALAPPDATA falls back to the profile's AppData.
    monkeypatch.setattr(sys, "platform", "win32")
    win_fallback = paths.app_data_dir()
    assert isinstance(win_fallback, Path)
    assert win_fallback == home / "AppData" / "Local" / "BootlegCatalogManager"

    # Windows with LOCALAPPDATA sits under it.
    localappdata = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    win = paths.app_data_dir()
    assert win == localappdata / "BootlegCatalogManager"
    assert "BootlegCatalogManager" in win.parts

    # macOS uses ~/Library/Application Support.
    monkeypatch.setattr(sys, "platform", "darwin")
    mac = paths.app_data_dir()
    assert mac == home / "Library" / "Application Support" / "BootlegCatalogManager"
    assert home in mac.parents

    # Linux without XDG_DATA_HOME uses ~/.local/share.
    monkeypatch.setattr(sys, "platform", "linux")
    linux = paths.app_data_dir()
    assert linux == home / ".local" / "share" / "bootleg-catalog-manager"
    assert "bootleg-catalog-manager" in linux.parts

    # Linux with XDG_DATA_HOME uses it as the base.
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    linux_xdg = paths.app_data_dir()
    assert linux_xdg == xdg / "bootleg-catalog-manager"
