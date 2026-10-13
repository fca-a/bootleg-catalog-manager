"""Named export-layout persistence (one JSON file per layout).

Layouts are stored inside the ``layouts`` directory of the application
data directory (or a caller-supplied ``base_dir``).  The payload is the
LayoutModel's own JSON serialisation — no UI toolkit, no database, no
repository dependency.  The directory is created lazily on the first
save.

File name vs. layout name
-------------------------
The file name is ``<sanitised name>.json``; the ``LayoutModel.name``
stored *inside* the JSON is preserved exactly as given.  So a layout
named ``"My/Layout"`` lives in ``My_Layout.json`` yet round-trips back
with ``name == "My/Layout"``, and :meth:`LayoutManager.list_layouts`
returns those original names (sorted), never the file stems.

If two layout names sanitise to the same file name, the second
:meth:`LayoutManager.save` overwrites the first — collisions resolve to
a single file.

Logo images
-----------
A header/footer slot may hold a ``LogoSlot`` whose ``logo_file`` is a
relative filename stored next to the JSON as
``<sanitised>.logo.<slot_id>.<ext>`` (slot ids: ``header.left``,
``header.center``, ``header.right``, ``footer.left``, ``footer.center``,
``footer.right``; the dots become underscores).  :meth:`LayoutManager.save`
copies freshly chosen images into that naming scheme via its
``pending_logos`` keyword, and :meth:`LayoutManager.delete` removes the
sibling images along with the JSON.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from bootleg_catalog.models.layout_model import (
    ElementStyle,
    FieldType,
    FooterConfig,
    HeaderConfig,
    LayoutElement,
    LayoutLine,
    LayoutModel,
    LogoSlot,
    TextSlot,
)
from bootleg_catalog.utils.paths import app_data_dir


# Characters that are unsafe in file names on Windows, macOS, or Linux.
_FORBIDDEN_CHARS = frozenset('\\/:*?"<>|')

# Windows-prohibited device names, matched case-insensitively against the
# FIRST dot-delimited segment of the sanitised stem (so "CON" and
# "CON.bootleg" are rejected while "CONsole" and "MY CON" are fine).
# COM0 and LPT0 are deliberately NOT reserved.
_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class LayoutManagerError(Exception):
    """Raised when a layout operation fails."""


# ---------------------------------------------------------------------------
# Name sanitisation
# ---------------------------------------------------------------------------

def _has_content(name: str) -> bool:
    """True when *name* holds at least one meaningful character.

    A name consisting only of forbidden characters, whitespace, or dots
    (e.g. ``"///"`` or ``"   "``) conveys nothing usable.
    """
    return any(
        c not in _FORBIDDEN_CHARS and not c.isspace() and c != "."
        for c in name
    )


def _sanitize_name(name: str) -> str:
    """Return the file-name stem for *name*.

    Rules, in order:

    1. Every character in ``\\/:*?"<>|`` becomes ``_``.
    2. Consecutive whitespace collapses to a single space (edges too).
    3. Leading/trailing whitespace and dots are stripped.
    4. Case is preserved — names are never lower-cased.
    5. A stem whose first dot-delimited segment is a Windows reserved
       device name (CON, PRN, AUX, NUL, COM1-9, LPT1-9 — any case) is
       rejected; ``COM0``/``LPT0`` are allowed.

    Raises :class:`LayoutManagerError` (``Layout name is empty or
    invalid.``) when nothing meaningful remains — empty, whitespace-only,
    or all-forbidden names such as ``"///"`` — or when the stem names a
    reserved device.
    """
    replaced = "".join("_" if c in _FORBIDDEN_CHARS else c for c in name)
    collapsed = " ".join(replaced.split())   # collapses runs, trims edges
    stripped = collapsed.strip(" .")         # strips edge dots and spaces
    if not stripped or not _has_content(name):
        raise LayoutManagerError("Layout name is empty or invalid.")
    if stripped.split(".", 1)[0].upper() in _RESERVED_DEVICE_NAMES:
        raise LayoutManagerError("Layout name is empty or invalid.")
    return stripped


def _remove_quietly(path: Path) -> None:
    """Best-effort temp-file removal; failures are ignored."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Logo slots
# ---------------------------------------------------------------------------

def _find_slot(layout: LayoutModel, slot_id: str) -> TextSlot | LogoSlot | None:
    """Return the slot object a ``pending_logos`` id addresses.

    Valid ids are ``"header.left"``, ``"header.center"``,
    ``"header.right"``, ``"footer.left"``, ``"footer.center"``, and
    ``"footer.right"``; anything unknown — or addressing a ``None``
    config — yields ``None``.
    """
    prefix, _, side = slot_id.partition(".")
    if prefix == "header":
        config = layout.header
    elif prefix == "footer":
        config = layout.footer
    else:
        return None
    if config is None or side not in ("left", "center", "right"):
        return None
    return getattr(config, side)


