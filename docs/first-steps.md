# First steps

This page walks through the basic sequence: open the app, create a session,
add a recording, pick a layout, and export.

## Open the application

Open Bootleg Catalog Manager from the Start Menu (Windows) or from
wherever you installed it. See [Installation](installation.md) if it is not
installed yet.

??? note "Running from source"

    ```bash
    python -m bootleg_catalog.app.main
    ```

On start, the application offers a choice between creating a new session
and continuing a recent one. Recent sessions are listed by name; choosing
one loads it directly.

## Create a session

A session is the unit of work: it holds the recordings you are cataloging
together with the layout used to export them. Sessions are saved as
`.bootleg` files (see [Export](export.md)).

When you create a new session, you are asked for a display name. The name
identifies the session in the recent-session list.

## Add a recording

Add recordings by scanning a folder for media files. The catalog reads the
technical metadata with FFprobe — codecs, duration, file size, and the
rest — and never modifies the media files themselves. Files already present
in the session are skipped when you scan the same folder again.

Recordings can also be imported as a single entry from a disc rip folder;
see [Rips and multi-part recordings](rips.md).

Audio recordings are added the same way, with
`+ Add Audio Recording…` — either a single audio file or a folder of
tracks as one album entry; see [Audio recordings](audio.md).

## Pick a layout

Every export is driven by a layout template. A default layout is provided;
adjust it in the layout designer when you need a different set of fields, a
different order, or screenshots at a different position. The marker syntax
is described in [Layouts](layouts.md).

## Export

Once the session holds recordings and the layout looks right, run the
export. The result is either a PDF catalog or an XLSX spreadsheet — see
[Export](export.md) for what each format contains.

## Save the session

Use `File → Save As…` to write the session to a `.bootleg` file, and
`File → Open…` or the startup "Continue a recent session" flow to load it
again later.
