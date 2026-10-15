"""Tests for the field registry."""

from __future__ import annotations

import pytest

from bootleg_catalog.core.field_registry import (
    ALL_FIELDS,
    ARCHIVAL_FIELDS,
    SUGGESTION_FIELDS,
    TECHNICAL_FIELDS,
    FieldCategory,
    SuggestionFieldSpec,
    TechnicalFieldSpec,
    all_field_names,
    get_field_spec,
    is_archival,
    is_suggestion,
    is_technical,
)
from bootleg_catalog.models.export_recording import ExportRecording


# ---------------------------------------------------------------------------
# Technical fields
# ---------------------------------------------------------------------------

class TestTechnicalFields:
    """All 11 technical fields must be registered correctly."""

    EXPECTED = [
        "Extension", "Size", "Duration", "Container", "Video Codec",
        "Resolution", "Frame Rate", "Audio Codec", "Audio Channels",
        "Sample Rate", "Audio Bitrate",
    ]

    def test_count(self) -> None:
        assert len(TECHNICAL_FIELDS) == 11

    def test_all_expected_present(self) -> None:
        for name in self.EXPECTED:
            assert name in TECHNICAL_FIELDS, f"Missing technical field: {name}"

    def test_category(self) -> None:
        for name, spec in TECHNICAL_FIELDS.items():
            assert spec.category == FieldCategory.TECHNICAL, f"{name} has wrong category"

    def test_names_match_display_names(self) -> None:
        for name, spec in TECHNICAL_FIELDS.items():
            assert spec.name == name
            assert spec.display_name == name

    def test_resolvers_are_callable(self) -> None:
        rec = ExportRecording(source_path="/test.mkv")
        for name, spec in TECHNICAL_FIELDS.items():
            result = spec.resolver(rec)
            # None is acceptable (field is unset)
            assert result is None or isinstance(result, str), (
                f"{name}.resolver returned {type(result)}"
            )


class TestTechnicalFieldResolution:
    """Each technical field resolves correctly against an ExportRecording."""

    def _rec(self, **kwargs) -> ExportRecording:
        defaults = dict(
            source_path="/test.mkv",
            extension=".mkv",
            file_size=2_500_000_000,
            duration=7421.0,
            container_format="matroska",
            video_codec="h264",
            video_width=1920,
            video_height=1080,
            frame_rate=29.97,
            pixel_format="yuv420p",
            audio_codec="aac",
            audio_channels=2,
            audio_sample_rate=48000,
            audio_bitrate=192000,
        )
        defaults.update(kwargs)
        return ExportRecording(**defaults)

    def test_extension(self) -> None:
        rec = self._rec()
        assert TECHNICAL_FIELDS["Extension"].resolver(rec) == ".mkv"

    def test_size(self) -> None:
        rec = self._rec()
        result = TECHNICAL_FIELDS["Size"].resolver(rec)
        assert "GB" in result

    def test_duration(self) -> None:
        rec = self._rec()
        result = TECHNICAL_FIELDS["Duration"].resolver(rec)
        assert "2:03:41" == result

    def test_container(self) -> None:
        rec = self._rec()
        assert TECHNICAL_FIELDS["Container"].resolver(rec) == "matroska"

    def test_video_codec(self) -> None:
        rec = self._rec()
        assert TECHNICAL_FIELDS["Video Codec"].resolver(rec) == "h264"

    def test_resolution(self) -> None:
        rec = self._rec()
        result = TECHNICAL_FIELDS["Resolution"].resolver(rec)
        assert "1920" in result and "1080" in result

    def test_frame_rate(self) -> None:
        rec = self._rec()
        result = TECHNICAL_FIELDS["Frame Rate"].resolver(rec)
        assert "29.97" in result

    def test_audio_codec(self) -> None:
        rec = self._rec()
        assert TECHNICAL_FIELDS["Audio Codec"].resolver(rec) == "aac"

    def test_audio_channels(self) -> None:
        rec = self._rec()
        assert TECHNICAL_FIELDS["Audio Channels"].resolver(rec) == "2"

    def test_sample_rate(self) -> None:
        rec = self._rec()
        assert TECHNICAL_FIELDS["Sample Rate"].resolver(rec) == "48000 Hz"

    def test_audio_bitrate(self) -> None:
        rec = self._rec()
        result = TECHNICAL_FIELDS["Audio Bitrate"].resolver(rec)
        assert "kbps" in result

    def test_none_values_return_none(self) -> None:
        rec = ExportRecording(source_path="/x.mkv")
        for name, spec in TECHNICAL_FIELDS.items():
            result = spec.resolver(rec)
            assert result is None, f"{name} should be None for empty recording"