def _retarget_config(
    config: HeaderConfig | FooterConfig | None,
    prefix: str,
    logo_files: dict[str, str],
) -> HeaderConfig | FooterConfig | None:
    """Return *config* with its pending slots pointed at the new files.

    Each pending slot's :class:`LogoSlot` is rebuilt with the copied
    relative filename; slots NOT in *logo_files* keep their existing
    ``logo_file`` untouched (stale references are preserved, never
    deleted).  ``None`` stays ``None``.  The input is not mutated.
    """
    if config is None:
        return None
    updates: dict[str, LogoSlot] = {}
    for side in ("left", "center", "right"):
        slot = getattr(config, side)
        new_name = logo_files.get(f"{prefix}.{side}")
        if new_name is not None and isinstance(slot, LogoSlot):
            updates[side] = LogoSlot(
                width_mm=slot.width_mm, logo_file=new_name
            )
    if not updates:
        return config
    return replace(config, **updates)


# ---------------------------------------------------------------------------
# Default layout
# ---------------------------------------------------------------------------

def default_layout() -> LayoutModel:
    """Build the built-in ``"Default"`` export layout.

    Archival fields use ``[...]`` semantics (``FieldType.ARCHIVAL``),
    technical fields use ``{...}`` semantics (``FieldType.TECHNICAL``).
    Page and screenshot settings stay at their dataclass defaults.
    """
    return LayoutModel(
        name="Default",
        show_header=[
            LayoutLine(elements=[
                LayoutElement(
                    kind="field",
                    content="Show",
                    field_type=FieldType.ARCHIVAL,
                    style=ElementStyle(font_size=16, bold=True),
                ),
            ]),
            LayoutLine(elements=[
                LayoutElement(
                    kind="field",
                    content="Location",
                    field_type=FieldType.ARCHIVAL,
                ),
                LayoutElement(kind="text", content=", "),
                LayoutElement(
                    kind="field",
                    content="Country",
                    field_type=FieldType.ARCHIVAL,
                ),
            ]),
            LayoutLine(elements=[
                LayoutElement(
                    kind="field",
                    content="Venue",
                    field_type=FieldType.ARCHIVAL,
                ),
            ]),
            LayoutLine(elements=[
                LayoutElement(
                    kind="field",
                    content="Date",
                    field_type=FieldType.ARCHIVAL,
                ),
            ]),
        ],
        recording_body=[
            LayoutLine(elements=[
                LayoutElement(kind="text", content="Format: "),
                LayoutElement(
                    kind="field",
                    content="Extension",
                    field_type=FieldType.TECHNICAL,
                ),
                LayoutElement(kind="text", content=" | Duration: "),
                LayoutElement(
                    kind="field",
                    content="Duration",
                    field_type=FieldType.TECHNICAL,
                ),
            ]),
            LayoutLine(elements=[
                LayoutElement(kind="text", content="Resolution: "),
                LayoutElement(
                    kind="field",
                    content="Resolution",
                    field_type=FieldType.TECHNICAL,
                ),
            ]),
            LayoutLine(elements=[
                LayoutElement(kind="text", content="Video: "),
                LayoutElement(
                    kind="field",
                    content="Video Codec",
                    field_type=FieldType.TECHNICAL,
                ),
                LayoutElement(kind="text", content=" | Audio: "),
                LayoutElement(
                    kind="field",
                    content="Audio Codec",
                    field_type=FieldType.TECHNICAL,
                ),
            ]),
        ],
        separator="\u2500",
    )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class LayoutManager:
    """Create, read, update, and delete named export layouts.

    Parameters
    ----------
    base_dir:
        Directory holding the layout files.  ``None`` (the default)
        resolves to ``app_data_dir() / "layouts"``.  The directory is
        created lazily on the first save, never in ``__init__``.

    Notes
    -----
    - One file per layout: ``<sanitised name>.json``.
    - Saves are atomic: the payload is written to a ``.tmp`` sibling
      first, then moved over the destination; the temp file is removed
      if anything fails.
    - Logo images live beside the JSON as
      ``<sanitised>.logo.<slot_id>.<ext>``; :meth:`save`
      copies them via ``pending_logos`` and :meth:`delete` removes the
      whole ``<sanitised>.logo.*`` sibling set with the JSON.
    - OS-level errors surface as :class:`LayoutManagerError` from
      :meth:`save` and :meth:`load`, with the original exception
      chained via ``__cause__``.
    - :meth:`list_layouts` skips corrupt or unreadable files silently
      and does not descend into sub-directories.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            self._dir = app_data_dir() / "layouts"
        else:
            self._dir = Path(base_dir)

    # -- Read -------------------------------------------------------------

    def list_layouts(self) -> list[str]:
        """Return the sorted original layout names.

        Names come from the JSON payloads, not the file stems.  A
        missing directory yields ``[]``; corrupt or unreadable files
        are skipped silently (never raised).
        """
        if not self._dir.is_dir():
            return []
        names: list[str] = []
        for path in self._dir.glob("*.json"):
            if not path.is_file():
                continue
            try:
                layout = LayoutModel.from_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if isinstance(layout.name, str):
                names.append(layout.name)
        return sorted(names)

    def exists(self, name: str) -> bool:
        """True when the sanitised form of *name* maps to an existing file."""
        return self._path_for(name).exists()

    def load(self, name: str) -> LayoutModel:
        """Return the layout stored for *name*.

        LogoSlots keep their ``logo_file`` exactly as stored (a
        relative filename resolved by the render layer); the file's
        existence is deliberately NOT checked here — a missing image is
        tolerated at render time, which draws a placeholder instead of
        failing the load.

        Raises :class:`LayoutManagerError` when the file is missing
        (``Layout not found: ...``) or cannot be read/parsed — corrupt
        JSON *or* an OS read error, both chained from the original
        exception (``Layout file is corrupt: ...``).
        """
        path = self._path_for(name)
        if not path.exists():
            raise LayoutManagerError(f"Layout not found: {name}")
        try:
            layout = LayoutModel.from_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise LayoutManagerError(f"Layout file is corrupt: {name}") from exc
        if not isinstance(layout.name, str):
            raise LayoutManagerError(f"Layout file is corrupt: {name}")
        return layout

    # -- Write ------------------------------------------------------------

    def save(
        self,
        layout: LayoutModel,
        *,
        pending_logos: dict[str, Path] | None = None,
    ) -> LayoutModel:
        """Persist *layout* under its (sanitised) name; return what was stored.

        Internal fields are stored exactly as given — only the file
        name is sanitised.  Existing files with the same sanitised name
        are overwritten.  Writes are atomic.

        Returns the layout AS SERIALISED: the retargeted copy whose
        LogoSlots point at the copied ``pending_logos`` files (after
        the healing pass below), or the input ``layout`` object itself
        when nothing was copied or healed.  The caller's input object
        is never mutated either way.

        *pending_logos* maps slot ids — ``"header.left"``,
        ``"header.center"``, ``"header.right"``, ``"footer.left"``,
        ``"footer.center"``, ``"footer.right"`` — to absolute image
        paths on disk.  Each image is copied to
        ``<sanitised>.logo.<slot_id_sanitised>.<ext>`` next to the JSON
        (dots in the id become underscores, the source extension is
        preserved; an existing logo for that slot is overwritten) and
        the layout's matching LogoSlot is repointed at that relative
        filename before serialisation — the caller's layout object is
        not mutated.  LogoSlots NOT listed in *pending_logos* keep their
        current ``logo_file`` unchanged (never deleted).  A stale
        ``logo_file`` — one whose file no longer exists on disk — is
        saved as-is without failing; missing logos are the render
        layer's problem at render time, the same policy as missing
        referenced screenshots.  A pending logo whose copy fails (an
        ``OSError``) does not abort the save: the slot keeps its
        previous ``logo_file`` and the JSON is still written.  Finally,
        any remaining ``logo_file=None`` LogoSlot whose
        ``<sanitised>.logo.<slot_id>.*`` sibling already exists on disk
        is repointed at that sibling before serialisation, so a later
        re-save without *pending_logos* (the export window's flow after
        the designer saved) cannot null out an image that was already
        copied.

        Raises
        ------
        LayoutManagerError
            The layout name is invalid (empty, whitespace-only,
            all-forbidden, or a Windows reserved device name), a
            *pending_logos* id does not address a LogoSlot of *layout*,
            *or* an OS-level error occurred while creating the
            directory, writing the temp file, or moving it into place.
            OS errors are chained via ``__cause__`` as the original
            ``OSError``; a failed copy of one pending logo is NOT such
            an error (see above).
        """
        path = self._path_for(layout.name)   # raises LayoutManagerError on an invalid name
        tmp = path.with_name(path.name + ".tmp")
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            to_write = layout
            if pending_logos:
                to_write = self._store_pending_logos(layout, pending_logos)
            to_write = self._heal_null_logo_files(to_write)
            tmp.write_text(to_write.to_json(), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            _remove_quietly(tmp)
            if isinstance(exc, OSError):
                raise LayoutManagerError(
                    f"Could not save layout '{layout.name}': {exc}"
                ) from exc
            raise
        return to_write

    def delete(self, name: str) -> None:
        """Remove the file stored for *name* and its logo siblings.

        Every ``<sanitised>.logo.*`` image next to the JSON
        is removed first; missing siblings are not an error.  Another
        layout's JSON that happens to look like a sibling (``X.logo.json``
        for a layout literally named ``X.logo``) is never touched.

        Raises :class:`LayoutManagerError` when the JSON does not exist.
        """
        path = self._path_for(name)
        if not path.exists():
            raise LayoutManagerError(f"Layout not found: {name}")
        prefix = f"{path.stem}.logo."
        for candidate in self._dir.iterdir():
            if (
                candidate.name.startswith(prefix)
                and candidate.suffix != ".json"
                and candidate.is_file()
            ):
                _remove_quietly(candidate)
        path.unlink()

    def ensure_default(self) -> LayoutModel:
        """Return an existing layout, creating the default if none exist.

        When the directory holds at least one loadable layout, the
        first by sorted name is returned unchanged.  Otherwise the
        :func:`default_layout` is saved and returned.  Idempotent —
        repeated calls never create a second default.
        """
        names = self.list_layouts()
        if names:
            return self.load(names[0])
        layout = default_layout()
        self.save(layout)
        return layout

    # -- Internals --------------------------------------------------------

    def _store_pending_logos(
        self, layout: LayoutModel, pending_logos: dict[str, Path]
    ) -> LayoutModel:
        """Copy pending logo images and repoint the matching slots.

        Every entry is validated against *layout* before anything is
        copied; each source image then lands at
        ``<sanitised>.logo.<slot_id_sanitised>.<ext>``, overwriting any
        previous logo for that slot.  Returns a NEW LayoutModel whose
        LogoSlots reference the copied relative filenames — *layout*
        itself is never mutated.
        """
        stem = _sanitize_name(layout.name)
        plan: list[tuple[str, Path, str]] = []   # (slot_id, source, dest name)
        for slot_id, source in pending_logos.items():
            if not isinstance(_find_slot(layout, slot_id), LogoSlot):
                raise LayoutManagerError(
                    f"Layout '{layout.name}' has no logo at slot {slot_id!r}."
                )
            source_path = Path(source)
            dest_name = (
                f"{stem}.logo.{slot_id.replace('.', '_')}{source_path.suffix}"
            )
            plan.append((slot_id, source_path, dest_name))

        logo_files: dict[str, str] = {}
        for slot_id, source_path, dest_name in plan:
            try:
                shutil.copy(source_path, self._dir / dest_name)
            except OSError:
                # A failed copy must not lose the whole layout: the slot
                # keeps its previous logo_file (usually None) and the
                # JSON is still written; a sibling already on disk may
                # even be reattached by save()'s healing pass.
                continue
            logo_files[slot_id] = dest_name

        return replace(
            layout,
            header=_retarget_config(layout.header, "header", logo_files),
            footer=_retarget_config(layout.footer, "footer", logo_files),
        )

    def _heal_null_logo_files(self, layout: LayoutModel) -> LayoutModel:
        """Repoint ``logo_file=None`` LogoSlots at an existing sibling image.

        The same layout can be saved twice — e.g. the designer persists
        chosen logos (copying ``<stem>.logo.<slot>.<ext>`` beside the
        JSON), then the export window re-saves the accepted layout whose
        in-memory LogoSlots still carry ``logo_file=None`` — and the
        second save would serialise a null reference to an image that IS
        on disk.  Only ``None`` references are
        healed; slots with a stored ``logo_file`` keep it untouched
        (stale references included).  When several extensions exist for
        a slot, the most recently copied sibling wins.  Returns a NEW
        LayoutModel; *layout* is not mutated.
        """
        stem = _sanitize_name(layout.name)
        logo_files: dict[str, str] = {}
        for prefix, config in (("header", layout.header), ("footer", layout.footer)):
            if config is None:
                continue
            for side in ("left", "center", "right"):
                slot = getattr(config, side)
                if not isinstance(slot, LogoSlot) or slot.logo_file is not None:
                    continue
                sibling_prefix = f"{stem}.logo.{prefix}_{side}."
                candidates = sorted(
                    (
                        path for path in self._dir.iterdir()
                        if path.is_file()
                        and path.suffix != ".json"
                        and path.name.startswith(sibling_prefix)
                    ),
                    key=lambda path: (path.stat().st_mtime, path.name),
                )
                if candidates:
                    logo_files[f"{prefix}.{side}"] = candidates[-1].name
        if not logo_files:
            return layout
        return replace(
            layout,
            header=_retarget_config(layout.header, "header", logo_files),
            footer=_retarget_config(layout.footer, "footer", logo_files),
        )

    def _path_for(self, name: str) -> Path:
        """Map *name* (original form) to its JSON file path."""
        return self._dir / f"{_sanitize_name(name)}.json"
