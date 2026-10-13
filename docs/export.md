# Export

A session exports to two formats: PDF and XLSX. Both are driven by the same
layout (see [Layouts](layouts.md)), so the fields and screenshots in the
export are the ones the layout asks for.

## PDF

The PDF export renders the session as pages: a header band, one block per
recording with the fields in layout order, inline formatting, optional
screenshots at `[Screenshot:N]` positions, and a footer band. Header and
footer bands can carry text and optional logo images.

Page size (A4, Letter, A3), orientation (portrait or landscape), and
custom margins are part of the page setup, so the same layout can produce
a small hand list or a full-page catalog.

Use the PDF when the catalog is meant to be read or printed as a document.

## XLSX

The XLSX export writes the recordings as spreadsheet rows, one row per
recording, with the layout's fields as columns. It is meant for further
processing: sorting, filtering, or reusing the data in another tool.

Use the XLSX when the catalog is meant to be worked with as data rather
than read as pages.

## Sessions

`File → Save As…` writes the session itself as a `.bootleg` file — a ZIP
archive holding the layout, the recordings, and any manually attached
screenshots. It is self-contained: with the media files it references, it
opens unchanged on another machine. `File → Open…` or the startup
"Continue a recent session" flow loads one again.

## If an export fails

The two formats fail differently, and the usual causes are listed under
[Troubleshooting](troubleshooting.md) — in particular the latin-1 limit
of the PDF writer, which does not apply to the XLSX export.

## Related pages

- [Layouts](layouts.md) — writing the template both formats share
- [Screenshots](screenshots.md) — the images embedded in either format
