# Troubleshooting

## FFmpeg or FFprobe is not found

Symptom: scanning a folder produces no technical metadata, or screenshot
generation refuses to start.

The application looks for both tools on `PATH`. Either add the directory
containing them to `PATH`, or set the full executable paths in the settings
dialog — see [Settings](settings.md) and [Installation](installation.md).
Restart the scan after changing the paths.

## Screenshots are missing from an export

Check the following, in order:

- The recording actually has screenshots — open the Screenshot Manager
  for it. See [Screenshots](screenshots.md) for the three ways to get
  them into a session.
- The layout contains a `[Screenshot:N]` token where you expect them.
  Without the token no screenshot is placed, whatever the recording
  holds. See [Layouts](layouts.md).
- The images are attached to the recording you expect. Reordering in the
  Screenshot Manager changes which image takes which slot.

## A legacy `.bootleg` file will not open

A `.bootleg` file is a ZIP archive holding the layout, the recordings, and
the attached screenshots. A file written by a much older version may
predate the current session format and fail to load.

There is no in-place conversion. Create a new session and add the
recordings again; the media files themselves are never modified, so
nothing is lost but the cataloging work.

## PDF export fails on non-latin-1 characters

The PDF export fails when the layout or a field contains characters
outside the latin-1 range — for example Cyrillic, Greek, or CJK text.
Accented characters within latin-1 (such as `é` or `ñ`) are fine.

Two ways out:

- Export the session as XLSX instead. The spreadsheet format is not
  limited to latin-1 and keeps every character — see [Export](export.md).
- Replace the offending characters in the field values or in the layout
  text with latin-1 equivalents.
