# Layouts

Layouts are text templates that drive both the PDF and the XLSX export.
Every line of a layout becomes a line of the output, with the field markers
replaced by the recording's values.

## Marker families

There are two marker families, and they carry different meaning:

- `{FieldName}` for technical fields, for example `{Duration}`,
  `{Resolution}`, `{Video Codec}`
- `[FieldName]` for archival fields, for example `[Show]`, `[Venue]`,
  `[Year]`, `[Trade Status]`

Technical fields hold what FFprobe read from the file; archival fields hold
what you cataloged by hand.

## Inline formatting

Formatting markers wrap a run of text inside a line:

- `**bold**` for bold text
- `*italic*` for italic text
- `==highlight==` for a highlighted run
- `[mark=#RRGGBB]text[/mark]` for a custom highlight color
- `[color=#RRGGBB]text[/color]` for a text color

The highlight and text color markers take a hex color with a leading `#`.

## Screenshots in a layout

`[Screenshot:N]` places N screenshots (1-20) wherever the layout calls for
them. The size, spacing, borders, and alignment come from the screenshot
settings in the page setup; see [Screenshots](screenshots.md).

## Page setup

The page setup controls the paper size (A4, Letter, A3), orientation
(portrait or landscape), and custom margins. Header and footer bands can
carry text and optional logo images.

## Background colors

The page setup dialog also has a Backgrounds group with five colors, one
per area of the page. Any of the five can be left unset, which keeps the
export's default — a white page with no fills:

- **Page background** — the whole page, behind everything
- **Header background** — the band across the top margin, behind the
  header text
- **Footer background** — the band across the bottom margin, behind the
  footer text
- **Body background** — the content column between the two bands
- **Screenshot background** — the fill behind each image placed by
  `[Screenshot:N]`

Each row shows its current color as a swatch; clicking it opens a color
picker, and clearing it returns the area to unset. The colors are part of
the layout, so they travel with the session file, and the export preview
shows them before the PDF is written.

## Trade Status

`[Trade Status]` is a free-text archival field for whether the recording
can be traded. It ships with two default suggestions: **NFT** (Not For
Trade) and **NFTF** (Not For Trade Forever). Any value can be typed — the
suggestions are a starting point, not a fixed list. The suggestion list
is not yet editable in the settings dialog; that is planned for a future
release.

## Worked example

```text
{Date} — [Show]
{Duration} at [Venue], [Location]
==[Year]==  [Video Codec]
[Screenshot:2]
```

For each recording this prints the date and show name, a line with the
duration, venue, and location, the year highlighted together with the video
codec, and finally two screenshots.

Layouts are edited in the layout designer, which previews the result as you
type. See [First steps](first-steps.md) for where the designer fits in the
workflow.
