"""Field resolver for export layouts.

Resolves layout elements (fields and text) against an ExportRecording,
producing concrete text values.  Enforces the semantic distinction
between technical (``{}``) and archival (``[]``) fields:

- A ``FieldType.TECHNICAL`` element MUST reference a technical field.
- A ``FieldType.ARCHIVAL`` element MUST reference an archival or
  suggestion field.

Mismatched syntax (e.g. ``{Show}`` or ``[Duration]``) is treated as
**invalid** — the element resolves to an empty string and a
validation error is recorded.

Resolution is deterministic and produces no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass

from bootleg_catalog.core.field_registry import (
    FieldCategory,
    get_field_spec,
)
from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FieldType,
    LayoutElement,
    LayoutLine,
)


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------

@dataclass
class ResolvedElement:
    """A layout element with its field replaced by a concrete value.

    ``kind`` mirrors the source :class:`LayoutElement`'s kind:
    ``"text"`` (the default), ``"field"``, or ``"screenshot"``.
    Screenshot elements carry an empty ``text`` and put the digit-only
    count ``N`` in ``content`` — the resolver never loads image files.
    """
    text: str
    style: ElementStyle | None = None
    kind: str = "text"
    content: str = ""


@dataclass
class ResolvedLine:
    """A layout line with all fields resolved to concrete values."""
    elements: list[ResolvedElement]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class FieldResolutionError:
    """Records an invalid field reference detected during resolution."""
    field_name: str
    declared_type: FieldType
    actual_category: FieldCategory | None  # None if field not found
    message: str


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class FieldResolver:
    """Resolves layout elements against an ExportRecording.

    Usage::

        resolver = FieldResolver()
        resolved = resolver.resolve_line(layout_line, recording)
    """

    def resolve_element(
        self,
        element: LayoutElement,
        recording: ExportRecording,
    ) -> tuple[ResolvedElement, FieldResolutionError | None]:
        """Resolve a single element.

        Returns the resolved element and an optional error if the
        field reference is invalid.
        """
        if element.kind == "screenshot":
            # Inline [Screenshot:N] block: the count passes through
            # untouched.  No file is loaded and no registry lookup
            # happens, so it can never produce an error.
            return ResolvedElement(
                kind="screenshot",
                text="",
                content=element.content,
                style=None,
            ), None

        if element.kind == "text":
            return ResolvedElement(text=element.content, style=element.style), None

        # element.kind == "field"
        name = element.content
        declared = element.field_type

        # No declared type → treat as error (shouldn't happen in valid layouts)
        if declared is None:
            return ResolvedElement(text="", style=element.style), FieldResolutionError(
                field_name=name,
                declared_type=FieldType.TECHNICAL,  # placeholder
                actual_category=None,
                message=f"Field '{name}' has no field_type declaration.",
            )

        spec = get_field_spec(name)

        # Field not found in registry
        if spec is None:
            return ResolvedElement(text="", style=element.style), FieldResolutionError(
                field_name=name,
                declared_type=declared,
                actual_category=None,
                message=f"Field '{name}' is not registered in the field registry.",
            )

        # Validate category match
        if declared == FieldType.TECHNICAL and spec.category != FieldCategory.TECHNICAL:
            return ResolvedElement(text="", style=element.style), FieldResolutionError(
                field_name=name,
                declared_type=declared,
                actual_category=spec.category,
                message=(
                    f"Field '{name}' is declared as technical ({{}}) but is "
                    f"registered as {spec.category.value}.  Use [{name}] instead."
                ),
            )

        if declared == FieldType.ARCHIVAL and spec.category == FieldCategory.TECHNICAL:
            return ResolvedElement(text="", style=element.style), FieldResolutionError(
                field_name=name,
                declared_type=declared,
                actual_category=spec.category,
                message=(
                    f"Field '{name}' is declared as archival ([]) but is "
                    f"registered as technical.  Use {{{name}}} instead."
                ),
            )

        # Valid — resolve value
        raw = spec.resolver(recording)
        text = raw if raw is not None else ""
        return ResolvedElement(text=text, style=element.style), None

    def resolve_line(
        self,
        line: LayoutLine,
        recording: ExportRecording,
    ) -> tuple[ResolvedLine, list[FieldResolutionError]]:
        """Resolve all elements in a line.

        Returns the resolved line and a list of any validation errors.
        """
        errors: list[FieldResolutionError] = []
        resolved_elements: list[ResolvedElement] = []

        for elem in line.elements:
            resolved, err = self.resolve_element(elem, recording)
            resolved_elements.append(resolved)
            if err is not None:
                errors.append(err)

        return ResolvedLine(elements=resolved_elements), errors

    def resolve_lines(
        self,
        lines: list[LayoutLine],
        recording: ExportRecording,
    ) -> tuple[list[ResolvedLine], list[FieldResolutionError]]:
        """Resolve multiple lines (e.g. a recording_body section)."""
        all_errors: list[FieldResolutionError] = []
        resolved_lines: list[ResolvedLine] = []

        for line in lines:
            resolved, errors = self.resolve_line(line, recording)
            resolved_lines.append(resolved)
            all_errors.extend(errors)

        return resolved_lines, all_errors
