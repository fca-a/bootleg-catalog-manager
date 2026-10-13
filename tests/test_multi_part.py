"""Tests for the multi-part recording model (ExportRecording.parts and multi_part)."""

from __future__ import annotations

import pytest

from bootleg_catalog.core.multi_part import (
    PartTech,
    aggregate_parts,
    apply_resolutions,
    distribute_screenshot_counts,
)
from bootleg_catalog.models.export_recording import ExportRecording


def _make_rec(source_path: str, **overrides) -> ExportRecording:
    """Create a probed ExportRecording with sensible technical defaults."""
    defaults = dict(
        file_size=1000,
        duration=100.0,
        video_width=1920,
        video_height=1080,
        video_codec="h264",
        audio_codec="aac",
        frame_rate=29.97,
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate=192000,
    )
    defaults.update(overrides)
    return ExportRecording(source_path=source_path, **defaults)


# 1
def test_parts_empty_normalises_to_source_path() -> None:
    rec = ExportRecording(source_path="/videos/a.mkv")
    assert rec.parts == ("/videos/a.mkv",)
    assert rec.is_multi_part is False


# 2
def test_parts_mismatched_source_path_raises() -> None:
    with pytest.raises(ValueError):
        ExportRecording(source_path="/videos/a.mkv", parts=("/videos/b.mkv",))
    with pytest.raises(ValueError):
        ExportRecording(
            source_path="/videos/a.mkv",
            parts=("/videos/b.mkv", "/videos/c.mkv"),
        )
    # Matching two-part construction is valid
    rec = ExportRecording(
        source_path="/videos/a.mkv",
        parts=("/videos/a.mkv", "/videos/b.mkv"),
    )
    assert rec.is_multi_part is True


# 3
def test_parts_three_entries_raises() -> None:
    with pytest.raises(ValueError, match="At most two parts are supported"):
        ExportRecording(
            source_path="/videos/a.mkv",
            parts=("/videos/a.mkv", "/videos/b.mkv", "/videos/c.mkv"),
        )


# 4
def test_aggregate_single_part_identity() -> None:
    rec = _make_rec("/videos/part1.mkv")
    result = aggregate_parts([rec])

    assert result.duration == rec.duration
    assert result.file_size == rec.file_size
    assert result.video_width == rec.video_width
    assert result.video_height == rec.video_height
    assert result.video_codec == rec.video_codec
    assert result.audio_codec == rec.audio_codec
    assert result.frame_rate == rec.frame_rate
    assert result.audio_channels == rec.audio_channels
    assert result.audio_sample_rate == rec.audio_sample_rate
    assert result.audio_bitrate == rec.audio_bitrate
    assert result.divergent == frozenset()
    assert result.parts == (
        PartTech(
            path=rec.source_path,
            duration=rec.duration,
            file_size=rec.file_size,
            video_width=rec.video_width,
            video_height=rec.video_height,
            video_codec=rec.video_codec,
            audio_codec=rec.audio_codec,
            frame_rate=rec.frame_rate,
            audio_channels=rec.audio_channels,
            audio_sample_rate=rec.audio_sample_rate,
            audio_bitrate=rec.audio_bitrate,
        ),
    )


# 5
def test_aggregate_two_identical_parts_sums_duration_and_size() -> None:
    r1 = _make_rec("/videos/part1.mkv", duration=100.0, file_size=1000)
    r2 = _make_rec("/videos/part2.mkv", duration=250.5, file_size=2000)

    result = aggregate_parts([r1, r2])

    assert result.duration == 350.5
    assert result.file_size == 3000
    assert result.divergent == frozenset()
    assert result.video_width == 1920
    assert result.video_height == 1080
    assert result.video_codec == "h264"
    # First part's values, no divergence check
    assert result.frame_rate == r1.frame_rate
    assert result.audio_channels == r1.audio_channels
    assert len(result.parts) == 2
    assert result.parts[0].path == r1.source_path
    assert result.parts[1].path == r2.source_path


# 6
def test_aggregate_different_resolution_flags_divergence() -> None:
    r1 = _make_rec("/videos/part1.mkv", video_width=1920, video_height=1080)
    r2 = _make_rec("/videos/part2.mkv", video_width=1280, video_height=720)

    result = aggregate_parts([r1, r2])

    assert result.video_width is None
    assert result.video_height is None
    assert "resolution" in result.divergent
    # Non-resolution fields still resolve
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"