# ---------------------------------------------------------------------------
# Archival fields
# ---------------------------------------------------------------------------

class TestArchivalFields:
    """All 15 archival fields must be registered correctly."""

    EXPECTED = [
        "Title", "Year", "Date", "Venue", "Location", "Country",
        "Tour", "Show", "Generation", "Audio Source",
        "Video Source", "Master", "Notes", "Description",
        "Trade Status",
    ]

    def test_count(self) -> None:
        assert len(ARCHIVAL_FIELDS) == 15

    def test_all_expected_present(self) -> None:
        for name in self.EXPECTED:
            assert name in ARCHIVAL_FIELDS, f"Missing archival field: {name}"

    def test_category(self) -> None:
        for name, spec in ARCHIVAL_FIELDS.items():
            assert spec.category == FieldCategory.ARCHIVAL, f"{name} has wrong category"

    def test_names_match_display_names(self) -> None:
        for name, spec in ARCHIVAL_FIELDS.items():
            assert spec.name == name
            assert spec.display_name == name


class TestArchivalFieldResolution:
    """Each archival field resolves correctly against an ExportRecording."""

    def _rec(self, **kwargs) -> ExportRecording:
        defaults = dict(
            source_path="/test.mkv",
            title="Test Title",
            year=1997,
            date="1997-03-15",
            venue="Olympiahalle",
            location="Munich",
            country="Germany",
            tour="European Tour",
            show="Munich '97",
            source="Audience",
            type="Housecam",
            generation="1st gen",
            quality="Excellent",
            audio_source="Soundboard",
            video_source="Camera",
            master="Yes",
            notes="Test notes",
            description="Test description",
        )
        defaults.update(kwargs)
        return ExportRecording(**defaults)

    def test_title(self) -> None:
        assert ARCHIVAL_FIELDS["Title"].resolver(self._rec()) == "Test Title"

    def test_year(self) -> None:
        assert ARCHIVAL_FIELDS["Year"].resolver(self._rec()) == "1997"

    def test_date(self) -> None:
        assert ARCHIVAL_FIELDS["Date"].resolver(self._rec()) == "1997-03-15"

    def test_venue(self) -> None:
        assert ARCHIVAL_FIELDS["Venue"].resolver(self._rec()) == "Olympiahalle"

    def test_location(self) -> None:
        assert ARCHIVAL_FIELDS["Location"].resolver(self._rec()) == "Munich"

    def test_country(self) -> None:
        assert ARCHIVAL_FIELDS["Country"].resolver(self._rec()) == "Germany"

    def test_tour(self) -> None:
        assert ARCHIVAL_FIELDS["Tour"].resolver(self._rec()) == "European Tour"

    def test_show(self) -> None:
        assert ARCHIVAL_FIELDS["Show"].resolver(self._rec()) == "Munich '97"

    def test_generation(self) -> None:
        assert ARCHIVAL_FIELDS["Generation"].resolver(self._rec()) == "1st gen"

    def test_quality(self) -> None:
        assert ARCHIVAL_FIELDS.get("Quality") is None  # moved to SUGGESTION_FIELDS
        from bootleg_catalog.core.field_registry import SUGGESTION_FIELDS
        assert SUGGESTION_FIELDS["Quality"].resolver(self._rec()) == "Excellent"

    def test_audio_source(self) -> None:
        assert ARCHIVAL_FIELDS["Audio Source"].resolver(self._rec()) == "Soundboard"

    def test_video_source(self) -> None:
        assert ARCHIVAL_FIELDS["Video Source"].resolver(self._rec()) == "Camera"

    def test_master(self) -> None:
        assert ARCHIVAL_FIELDS["Master"].resolver(self._rec()) == "Yes"

    def test_notes(self) -> None:
        assert ARCHIVAL_FIELDS["Notes"].resolver(self._rec()) == "Test notes"

    def test_description(self) -> None:
        assert ARCHIVAL_FIELDS["Description"].resolver(self._rec()) == "Test description"

    def test_none_values_return_none(self) -> None:
        rec = ExportRecording(source_path="/x.mkv")
        for name, spec in ARCHIVAL_FIELDS.items():
            result = spec.resolver(rec)
            assert result is None, f"{name} should be None for empty recording"


