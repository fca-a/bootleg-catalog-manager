"""Tests for the show grouper."""

from __future__ import annotations

import pytest

from bootleg_catalog.core.show_grouper import UNGROUPED, ShowGroup, group_by_show
from bootleg_catalog.models.export_recording import ExportRecording


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(path: str = "/test.mkv", show: str | None = None) -> ExportRecording:
    return ExportRecording(source_path=path, show=show)


# ---------------------------------------------------------------------------
# Empty list
# ---------------------------------------------------------------------------

class TestEmptyList:
    def test_empty_list(self) -> None:
        groups = group_by_show([])
        assert groups == []


# ---------------------------------------------------------------------------
# Single show
# ---------------------------------------------------------------------------

class TestSingleShow:
    def test_one_recording(self) -> None:
        groups = group_by_show([_rec("/a.mkv", show="Munich 1997")])
        assert len(groups) == 1
        assert groups[0].show_name == "Munich 1997"
        assert len(groups[0].recordings) == 1
        assert groups[0].recordings[0].source_path == "/a.mkv"

    def test_multiple_recordings_same_show(self) -> None:
        recs = [
            _rec("/a.mkv", show="Munich 1997"),
            _rec("/b.mkv", show="Munich 1997"),
            _rec("/c.mkv", show="Munich 1997"),
        ]
        groups = group_by_show(recs)
        assert len(groups) == 1
        assert groups[0].show_name == "Munich 1997"
        assert len(groups[0].recordings) == 3


# ---------------------------------------------------------------------------
# Multiple shows
# ---------------------------------------------------------------------------

class TestMultipleShows:
    def test_distinct_shows(self) -> None:
        recs = [
            _rec("/a.mkv", show="Berlin"),
            _rec("/b.mkv", show="London"),
            _rec("/c.mkv", show="Munich"),
        ]
        groups = group_by_show(recs)
        assert len(groups) == 3
        names = [g.show_name for g in groups]
        assert names == ["Berlin", "London", "Munich"]

    def test_alphabetical_order(self) -> None:
        recs = [
            _rec("/a.mkv", show="Tokyo"),
            _rec("/b.mkv", show="Berlin"),
            _rec("/c.mkv", show="London"),
            _rec("/d.mkv", show="Munich"),
        ]
        groups = group_by_show(recs)
        names = [g.show_name for g in groups]
        assert names == ["Berlin", "London", "Munich", "Tokyo"]


# ---------------------------------------------------------------------------
# Case-insensitive
# ---------------------------------------------------------------------------

class TestCaseInsensitive:
    def test_same_show_different_case(self) -> None:
        recs = [
            _rec("/a.mkv", show="Munich 1997"),
            _rec("/b.mkv", show="munich 1997"),
            _rec("/c.mkv", show="MUNICH 1997"),
        ]
        groups = group_by_show(recs)
        assert len(groups) == 1
        assert groups[0].show_name == "Munich 1997"  # first normalised form
        assert len(groups[0].recordings) == 3

    def test_ordering_case_insensitive(self) -> None:
        recs = [
            _rec("/a.mkv", show="berlin"),
            _rec("/b.mkv", show="Berlin"),
            _rec("/c.mkv", show="BERLIN"),
        ]
        groups = group_by_show(recs)
        assert len(groups) == 1
        assert groups[0].show_name == "berlin"  # first normalised form


# ---------------------------------------------------------------------------
# Whitespace normalisation
# ---------------------------------------------------------------------------