# 7
def test_aggregate_different_video_codec_flags_divergence() -> None:
    r1 = _make_rec("/videos/part1.mkv", video_codec="h264")
    r2 = _make_rec("/videos/part2.mkv", video_codec="hevc")

    result = aggregate_parts([r1, r2])

    assert "video_codec" in result.divergent
    assert result.video_codec is None
    # Resolution still resolved
    assert result.video_width == 1920
    assert result.video_height == 1080
    assert "resolution" not in result.divergent


# 8
def test_distribute_screenshot_counts() -> None:
    # 1 part → (n,)
    assert distribute_screenshot_counts(7, 1) == (7,)
    # 2 parts, even → equal split
    assert distribute_screenshot_counts(8, 2) == (4, 4)
    # 2 parts, odd → first gets the extra
    assert distribute_screenshot_counts(9, 2) == (5, 4)
    # 2 parts, zero
    assert distribute_screenshot_counts(0, 2) == (0, 0)


def _divergent_two_part_probe():
    """A two-part aggregate whose resolution diverges (widths differ)."""
    r1 = _make_rec("/videos/part1.mkv", video_width=1920, video_height=1080)
    r2 = _make_rec("/videos/part2.mkv", video_width=1280, video_height=720)
    return aggregate_parts([r1, r2])


# 12
def test_apply_resolutions_empty_dict_returns_aggregate() -> None:
    aggregate = _divergent_two_part_probe()

    result = apply_resolutions(aggregate, {})

    assert result == aggregate
    assert result.video_width is None  # divergence untouched
    assert result.divergent == aggregate.divergent
    assert result.parts == aggregate.parts


# 13
def test_apply_resolutions_resolution_picks_part_b() -> None:
    aggregate = _divergent_two_part_probe()
    assert "resolution" in aggregate.divergent

    result = apply_resolutions(aggregate, {"resolution": 1})

    assert result.video_width == 1280
    assert result.video_height == 720
    assert "resolution" not in result.divergent
    assert result.divergent == frozenset()
    # Non-divergent fields and the parts tuple are untouched.
    assert result.video_codec == aggregate.video_codec
    assert result.audio_codec == aggregate.audio_codec
    assert result.duration == aggregate.duration
    assert result.parts == aggregate.parts


# 14
def test_apply_resolutions_unknown_key_raises() -> None:
    aggregate = _divergent_two_part_probe()

    with pytest.raises(ValueError):
        apply_resolutions(aggregate, {"pixel_format": 0})


# 15
def test_screenshot_part_index_single_part_all_zero() -> None:
    rec = _make_rec(
        "/videos/a.mkv", screenshot_paths=["x.jpg", "y.jpg", "z.jpg"],
    )
    assert rec.screenshot_part_index(2) == (0, 0, 0)


# 16
def test_screenshot_part_index_uses_explicit_split() -> None:
    rec = ExportRecording(
        source_path="/videos/a.mkv",
        parts=("/videos/a.mkv", "/videos/b.mkv"),
        screenshot_paths=["1.jpg", "2.jpg", "3.jpg"],
        screenshot_split=1,
    )
    assert rec.screenshot_part_index(99) == (0, 1, 1)


# 17
def test_screenshot_part_index_falls_back_to_default() -> None:
    rec = ExportRecording(
        source_path="/videos/a.mkv",
        parts=("/videos/a.mkv", "/videos/b.mkv"),
        screenshot_paths=["1.jpg", "2.jpg", "3.jpg", "4.jpg"],
    )
    assert rec.screenshot_part_index(2) == (0, 0, 1, 1)


# 18
def test_screenshot_split_invariants() -> None:
    # Single part: when set, the split must equal the path count.
    with pytest.raises(ValueError, match="screenshot_split"):
        ExportRecording(
            source_path="/videos/a.mkv",
            screenshot_paths=["1.jpg", "2.jpg"],
            screenshot_split=1,
        )
    # Two parts: 0 <= split <= len(screenshot_paths).
    with pytest.raises(ValueError, match="screenshot_split"):
        ExportRecording(
            source_path="/videos/a.mkv",
            parts=("/videos/a.mkv", "/videos/b.mkv"),
            screenshot_paths=["1.jpg", "2.jpg"],
            screenshot_split=3,
        )
    # Valid values and the None default never raise.
    ExportRecording(
        source_path="/videos/a.mkv",
        screenshot_paths=["1.jpg", "2.jpg"],
        screenshot_split=2,
    )
    ExportRecording(
        source_path="/videos/a.mkv",
        parts=("/videos/a.mkv", "/videos/b.mkv"),
        screenshot_paths=["1.jpg", "2.jpg"],
        screenshot_split=0,
    )
    ExportRecording(source_path="/videos/a.mkv", screenshot_paths=["1.jpg"])
