"""Layout resolver — produces a ResolvedExportDocument.

Orchestrates:

    LayoutModel + list[ShowGroup]
        → FieldResolver (element-level resolution)
        → ResolvedExportDocument

The resolver is stateless and does not modify any input objects.
It delegates element-level resolution entirely to the
:class:`FieldResolver`.
"""

from __future__ import annotations

from bootleg_catalog.core.field_resolver import FieldResolver
from bootleg_catalog.core.show_grouper import ShowGroup
from bootleg_catalog.models.layout_model import LayoutModel
from bootleg_catalog.models.resolved_export import (
    ResolvedExportDocument,
    ResolvedRecording,
    ResolvedShowSection,
)


class LayoutResolver:
    """Resolves a full export document from layout and show groups.

    Usage::

        resolver = LayoutResolver()
        document = resolver.resolve(layout, show_groups)
    """

    def __init__(self) -> None:
        self._field_resolver = FieldResolver()

    def resolve(
        self,
        layout: LayoutModel,
        groups: list[ShowGroup],
    ) -> ResolvedExportDocument:
        """Produce a fully-resolved export document.

        Parameters
        ----------
        layout:
            The layout defining show_header and recording_body lines.
        groups:
            Show groups as produced by :func:`ShowGrouper.group_by_show`.
            Groups must already be sorted (Ungrouped last).

        Returns
        -------
        ResolvedExportDocument
            A document where all field references have been replaced by
            concrete text values.  ``errors`` contains every
            :class:`FieldResolutionError` encountered during resolution.
        """
        sections: list[ResolvedShowSection] = []
        all_errors: list = []

        for group in groups:
            # Resolve show header using the first recording as context
            header_lines, header_errors = self._resolve_header(layout, group)
            all_errors.extend(header_errors)

            # Resolve recording bodies
            resolved_recordings: list[ResolvedRecording] = []
            for rec in group.recordings:
                body_lines, body_errors = self._resolve_body(layout, rec)
                all_errors.extend(body_errors)
                resolved_recordings.append(ResolvedRecording(
                    recording=rec,
                    lines=body_lines,
                ))

            sections.append(ResolvedShowSection(
                show_name=group.show_name,
                header_lines=header_lines,
                recordings=resolved_recordings,
            ))

        return ResolvedExportDocument(
            layout=layout,
            show_sections=sections,
            separator=layout.separator,
            errors=all_errors,
        )

    def _resolve_header(
        self,
        layout: LayoutModel,
        group: ShowGroup,
    ) -> tuple[list, list]:
        """Resolve show_header lines using the first recording as context.

        Returns ``(resolved_lines, errors)``.  Empty when there is no
        header or no recordings in the group.
        """
        if not layout.show_header or not group.recordings:
            return [], []
        context = group.recordings[0]
        return self._field_resolver.resolve_lines(
            layout.show_header, context,
        )

    def _resolve_body(
        self,
        layout: LayoutModel,
        recording: 'ExportRecording',
    ) -> tuple[list, list]:
        """Resolve recording_body lines for a single recording.

        Returns ``(resolved_lines, errors)``.
        """
        if not layout.recording_body:
            return [], []
        return self._field_resolver.resolve_lines(
            layout.recording_body, recording,
        )
