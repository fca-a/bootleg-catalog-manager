from bootleg_catalog.utils.platform import platform_name


def test_platform_name_is_known() -> None:
    assert platform_name() in {"windows", "linux", "macos"}
