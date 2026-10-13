"""Resolved export document models.

Intermediate representation of a fully-resolved export document.
Produced by :class:`LayoutResolver` from a :class:`LayoutModel` and
a list of :class:`ShowGroup`.  Consumed identically by Preview, PDF,
and XLSX exporters.

ResolvedExportDocument
 ├── ResolvedShowSection (per show group)
 │    ├── header_lines: list[ResolvedLine]
 │    └── recordings: list[ResolvedRecording]
 │         └── lines: list[ResolvedLine]
 │              └── ResolvedElement
 └── separator: str
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bootleg_catalog.core.field_resolver import FieldResolutionError, ResolvedLine
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import LayoutModel


@dataclass
class ResolvedRecording:
    """A single recording with its body lines already resolved."""
    recording: ExportRecording
    lines: list[ResolvedLine]


@dataclass
class ResolvedShowSection:
    """A show section with header and resolved recordings."""
    show_name: str
    header_lines: list[ResolvedLine]
    recordings: list[ResolvedRecording]


@dataclass
class ResolvedExportDocument:
    """The complete resolved export document.

    This is the single shared representation consumed by Preview, PDF,
    and XLSX exporters — none of them need to re-interpret the
    :class:`LayoutModel`.

    ``errors`` collects every :class:`FieldResolutionError` produced
    during resolution (unknown fields, wrong ``{}``/``[]`` syntax, etc.)
    so that downstream consumers can surface diagnostics.
    """
    layout: LayoutModel
    show_sections: list[ResolvedShowSection]
    separator: str | None
    errors: list[FieldResolutionError] = field(default_factory=list)
