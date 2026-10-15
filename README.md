# Bootleg Catalog Manager

Documentation: https://fca-a.github.io/bootleg-catalog-manager/

Pre-built installers are available on the [Releases page](https://github.com/fca-a/bootleg-catalog-manager/releases). Currently Windows only; Linux and macOS users can run from source (see Installation below).

A local-first desktop application for cataloging live recordings and building custom PDF/XLSX exports.

## Overview

Everything runs on your machine: the catalog is stored in SQLite, there is no
cloud component and no telemetry. A set of recordings is saved as a `.bootleg`
session file, and exports are built from user-editable layout templates rather
than fixed report formats. FFprobe supplies the technical metadata; the
application never modifies your media files.

## Features

- Scan folders for media and extract technical metadata via FFprobe
- Skip files already present in the session when adding recordings
- Screenshots: images found next to media files are picked up automatically,
  and you can attach, remove, and reorder your own in the Screenshot Manager
- Build custom PDF and XLSX exports from a session of recordings
- Layout templates with `{TechnicalField}` and `[ArchivalField]` markers
- Header and footer bands with text and optional logo images
- Page setup: A4 / Letter / A3, portrait / landscape, custom margins
- Configurable background colors for the page, header, footer, body, and screenshots in PDF exports
- Inline formatting in layouts: bold, italic, highlight, text color
- `[Screenshot:N]` layout token to place N screenshots at any position
- Import a folder as a single rip entry (`VIDEO_TS` / `BDMV` folder detection)
- Multi-part recordings: combine up to two files into one entry
- Catalog audio recordings — either a single file or an album folder treated as one entry
- A free-text Trade Status field (`[Trade Status]`) with default suggestions for NFT and NFTF
- Editable suggestion lists for the Type / Source / Quality fields in settings

## Requirements

- Python 3.12 or newer
- PySide6 (installed via pip, see below)
- FFmpeg and FFprobe available on `PATH`, or configured in Settings
- Tested on Windows; should work on Linux and macOS

## Installation

1. Clone the repository:
   `git clone https://github.com/fca-a/bootleg-catalog-manager.git`
2. `cd bootleg-catalog-manager`
3. `python -m venv .venv`
4. Activate the venv:
   - Windows: `.venv\Scripts\activate`
   - Linux / macOS: `source .venv/bin/activate`
5. `pip install -e ".[dev]"`

## Running

```
python -m bootleg_catalog.app.main
```

## Building a standalone executable

```
pyinstaller bootleg-catalog-manager.spec
```

The executable is written under `dist/`.

To build the Windows installer, run `scripts\build-installer.bat`. It
requires Inno Setup 6 (https://jrsoftware.org/isdl.php) and expects the
PyInstaller output to already exist. The result is written to
`installer-output\BootlegCatalogManagerSetup-<version>.exe`.

## Layouts

Layouts are text templates with two marker families:

- `{FieldName}` for technical fields, e.g. `{Duration}`, `{Resolution}`,
  `{Video Codec}`
- `[FieldName]` for archival fields, e.g. `[Show]`, `[Venue]`, `[Year]`

Inline formatting: `**bold**`, `*italic*`, `==highlight==`,
`[mark=#RRGGBB]text[/mark]` for a custom highlight color, and
`[color=#RRGGBB]text[/color]` for a text color. `[Screenshot:N]` places N
screenshots (1-20) wherever the layout calls for them.

Example layout block:

```
{Date} — [Show]
{Duration} at [Venue], [Location]
==[Year]==  [Video Codec]
[Screenshot:2]
```

## Sessions

A `.bootleg` file is a ZIP archive holding the layout, the session's
recordings, and any manually attached screenshots. It is self-contained: with
the media files it references, it opens unchanged on another machine. Use
`File → Save As…` to write one, and `File → Open…` or the startup
"Continue a recent session" flow to load one.

## Tests

```
pytest
```

## License

MIT — see LICENSE.
