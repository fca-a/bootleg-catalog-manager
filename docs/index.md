# Bootleg Catalog Manager

Bootleg Catalog Manager is a local-first desktop application for cataloging
live recordings and building custom PDF/XLSX exports. The catalog is stored
in SQLite, there is no cloud component and no telemetry. A set of recordings
is saved as a `.bootleg` session file, and exports are built from
user-editable layout templates rather than fixed report formats. FFprobe
supplies the technical metadata, and the application never modifies your
media files.

## Guides

- [Installation](installation.md) — set up a virtual environment and install
  the dependencies, including FFmpeg and FFprobe.
- [First steps](first-steps.md) — create a session, add recordings, choose a
  layout, and run your first export.
- [Layouts](layouts.md) — write layout templates with field markers and
  inline formatting.
- [Screenshots](screenshots.md) — generate screenshots with FFmpeg, pick up
  images stored next to your media, or attach your own.
- [Rips and multi-part recordings](rips.md) — add DVD and Blu-ray rip
  folders as a single entry, and combine multi-part recordings.
- [Export](export.md) — produce a PDF catalog or an XLSX spreadsheet.
- [Settings](settings.md) — configure the FFmpeg and FFprobe paths and the
  suggestion lists.
- [Troubleshooting](troubleshooting.md) — solutions for common problems.

## Requirements

- Python 3.12 or newer
- PySide6 (installed via pip, see [Installation](installation.md))
- FFmpeg and FFprobe available on `PATH`, or configured in
  [Settings](settings.md)

Tested on Windows; should work on Linux and macOS.

## License

MIT — see the LICENSE file in the repository.