class TestTradeStatusField:
    """Trade Status — archival field resolving the recording's value (L15.9.5)."""

    def test_registered_and_resolves(self) -> None:
        spec = get_field_spec("Trade Status")
        assert spec is not None
        assert spec.category == FieldCategory.ARCHIVAL
        assert is_archival("Trade Status")

        rec = ExportRecording(source_path="/x.mkv", trade_status="NFT")
        assert ARCHIVAL_FIELDS["Trade Status"].resolver(rec) == "NFT"

        # Unset resolves to the empty string through the field resolver,
        # like every other optional archival field.
        from bootleg_catalog.core.field_resolver import FieldResolver
        from bootleg_catalog.models.layout_model import FieldType, LayoutElement

        element = LayoutElement(
            kind="field", content="Trade Status",
            field_type=FieldType.ARCHIVAL,
        )
        empty = ExportRecording(source_path="/x.mkv")
        resolved, err = FieldResolver().resolve_element(element, rec)
        assert err is None
        assert resolved.text == "NFT"
        resolved, err = FieldResolver().resolve_element(element, empty)
        assert err is None
        assert resolved.text == ""


# ---------------------------------------------------------------------------
# Suggestion fields
# ---------------------------------------------------------------------------

class TestSuggestionFields:
    """Type, Source, and Quality must be suggestion fields."""

    def test_type_is_suggestion(self) -> None:
        assert "Type" in SUGGESTION_FIELDS
        spec = SUGGESTION_FIELDS["Type"]
        assert isinstance(spec, SuggestionFieldSpec)
        assert spec.suggestions_key == "type"

    def test_source_is_suggestion(self) -> None:
        assert "Source" in SUGGESTION_FIELDS
        spec = SUGGESTION_FIELDS["Source"]
        assert isinstance(spec, SuggestionFieldSpec)
        assert spec.suggestions_key == "source"

    def test_quality_is_suggestion(self) -> None:
        assert "Quality" in SUGGESTION_FIELDS
        spec = SUGGESTION_FIELDS["Quality"]
        assert isinstance(spec, SuggestionFieldSpec)
        assert spec.suggestions_key == "quality"

    def test_count(self) -> None:
        assert len(SUGGESTION_FIELDS) == 3

    def test_type_category(self) -> None:
        assert SUGGESTION_FIELDS["Type"].category == FieldCategory.SUGGESTION

    def test_source_category(self) -> None:
        assert SUGGESTION_FIELDS["Source"].category == FieldCategory.SUGGESTION

    def test_type_resolution(self) -> None:
        rec = ExportRecording(source_path="/x.mkv", type="Housecam")
        assert SUGGESTION_FIELDS["Type"].resolver(rec) == "Housecam"

    def test_source_resolution(self) -> None:
        rec = ExportRecording(source_path="/x.mkv", source="DVD")
        assert SUGGESTION_FIELDS["Source"].resolver(rec) == "DVD"

    def test_quality_resolution(self) -> None:
        rec = ExportRecording(source_path="/x.mkv", quality="Excellent")
        assert SUGGESTION_FIELDS["Quality"].resolver(rec) == "Excellent"


# ---------------------------------------------------------------------------
# Unified lookup
# ---------------------------------------------------------------------------

class TestUnifiedLookup:
    """get_field_spec and helpers work across all categories."""

    def test_total_field_count(self) -> None:
        # 11 technical + 15 archival + 3 suggestion = 29
        assert len(ALL_FIELDS) == 29

    def test_get_technical(self) -> None:
        spec = get_field_spec("Duration")
        assert spec is not None
        assert spec.category == FieldCategory.TECHNICAL

    def test_get_archival(self) -> None:
        spec = get_field_spec("Show")
        assert spec is not None
        assert spec.category == FieldCategory.ARCHIVAL

    def test_get_suggestion(self) -> None:
        spec = get_field_spec("Type")
        assert spec is not None
        assert spec.category == FieldCategory.SUGGESTION

    def test_get_unknown(self) -> None:
        assert get_field_spec("Nonexistent") is None

    def test_is_technical_true(self) -> None:
        assert is_technical("Duration") is True

    def test_is_technical_false_for_archival(self) -> None:
        assert is_technical("Show") is False

    def test_is_archival_true_for_archival(self) -> None:
        assert is_archival("Show") is True

    def test_is_archival_true_for_suggestion(self) -> None:
        assert is_archival("Type") is True

    def test_is_archival_false_for_technical(self) -> None:
        assert is_archival("Duration") is False

    def test_is_suggestion_true(self) -> None:
        assert is_suggestion("Type") is True
        assert is_suggestion("Source") is True
        assert is_suggestion("Quality") is True

    def test_is_suggestion_false_for_regular_archival(self) -> None:
        assert is_suggestion("Show") is False
        assert is_suggestion("Master") is False

    def test_is_archival_true_for_quality_suggestion(self) -> None:
        assert is_archival("Quality") is True  # suggestion fields are a subcategory of archival

    def test_all_field_names(self) -> None:
        names = all_field_names()
        assert len(names) == 29
        assert "Duration" in names
        assert "Show" in names
        assert "Type" in names
        assert "Source" in names
