"""Session store — read/write the ``.bootleg`` export-session container.

Format v4 is a ZIP archive (magic ``PK``) holding::

    session.bootleg
    ├── manifest.json
    └── screenshots/            # only embedded screenshots
        ├── r0001-s02.jpg
        └── ...

``manifest.json`` schema (v4)::

    {
      "format_version": 4,
      "display_name": "...",
      "created_at": "ISO-8601 UTC",
      "modified_at": "ISO-8601 UTC",
      "layout": { ...exact LayoutModel.to_dict() output... },
      "recordings": [
        { ...dataclasses.asdict(ExportRecording)...,
          "parts": ["p1", "p2"], "screenshot_split": 1 }
      ],
      "screenshots": [
        {"recording": "<source_path>", "mode": "referenced", "path": "...",
         "origin": "auto"},
        {"recording": "<source_path>", "mode": "embedded",
         "entry": "screenshots/...", "origin": "manual"}
      ]
    }

Screenshot metadata lives on each ``ExportRecording.screenshot_paths``.
``mode`` and ``origin`` are independent attributes of every entry:

- ``mode`` decides storage: ``referenced`` keeps a path on disk,
  ``embedded`` stores the bytes in ``screenshots/`` and resolves them
  into a per-session cache directory on load, so callers always receive
  real local paths without knowing which mode was used (a missing
  referenced file is still listed so callers can warn — it never fails
  the load).
- ``origin`` records provenance: ``manual`` (user-attached via the
  Screenshot Manager) or ``auto`` (sidecar discovery).  The store writes
  it for every entry and restores the ``manual`` ones into
  ``ExportRecording.manual_paths`` on load.

Format history (version table):

- **v1**: no ``origin`` on screenshot entries, no ``parts`` on recordings.
- **v2**: ``origin`` added to every screenshot entry.
- **v3**: ``parts`` added to every recording (the 1-2 video files a
  single entry spans).
- **v4**: ``screenshot_split`` added to every recording (size of the
  part-1 screenshot block; ``null`` = caller-computed default).

Version compatibility:

- **v1 → load**: v1 manifests have no ``origin`` field; every screenshot
  loads as ``auto`` and ``manual_paths`` stays empty.  The embedded /
  referenced *mode* logic is unchanged.
- **v1/v2 → load**: recording dicts without a ``parts`` key load as a
  single-part recording, ``parts = (source_path,)``.
- **v1/v2/v3 → load**: recording dicts without a ``screenshot_split``
  key (v3 and earlier never wrote one) load with ``None``; callers
  compute the default split.
- **v1/v2 → re-save**: the manifest always declares the current
  ``FORMAT_VERSION``, so the next save upgrades the file to v4 with
  ``"origin": "auto"`` on every screenshot entry and ``parts`` plus
  ``screenshot_split`` on every recording.
- An ``origin`` value other than ``"manual"`` is treated as ``"auto"``
  (lenient forward compatibility).

This module is pure: no Qt, no catalog, no repository, no SQLite.
Legacy SQLite-based ``.bootleg`` files are detected by their header and
rejected with a clear error.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bootleg_catalog.models.export_recording import ExportRecording
from bootleg_catalog.models.layout_model import LayoutModel
from bootleg_catalog.utils.logging import get_logger
from bootleg_catalog.utils.paths import app_data_dir

FORMAT_VERSION = 4
MANIFEST_NAME = "manifest.json"
_SCREENSHOT_DIR = "screenshots"
_CACHE_DIR_NAME = "cache"

# Subdirectories of the screenshot cache untouched for longer than this
# are eviction candidates on startup; see evict_cache().
CACHE_EVICTION_AGE_DAYS = 30

_ZIP_MAGIC = b"PK"
_SQLITE_MAGIC = b"SQLite format 3"

_LEGACY_MESSAGE = "This is a legacy catalog file, not an export session."
_INVALID_MESSAGE = "Not a valid .bootleg session file."


class SessionStoreError(Exception):
    """Raised when a session file cannot be read or written."""


@dataclass
class SessionData:
    """In-memory contents of a ``.bootleg`` session file.

    Screenshot metadata is attached to each recording's
    ``screenshot_paths``; the store resolves embedded entries into a
    cache directory on load.
    """

    display_name: str
    layout: LayoutModel
    recordings: list[ExportRecording]
    format_version: int = FORMAT_VERSION
    created_at: str = ""


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def save_session(
    data: SessionData,
    output_path: str | Path,
    *,
    embed_paths: set[str] | None = None,
) -> Path:
    """Write *data* as a ``.bootleg`` archive.  Returns the final path.

    The write is atomic: content goes to a temporary file inside the
    destination directory and is renamed into place with
    :meth:`pathlib.Path.replace`.  On any failure the temporary file is
    removed and :class:`SessionStoreError` is raised with the original
    exception as ``__cause__``.

    Parameters
    ----------
    data:
        Session contents to write.
    output_path:
        Destination file path.  Missing parent directories are created.
    embed_paths:
        Screenshot source paths that must be embedded (bytes stored in
        ``screenshots/``) instead of referenced by path.  ``None``
        (default) references every screenshot.  If an embedded source
        does not exist on disk, the save fails with
        :class:`SessionStoreError`.
    """
    output = Path(output_path)
    tmp_path: Path | None = None
    try:
        manifest, embedded = _build_manifest(data, embed_paths)
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".session-", suffix=".tmp", dir=str(output.parent)
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            for arcname, source in embedded:
                zf.write(source, arcname, compress_type=zipfile.ZIP_STORED)
        tmp_path.replace(output)
    except Exception as exc:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise SessionStoreError(
            f"Could not save the session file:\n{output}"
        ) from exc
    return output


def _build_manifest(
    data: SessionData,
    embed_paths: set[str] | None,
) -> tuple[dict[str, Any], list[tuple[str, Path]]]:
    """Return ``(manifest, embedded)`` where *embedded* is a list of
    ``(zip_entry_name, source_path)`` pairs to store in the archive."""
    embed = embed_paths if embed_paths is not None else set()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    recordings_out: list[dict[str, Any]] = []
    screenshots_out: list[dict[str, str]] = []
    embedded: list[tuple[str, Path]] = []

    for rec_index, recording in enumerate(data.recordings, start=1):
        rec_out = asdict(recording)
        # JSON has no set type: serialise the manual subset sorted for
        # stable manifests.  The screenshots index below stays the
        # authoritative source on load.
        rec_out["manual_paths"] = sorted(recording.manual_paths)
        # v3: every recording carries its 1-2 part paths.
        rec_out["parts"] = list(recording.parts)
        # v4: every recording carries its screenshot split (null =
        # caller-computed default).
        rec_out["screenshot_split"] = recording.screenshot_split
        # ``is_rip`` round-trips via asdict like every other field;
        # manifests without the key load as False, so the format
        # version stays 4.
        recordings_out.append(rec_out)
        for shot_index, shot in enumerate(recording.screenshot_paths, start=1):
            origin = "manual" if shot in recording.manual_paths else "auto"
            if shot in embed:
                suffix = Path(shot).suffix or ".jpg"
                arcname = f"{_SCREENSHOT_DIR}/r{rec_index:04d}-s{shot_index:02d}{suffix}"
                screenshots_out.append(
                    {
                        "recording": recording.source_path,
                        "mode": "embedded",
                        "entry": arcname,
                        "origin": origin,
                    }
                )
                embedded.append((arcname, Path(shot)))
            else:
                screenshots_out.append(
                    {
                        "recording": recording.source_path,
                        "mode": "referenced",
                        "path": shot,
                        "origin": origin,
                    }
                )

    manifest: dict[str, Any] = {
        # The manifest always declares the current format version; a
        # session loaded from v1 is upgraded on its next save.
        "format_version": FORMAT_VERSION,
        "display_name": data.display_name,
        "created_at": data.created_at or now,
        "modified_at": now,
        "layout": data.layout.to_dict(),
        "recordings": recordings_out,
        "screenshots": screenshots_out,
    }
    return manifest, embedded


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def load_session(path: str | Path) -> SessionData:
    """Read a ``.bootleg`` archive.

    Raises
    ------
    SessionStoreError
        On any failure: missing/unreadable file, legacy SQLite catalog
        file, invalid magic, corrupt ZIP, bad manifest, or invalid data.
        Low-level errors (``zipfile.BadZipFile``, ``json.JSONDecodeError``,
        ``OSError``...) are always chained as ``__cause__``.
    """
    file_path = Path(path)
    head = _read_head(file_path)

    if not head.startswith(_ZIP_MAGIC):
        if head.startswith(_SQLITE_MAGIC):
            raise SessionStoreError(_LEGACY_MESSAGE)
        raise SessionStoreError(_INVALID_MESSAGE)

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            manifest = _read_manifest(zf, file_path)
            return _session_from_manifest(manifest, zf, file_path)
    except SessionStoreError:
        raise
    except zipfile.BadZipFile as exc:
        raise SessionStoreError(
            f"The session file is not a readable ZIP archive:\n{file_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise SessionStoreError(
            f"The session file has a corrupt {MANIFEST_NAME}:\n{file_path}"
        ) from exc
    except OSError as exc:
        raise SessionStoreError(
            f"Could not read the session file:\n{file_path}"
        ) from exc
    except Exception as exc:
        raise SessionStoreError(
            f"The session file contains invalid data:\n{file_path}"
        ) from exc


def _read_head(file_path: Path) -> bytes:
    """Return the first 16 bytes of *file_path*."""
    try:
        with open(file_path, "rb") as fh:
            return fh.read(16)
    except OSError as exc:
        raise SessionStoreError(
            f"Could not read the session file:\n{file_path}"
        ) from exc


def _read_manifest(zf: zipfile.ZipFile, file_path: Path) -> dict[str, Any]:
    """Read and JSON-decode ``manifest.json`` from the archive."""
    try:
        raw = zf.read(MANIFEST_NAME)
    except KeyError as exc:
        raise SessionStoreError(
            f"The session file has no {MANIFEST_NAME}:\n{file_path}"
        ) from exc
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SessionStoreError(
            f"The session file has an invalid {MANIFEST_NAME}:\n{file_path}"
        )
    return data


def _session_from_manifest(
    manifest: dict[str, Any],
    zf: zipfile.ZipFile,
    file_path: Path,
) -> SessionData:
    """Validate *manifest* and build a :class:`SessionData`."""
    version = manifest.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SessionStoreError(
            f"The session file has an invalid format_version: {version!r}."
        )
    if version > FORMAT_VERSION:
        raise SessionStoreError(
            f"The session file uses format version {version}, "
            f"but this application supports up to version {FORMAT_VERSION}."
        )

    layout_raw = manifest.get("layout")
    if not isinstance(layout_raw, dict):
        raise SessionStoreError(
            f"The session file has no valid layout:\n{file_path}"
        )
    layout = LayoutModel.from_dict(layout_raw)

    raw_recordings = manifest.get("recordings")
    if not isinstance(raw_recordings, list):
        raise SessionStoreError(
            f"The session file has no valid recordings list:\n{file_path}"
        )
    recordings: list[ExportRecording] = []
    for item in raw_recordings:
        fields = dict(item)
        # The screenshots index below is the authoritative source for
        # both the path list and the manual subset; this is only the
        # fallback for recordings without index entries.  v1 manifests
        # have no key at all -> everything auto.
        raw_manual = fields.pop("manual_paths", [])
        fields["manual_paths"] = (
            set(raw_manual) if isinstance(raw_manual, list) else set()
        )
        # v1/v2 manifests predate ``parts``: a recording without the key
        # is a single-file entry.  v3+ lists pass through verbatim —
        # ExportRecording.__post_init__ validates them.
        if "parts" not in fields:
            fields["parts"] = (fields["source_path"],)
        # v1-v3 manifests predate ``screenshot_split``: a missing key
        # loads as ``None``; callers compute the default split.
        if "screenshot_split" not in fields:
            fields["screenshot_split"] = None
        recordings.append(ExportRecording(**fields))

    display_name = manifest.get("display_name", "")
    if not isinstance(display_name, str):
        display_name = ""
    created_at = manifest.get("created_at", "")
    if not isinstance(created_at, str):
        created_at = ""

    _resolve_screenshots(manifest, recordings, zf, file_path)

    return SessionData(
        display_name=display_name,
        layout=layout,
        recordings=recordings,
        format_version=version,
        created_at=created_at,
    )


def _resolve_screenshots(
    manifest: dict[str, Any],
    recordings: list[ExportRecording],
    zf: zipfile.ZipFile,
    file_path: Path,
) -> None:
    """Set each recording's ``screenshot_paths`` from the manifest index.

    ``referenced`` entries keep their original path (even when the file
    no longer exists — missing shots are listed so callers can warn).
    ``embedded`` entries are extracted into the per-session cache and
    resolved to the cached path.  A cached file is reused only when its
    CRC-32 matches the zip entry, so a stale file left by an earlier save
    (same entry name, different bytes) is re-extracted.

    The resolved paths whose entry carries ``"origin": "manual"`` are
    also collected into ``recording.manual_paths``.  v1 entries have no
    origin field (and any non-``"manual"`` value counts as ``"auto"``),
    so v1 files load with an empty manual set.
    """
    raw_entries = manifest.get("screenshots", [])
    if not isinstance(raw_entries, list):
        raise SessionStoreError(
            f"The session file has an invalid screenshots index:\n{file_path}"
        )

    entries_by_recording: dict[str, list[dict[str, Any]]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("recording"), str):
            raise SessionStoreError(
                f"The session file has an invalid screenshot entry: {entry!r}"
            )
        entries_by_recording.setdefault(entry["recording"], []).append(entry)

    cache_dir: Path | None = None
    for recording in recordings:
        entries = entries_by_recording.get(recording.source_path)
        if not entries:
            continue
        resolved: list[str] = []
        manual: set[str] = set()
        for entry in entries:
            mode = entry.get("mode")
            if mode == "referenced":
                target = entry.get("path")
                if not isinstance(target, str):
                    raise SessionStoreError(
                        f"Invalid referenced screenshot entry: {entry!r}"
                    )
                resolved.append(target)
            elif mode == "embedded":
                arcname = entry.get("entry")
                if (
                    not isinstance(arcname, str)
                    or not arcname.startswith(_SCREENSHOT_DIR + "/")
                    or ".." in Path(arcname).parts
                ):
                    raise SessionStoreError(
                        f"Invalid embedded screenshot entry: {entry!r}"
                    )
                if cache_dir is None:
                    cache_dir = _cache_dir_for(file_path)
                cached = cache_dir / Path(arcname)
                if not _cache_matches_entry(zf, arcname, cached):
                    zf.extract(arcname, str(cache_dir))
                resolved.append(str(cached))
            else:
                raise SessionStoreError(
                    f"Unknown screenshot mode {mode!r} in the session file."
                )
            # v1 entries have no origin; anything but "manual" is auto.
            if entry.get("origin") == "manual":
                manual.add(resolved[-1])
        recording.screenshot_paths = resolved
        recording.manual_paths = manual


def _cache_matches_entry(zf: zipfile.ZipFile, arcname: str, cached: Path) -> bool:
    """Return ``True`` when *cached* exists and its CRC-32 matches the
    zip entry.

    A stale cache file — same entry name, different bytes from an
    earlier save — fails this check and is re-extracted from the
    archive (``zipfile`` overwrites the target file).
    """
    info = zf.getinfo(arcname)
    if not cached.is_file():
        return False
    return _file_crc32(cached) == (info.CRC & 0xFFFFFFFF)


def _file_crc32(path: Path) -> int:
    """Return the CRC-32 of *path* as an unsigned int."""
    crc = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def _cache_dir_for(session_path: Path) -> Path:
    """Return ``app_data_dir() / "cache" / "<session-id>"`` for *session_path*.

    ``<session-id>`` is the first 16 hex chars of the SHA-256 of the
    absolute session path.  :func:`app_data_dir` is called lazily so
    tests can patch it.
    """
    digest = hashlib.sha256(str(session_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(app_data_dir()) / _CACHE_DIR_NAME / digest


def evict_cache(
    *,
    keep_paths: set[str] | None = None,
    now: datetime | None = None,
) -> int:
    """Delete stale subdirectories under ``app_data_dir() / "cache"``.

    Returns the number of subdirectories removed.  Never raises.

    Safety rules:

    - Any directory whose name (or a path under it) appears in
      *keep_paths* is preserved unconditionally.  Callers pass the
      absolute paths an open session's recordings reference (their
      parent cache directory is thereby protected); the startup path
      passes ``None`` because no session is open yet.
    - Directories modified within :data:`CACHE_EVICTION_AGE_DAYS` are
      preserved.  Extracting an embedded screenshot touches the cache
      directory, so an in-use directory is fresh by construction.
    - An unreadable or locked directory is skipped, not deleted.  A
      partial ``rmtree`` failure is likewise skipped and not counted.
    - A missing (or unreadable) cache root returns ``0``.

    *now* defaults to the current UTC time; tests pass a fixed,
    timezone-aware instant so age checks never depend on the wall clock.
    """
    try:
        root = Path(app_data_dir()) / _CACHE_DIR_NAME
        if now is None:
            now = datetime.now(timezone.utc)
        keep = {str(p) for p in keep_paths} if keep_paths else set()
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return 0  # missing or unreadable cache root
        cutoff = now.timestamp() - CACHE_EVICTION_AGE_DAYS * 86_400
        removed = 0
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue  # only subdirectories are candidates
                if _covered_by_keep_paths(entry, keep):
                    continue  # referenced by an open session
                if entry.stat().st_mtime >= cutoff:
                    continue  # modified within the age threshold
                shutil.rmtree(entry)
            except OSError:
                continue  # unreadable or locked — skip, never raise
            removed += 1
        return removed
    except Exception as exc:
        get_logger().warning("Cache eviction skipped: %s", exc)
        return 0  # evict_cache never raises


def _covered_by_keep_paths(directory: Path, keep: set[str]) -> bool:
    """Return ``True`` when *keep* names *directory*, its bare name, or
    any path below it.

    The comparison is purely lexical (``os.path.normcase``): it never
    touches the filesystem and is case-insensitive on Windows.
    """
    needle = os.path.normcase(str(directory))
    name = os.path.normcase(directory.name)
    for raw in keep:
        candidate = os.path.normcase(raw)
        if (
            candidate == needle
            or candidate == name
            or candidate.startswith(needle + os.sep)
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Sniffing
# ---------------------------------------------------------------------------

def is_session_file(path: str | Path) -> bool:
    """Return ``True`` if *path* is a v1+ ``.bootleg`` session file.

    ``True`` requires the ZIP magic (``PK``) and a manifest declaring
    ``format_version >= 1``.  Legacy SQLite-based ``.bootleg`` files
    (header ``SQLite format 3\\0``) and arbitrary files return ``False``.
    This predicate never raises.
    """
    file_path = Path(path)
    try:
        head = _read_head(file_path)
        if not head.startswith(_ZIP_MAGIC):
            return False
        with zipfile.ZipFile(file_path, "r") as zf:
            manifest = json.loads(zf.read(MANIFEST_NAME))
    except Exception:  # noqa: BLE001 — a predicate must never raise
        return False
    if not isinstance(manifest, dict):
        return False
    version = manifest.get("format_version")
    return (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version >= 1
    )
