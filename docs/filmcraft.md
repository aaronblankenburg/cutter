# FilmCraft

[FilmCraft](https://github.com/storytold/filmcraft) is an open-source video editor (MIT or Apache-2.0). cutter uses
its command-line engine `filmcraft-cli` to turn a timeline into a finished project and to export it. cutter is not
affiliated with FilmCraft or the ArtCraft team.

## Headless (the default)

```sh
cutter filmcraft build cut3      # -> cuts/cut3/cut3.fcproj
cutter filmcraft export cut3     # -> exports/cut3.mp4, subtitles burned in
```

`build` runs `filmcraft-cli` inside the Docker image, without a window:

1. It opens the previous cut's project (`cuts/cut2/cut2.fcproj`) – or an empty project for the first cut – and
   saves the result as `cuts/cut3/cut3.fcproj` (`--project … --save-as …`). The previous project is never changed;
   an existing `cut3.fcproj` is only replaced with `--overwrite`.
2. It imports `cuts/cut3/cut3.fcp7.xml` as a new sequence and checks that no media is offline.
3. It adds what a timeline format cannot carry, as one batch of commands (`cuts/cut3/work/filmcraft_batch.jsonl`,
   run with `filmcraft-cli run --keep-going`):
   - titles and end credits (`filmcraft.json`), the map zoom of an intro, end-text cards
   - the audio chain on every dialogue clip: high-pass, noise reduction, optional speech enhancement, compressor,
     limiter (values from the shot's `audio` in edit.json)
   - music levels: base level, lowered under dialogue with short ramps, faded out at the end of a cue
   - stabilisation (Warp Stabilizer) on shots with `"stabilize": true`
   - the subtitle track from `cut3.srt` with its style, marker colours
4. It saves the project.

`export` renders the sequence with the export preset (`Match Source – Adaptive Low Bitrate` by default,
`--preset` or `[filmcraft] export_preset`) and always burns the subtitles in (`burnCaptions`). `--start`/`--end`
export an excerpt. User presets are kept in `.cutter/filmcraft-data/` of the project.

**Measured** with FilmCraft 0.19.0 in the image on a 12th-gen Intel Core i5 laptop CPU (16 threads), 1080p25:
the demo (test patterns, titles, subtitles) exports at about 25 frames per second; a real travel film renders at
about 6 frames per second in scenes with maps and titles and about 2 frames per second in stabilised dialogue.
A 10-minute film therefore takes 30–60 minutes. `build` itself takes seconds (28 s for a 7.5-minute cut with
963 commands).

**Compared** with the desktop app's export of the same cut (a real 7.5-minute film): two 12-second excerpts gave
PSNR 41.3 dB and 32.8 dB (the lower value in a stabilised scene), identical loudness (−11.1 / −26.9 LUFS) and an
audio correlation of 0.9995 and 0.9988 without offset. The batch of commands was identical apart from FilmCraft's
own ids and effect positions.

## Opening a cut in the desktop app

The launcher mounts project and footage at the same paths as on the host, so `cuts/cut3/cut3.fcproj` opens directly
in the FilmCraft desktop app (downloads for Linux, macOS and Windows on the
[releases page](https://github.com/storytold/filmcraft/releases)). Changes made by hand are not part of the edit
list: tell the agent before the next cut, or they are lost when `cut4.fcproj` is built from the timeline.

## Live mode (optional)

```sh
cutter filmcraft gui                     # starts the desktop app with its control channel on 127.0.0.1:9876
cutter filmcraft build cut3 --bridge     # works in the open app instead of headless
```

`filmcraft gui` runs on the host. It creates a random control token in `~/.config/cutter/filmcraft_token` (mode 600)
and starts the app with `FILMCRAFT_CONTROL_TOKEN` and `--control 9876`. With `--bridge [PORT]` cutter sends the
same batch to the running app (on Linux the container uses the host network for this; on macOS use headless mode).
A backup of the open project is saved first (`cuts/cut3/work/backup_before_build.fcproj`).

cutter does not use `filmcraft-cli mcp`, and it has no renderer of its own: without FilmCraft, open the `.otio` or
`.fcp7.xml` in DaVinci Resolve, Premiere Pro, Kdenlive or another editor.

## The CLI in the image

The Dockerfile downloads the official release tarball for the image's architecture, checks it against a pinned
SHA-256, and installs only `filmcraft-cli` plus its license files (`LICENSE-MIT`, `LICENSE-APACHE`, `NOTICE` and the
OFL licenses of the fonts built into the binary) to `/usr/share/doc/filmcraft`. No logos or other assets are copied.
`cutter doctor` shows the version.

**Updating FilmCraft:**

1. Set `FILMCRAFT_VERSION` and both `FILMCRAFT_SHA256_*` values in the Dockerfile from the release's
   `SHA256SUMS.txt`.
2. Rebuild (`bin/cutter image build`) and run the tests in the image, including the headless demo test
   (`tests/test_filmcraft_headless.py`) and, if available, the local reference tests (docs/development.md).
3. Compare an export with the previous version (PSNR, loudness); pin the old version if output differs noticeably.

## Fonts

| Use | Font | In the image |
|---|---|---|
| subtitles, end text | Noto Sans (style "SemiBold") | `fonts-noto-core` |
| titles | P052 | `fonts-urw-base35` |
| credits, map labels | Liberation Sans Narrow | `fonts-liberation` |

Frames rendered from the same project on the host and in the container are pixel-identical (PSNR ∞ for a title,
a subtitle and the credits), so a project built in the container looks the same in the desktop app.

## Configuration

Titles, credits, the audio chain, music levels, caption style and export settings come from the defaults in
`src/cutter/filmcraft.py`, overridden by the file named in `[filmcraft] config` (see
[formats.md](formats.md#filmcraftjson)). The subtitle track's language is the project's `language`.

## Troubleshooting

- **Offline media after import:** a footage folder in cutter.toml is wrong or not mounted. `cutter doctor` checks
  every `[media]` path; rebuild with `cutter timeline <cut>` and `cutter filmcraft build <cut> --overwrite`.
- **"The project file exists already":** add `--overwrite` to rebuild it from the timeline.
- **A batch line failed:** the log names the line; the batch is in `cuts/<cut>/work/filmcraft_batch.jsonl`, and
  `filmcraft-cli commands <filter>` lists the commands and their parameters.