class TestWhitespaceNormalisation:
    def test_leading_trailing_spaces(self) -> None:
        recs = [
            _rec("/a.mkv", show="  Munich 1997  "),
        ]
        groups = group_by_show(recs)
        assert len(groups) == 1
        assert groups[0].show_name == "Munich 1997"

    def test_multiple_internal_spaces(self) -> None:
        recs = [
            _rec("/a.mkv", show="Munich    1997"),
        ]
        groups = group_by_show(recs)
        assert len(groups) == 1
        assert groups[0].show_name == "Munich 1997"

    def test_tabs_and_newlines(self) -> None:
        recs = [
            _rec("/a.mkv", show="Munich\t1997"),
        ]
        groups = group_by_show(recs)
        assert groups[0].show_name == "Munich 1997"

    def test_only_spaces_goes_to_ungrouped(self) -> None:
        recs = [_rec("/a.mkv", show="   ")]
        groups = group_by_show(recs)
        assert len(groups) == 1
        assert groups[0].show_name == UNGROUPED

    def test_normalised_match(self) -> None:
        """Two recordings with different whitespace should merge."""
        recs = [
            _rec("/a.mkv", show="  Munich  1997  "),
            _rec("/b.mkv", show="Munich 1997"),
        ]
        groups = group_by_show(recs)
        assert len(groups) == 1
        assert groups[0].show_name == "Munich 1997"  # internal spaces collapsed


# ---------------------------------------------------------------------------
# None / empty / whitespace → Ungrouped
# ---------------------------------------------------------------------------

class TestUngrouped:
    def test_none(self) -> None:
        groups = group_by_show([_rec("/a.mkv", show=None)])
        assert len(groups) == 1
        assert groups[0].show_name == UNGROUPED

    def test_empty_string(self) -> None:
        groups = group_by_show([_rec("/a.mkv", show="")])
        assert len(groups) == 1
        assert groups[0].show_name == UNGROUPED

    def test_whitespace_only(self) -> None:
        groups = group_by_show([_rec("/a.mkv", show="   ")])
        assert len(groups) == 1
        assert groups[0].show_name == UNGROUPED

    def test_ungrouped_always_last(self) -> None:
        recs = [
            _rec("/a.mkv", show="Ungrouped"),   # explicit "Ungrouped"
            _rec("/b.mkv", show=None),           # None → Ungrouped
            _rec("/c.mkv", show="Berlin"),
            _rec("/d.mkv", show=""),             # empty → Ungrouped
        ]
        groups = group_by_show(recs)
        names = [g.show_name for g in groups]
        assert names[-1] == UNGROUPED
        assert "Berlin" in names

    def test_ungrouped_last_even_with_many_shows(self) -> None:
        recs = [
            _rec("/a.mkv", show=None),
            _rec("/b.mkv", show="Zebra"),
            _rec("/c.mkv", show="Alpha"),
            _rec("/d.mkv", show=""),
        ]
        groups = group_by_show(recs)
        names = [g.show_name for g in groups]
        assert names == ["Alpha", "Zebra", UNGROUPED]


# ---------------------------------------------------------------------------
# Multiple recordings per group — insertion order
# ---------------------------------------------------------------------------

class TestInsertionOrder:
    def test_order_within_group(self) -> None:
        recs = [
            _rec("/a.mkv", show="Munich"),
            _rec("/b.mkv", show="Munich"),
            _rec("/c.mkv", show="Munich"),
        ]
        groups = group_by_show(recs)
        assert groups[0].recordings[0].source_path == "/a.mkv"
        assert groups[0].recordings[1].source_path == "/b.mkv"
        assert groups[0].recordings[2].source_path == "/c.mkv"

    def test_order_across_groups(self) -> None:
        """Groups are sorted, but recordings within preserve insertion order."""
        recs = [
            _rec("/a.mkv", show="Tokyo"),
            _rec("/b.mkv", show="Berlin"),
            _rec("/c.mkv", show="London"),
            _rec("/d.mkv", show="Berlin"),
        ]
        groups = group_by_show(recs)
        berlin = [g for g in groups if g.show_name == "Berlin"][0]
        tokyo = [g for g in groups if g.show_name == "Tokyo"][0]
        london = [g for g in groups if g.show_name == "London"][0]
        # Berlin recordings in insertion order
        assert berlin.recordings[0].source_path == "/b.mkv"
        assert berlin.recordings[1].source_path == "/d.mkv"
        # Tokyo and London each have one
        assert tokyo.recordings[0].source_path == "/a.mkv"
        assert london.recordings[0].source_path == "/c.mkv"


