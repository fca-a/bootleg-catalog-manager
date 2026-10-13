# Rips and multi-part recordings

A disc rip is a folder of many small files — `.VOB` and `.m2ts` pieces,
plus the supporting files of the disc structure. The catalog imports such
a folder as a single recording entry rather than one entry per file.

## Rip folders

Two folder layouts are detected automatically:

- `VIDEO_TS` — the file structure of a DVD
- `BDMV` — the file structure of a Blu-ray

When one of these folders is imported, all of its files become one entry,
so a ripped disc shows up in the catalog the way it was released.

## Format label

During the import you pick a format label for the entry:

- `DVD`
- `VIDEO_TS`
- `VOB + Small files`
- `VOB with Smalls`

The label records how the rip is laid out, which keeps the catalog readable
when a collection mixes full-disc rips with loose VOB files.

## Duration is entered by hand

FFprobe cannot read a full disc structure starting from its first VOB file,
so the total duration of a rip folder is not available automatically. The
import asks for it instead, and you type it in.

## Multi-part recordings

Some recordings span two files — a disc change, or a split transfer. Up to
two files can be combined into a single entry, so the recording is
cataloged once with both of its parts instead of twice.

## Related pages

- [First steps](first-steps.md) — where importing fits in the workflow
- [Screenshots](screenshots.md) — attaching images to a rip entry
- [Export](export.md) — producing the catalog
