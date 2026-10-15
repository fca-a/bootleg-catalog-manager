# Audio recordings

Audio recordings live in the same session as video recordings and are
driven by the same layouts. The difference is how they are added and what
they can show: an audio entry has no video frames, so screenshots never
apply to it.

## Adding an audio recording

Use `+ Add Audio Recording…` in the toolbar, next to
`+ Add Video Recording…`. A dialog asks whether the source is a single
file or a folder:

- **Single file…** — pick one audio file (FLAC, MP3, M4A, AAC, OGG, Opus,
  WAV, WMA, AIFF, APE, or ALAC). It is probed with FFprobe and added the
  same way a video file is.
- **Folder…** — pick an album folder. Every audio file directly inside
  the folder becomes ONE entry (subfolders are ignored): the tracks are
  read in sorted order, their durations are summed into the entry's
  duration, and the first track's audio parameters stand for the whole
  album. The folder path is the recording's source.

Both routes end in the same add dialog as video, where the archival
fields are filled in by hand.

## Screenshots

Audio entries have no frames to capture, so `[Screenshot:N]` resolves to
nothing for them — the token draws no images in the preview and in every
export. Video recordings are unaffected; see
[Screenshots](screenshots.md).

## Layouts

The same layout markers work for audio:

- `{Duration}` — the single file's length, or the summed track durations
  of an album
- `{Audio Codec}`, `{Sample Rate}`, `{Audio Bitrate}`,
  `{Audio Channels}` — read from the file, or from the first track of an
  album
- `[Venue]`, `[Show]`, `[Year]`, and the other archival fields behave as
  they do for video

A session can mix video and audio entries without changing the layout.
See [Layouts](layouts.md) for the marker syntax, and
[First steps](first-steps.md) for where adding recordings fits in the
workflow.

## Related pages

- [First steps](first-steps.md) — the basic workflow
- [Layouts](layouts.md) — field markers and inline formatting
- [Screenshots](screenshots.md) — what `[Screenshot:N]` does for video
- [Export](export.md) — producing the PDF or XLSX
