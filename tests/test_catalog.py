from __future__ import annotations

from bootleg_catalog.utils.paths import format_duration, format_file_size, same_folder_path


def test_path_helpers() -> None:
    assert format_file_size(None) == ""
    assert format_file_size(512) == "512 B"
    assert format_duration(None) == ""
    assert format_duration(125) == "2:05"
    assert same_folder_path("/media/concerts", "/media/concerts")
