# Settings

The settings dialog holds two groups of values: the media tool paths and
the suggestion lists.

## FFmpeg and FFprobe paths

The catalog reads technical metadata with FFprobe, and screenshots are
generated with FFmpeg. By default the application expects both on `PATH`,
which is the usual state on Linux and macOS after a package-manager
install.

If they live somewhere else — for example an unpacked release build on
Windows — set the full path to each executable in the settings. The
application then uses the configured executables for new scans and for new
screenshot generation.

See [Installation](installation.md) for where to obtain the tools, and
[Screenshots](screenshots.md) for what FFmpeg is used for.

## Suggestion lists

Three fields come with editable suggestion lists:

- **Type** — what kind of recording it is
- **Source** — where the recording came from
- **Quality** — how the recording rates

Each list ships with sensible defaults. The lists are editable: add the
labels you use for your own collection, and remove ones you never pick.
The suggestions offered when you catalog a recording follow the lists as
you edited them, so the catalog stays consistent across sessions without
typing the same labels repeatedly.

## Related pages

- [Installation](installation.md) — installing FFmpeg and FFprobe
- [First steps](first-steps.md) — the basic workflow
- [Screenshots](screenshots.md) — what FFmpeg generates once configured
- [Troubleshooting](troubleshooting.md) — what to check when the tools are
  not found
