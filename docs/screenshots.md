# Screenshots

Screenshots are images attached to a recording. They are placed into the
export by the `[Screenshot:N]` layout token (see [Layouts](layouts.md));
the number in the token is the maximum per recording, from 1 to 20.

There are three ways to get screenshots into a session. They can be mixed:
an automatically generated set can be extended with your own images, and
either can be reordered afterwards.

## Automatic generation with FFmpeg

The Screenshot Manager can generate screenshots directly from a
recording's video file with FFmpeg. You choose how many frames to capture,
and the application extracts them at evenly spaced points of the recording
and stores them with the session. Generation needs the recording's
duration, so it is available once the file has been probed — see
[Installation](installation.md) if FFmpeg is not set up yet.

## Sidecar images next to the media file

Images stored next to a media file are picked up automatically when the
file is added to the session. If a folder already contains frames exported
by another tool, no manual step is needed; place them beside the video
files before scanning.

## Manual attachment in the Screenshot Manager

You can attach your own images to any recording, remove ones you do not
want, and reorder them. Order matters: the first screenshot in the manager
is the first one placed by `[Screenshot:N]` in the export.

## Where they end up

Both export formats include the session's screenshots — in the PDF at the
token position, and in the XLSX export as embedded images. See
[Export](export.md).

Screenshots are saved inside the `.bootleg` session file together with the
recordings, so a session stays self-contained on another machine: copy the
session file and the media files, and the images travel with them.

## Related pages

- [Layouts](layouts.md) — placing `[Screenshot:N]` in a template
- [Settings](settings.md) — configuring the FFmpeg used for generation