# ---------------------------------------------------------------------------
# Deterministic group name
# ---------------------------------------------------------------------------

class TestDeterministicName:
    def test_first_normalised_form_wins(self) -> None:
        recs = [
            _rec("/a.mkv", show="MUNICH 1997"),
            _rec("/b.mkv", show="Munich 1997"),
            _rec("/c.mkv", show="munich 1997"),
        ]
        groups = group_by_show(recs)
        assert groups[0].show_name == "MUNICH 1997"

    def test_insertion_determines_name(self) -> None:
        recs = [
            _rec("/a.mkv", show="  Munich  1997  "),
            _rec("/b.mkv", show="MUNICH 1997"),
        ]
        groups = group_by_show(recs)
        # "  Munich  1997  " normalises to "Munich 1997" (internal spaces collapsed)
        assert groups[0].show_name == "Munich 1997"


# ---------------------------------------------------------------------------
# Complex scenario
# ---------------------------------------------------------------------------

class TestComplexScenario:
    def test_mixed_shows_ordering_and_ungrouped(self) -> None:
        recs = [
            _rec("/a.mkv", show="Tokyo 1990"),
            _rec("/b.mkv", show=None),
            _rec("/c.mkv", show="Berlin 1982"),
            _rec("/d.mkv", show="tokyo 1990"),  # same as Tokyo 1990
            _rec("/e.mkv", show=""),
            _rec("/f.mkv", show="London 1995"),
            _rec("/g.mkv", show="   "),  # whitespace → Ungrouped
            _rec("/h.mkv", show="BERLIN 1982"),  # same as Berlin 1982
        ]
        groups = group_by_show(recs)
        names = [g.show_name for g in groups]

        # Alphabetical, Ungrouped last
        assert names == ["Berlin 1982", "London 1995", "Tokyo 1990", UNGROUPED]

        # Berlin group: insertion order
        berlin = [g for g in groups if g.show_name == "Berlin 1982"][0]
        assert len(berlin.recordings) == 2
        assert berlin.recordings[0].source_path == "/c.mkv"
        assert berlin.recordings[1].source_path == "/h.mkv"

        # Tokyo group: insertion order
        tokyo = [g for g in groups if g.show_name == "Tokyo 1990"][0]
        assert len(tokyo.recordings) == 2
        assert tokyo.recordings[0].source_path == "/a.mkv"
        assert tokyo.recordings[1].source_path == "/d.mkv"

        # Ungrouped: 3 recordings (None, "", "   ")
        ungrouped = [g for g in groups if g.show_name == UNGROUPED][0]
        assert len(ungrouped.recordings) == 3
        assert ungrouped.recordings[0].source_path == "/b.mkv"
        assert ungrouped.recordings[1].source_path == "/e.mkv"
        assert ungrouped.recordings[2].source_path == "/g.mkv"


# ---------------------------------------------------------------------------
# Immutability of original objects
# ---------------------------------------------------------------------------

class TestImmutability:
    def test_original_recordings_not_modified(self) -> None:
        recs = [
            _rec("/a.mkv", show="Munich"),
            _rec("/b.mkv", show="Berlin"),
        ]
        original_shows = [r.show for r in recs]
        original_paths = [r.source_path for r in recs]

        groups = group_by_show(recs)

        # Original objects unchanged
        assert [r.show for r in recs] == original_shows
        assert [r.source_path for r in recs] == original_paths

        # Group recordings are the same objects (shallow copy of list)
        assert groups[0].recordings[0] is recs[1]  # Berlin
        assert groups[1].recordings[0] is recs[0]  # Munich

    def test_list_input_not_mutated(self) -> None:
        recs = [
            _rec("/a.mkv", show="A"),
            _rec("/b.mkv", show="B"),
        ]
        original_len = len(recs)
        group_by_show(recs)
        assert len(recs) == original_len
