# Bootleg Catalog Manager

Bootleg Catalog Manager is a local-first desktop application for cataloging
live recordings and building custom PDF/XLSX exports. The catalog is stored
in SQLite, there is no cloud component and no telemetry. A set of recordings
is saved as a `.bootleg` session file, and exports are built from
user-editable layout templates rather than fixed report formats. FFprobe
supplies the technical metadata, and the application never modifies your
media files.

## Guides

- [Installation](installation.md) — install on Windows with the installer,
  or run from source on Linux and macOS; FFmpeg and FFprobe setup.
- [First steps](first-steps.md) — create a session, add recordings, choose a
  layout, and run your first export.
- [Audio recordings](audio.md) — add a single audio file or an album
  folder as one entry; screenshots do not apply to audio.
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

## Download

Pre-built installers are available on the
[Releases page](https://github.com/fca-a/bootleg-catalog-manager/releases).

[Download the latest release](https://github.com/fca-a/bootleg-catalog-manager/releases)

The installer is currently Windows-only. Linux and macOS users can run the
app from source — see [Installation](installation.md).

## Requirements

- Windows 10 or newer (for the installer)
- FFmpeg and FFprobe, installed separately — see
  [Installation](installation.md)
- Python 3.12 or newer and PySide6 are only needed for the source install
  path, not for the installer

Pre-built installer for Windows; Linux and macOS supported via source
install.

## License

MIT — see the LICENSE file in the repository.
