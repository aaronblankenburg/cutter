# Workflow

How a film goes from a folder of footage through several cuts to the final export. The agent's view of the same
cycle is in [skills/video-editing](../skills/video-editing/SKILL.md); the file formats are in [formats.md](formats.md).

## Overview

```
footage (read-only)        project folder
───────────────────        ─────────────────────────────────────────────────────────────────────
                     ┌──>  analysis/            cutter analyze
  /media/cam   ──────┤     music/music.json     cutter music analyze      (after cutter music license)
  /media/phone ──────┘     cuts/cut1/edit.json  written by the agent (cut1) or by plan.py (cut2 …)
                           cuts/cut1/edit.json  cutter retime cut1        cuts snapped to the music
                           cuts/cut1/cut1.*     cutter timeline cut1      .otio, .fcp7.xml, .srt, audio levels
                           cuts/cut1/cut1.fcproj cutter filmcraft build   titles, credits, audio chain, subtitles
                           exports/cut1.mp4     cutter filmcraft export   subtitles burned in
                           .cutter/review/      cutter review-data cut1   video, thumbnails, timeline.json
                                                cutter serve / deploy     reviewers comment
                           .cutter/review/feedback-cut1.json   cutter feedback fetch
                           cuts/cut2/plan.py    the changes as code  ──>  cut2 …
```

## 1. Project

```sh
cutter new <folder> --media <footage> [--media <more footage>] [--title "…"] [--language de]
```

- The project folder is never inside the footage, and the footage is never inside the project.
- Default folder (agents): the current directory if it is empty, otherwise `./<name>-film`.
- `cutter.toml` holds the settings, `AGENTS.md` the project facts for agents (`CLAUDE.md` points to it).

## 2. Analysis

`cutter analyze` scans every `[media]` folder and writes:

| File | Content |
|---|---|
| `analysis/clips.json` | one entry per clip: id (`C001` …), footage root and path, recording time, length, size, fps, audio, words spoken |
| `analysis/transcripts/<id>.json` | Whisper segments with a start and end for every word |
| `analysis/transcript_all.txt` | all transcripts in one readable file |
| `analysis/sheets/`, `analysis/strips/` | contact sheets and film strips |
| `analysis/overview/` | an overview by folder and day (`--list` shows only this) |

Clips are numbered in recording order. Re-running only adds what is new (`--force` redoes everything). `--image`
adds sharpness, shake, exposure and scene changes per second (`analysis/image/`), which `retime` uses to avoid
blurry frames at cut points. `--language` (or `language` in cutter.toml) fixes the spoken language; otherwise
Whisper detects it per clip.

Transcription runs on the CPU with int8 (see the README for timings). The model is downloaded once (about 1.6 GB)
into the Docker volume `cutter-models`.

## 3. Music

1. `cutter music license` reads the license from the file tags and checks it against `music.allowed_licenses`
   (default CC0 and CC BY). ND and BY-SA are always reported as traps; unknown licenses as unknown.
2. `cutter music analyze` finds beats, downbeats (the "one" of each bar) and loudness. If the first downbeat is
   wrong, override the bar phase in cutter.toml: `[retime.cues.M1] phase = 2`.

## 4. The edit list

`cuts/cut1/edit.json` is the cut: sections of shots (clip, in, out, kind, subtitles, cutaways …), music cues anchored
to sections, markers. The agent writes it for the first cut. Every later cut is produced by `cuts/cut<n>/plan.py`,
a small script that reads the previous edit list and applies the changes (see "Next cut" below), so every cut can be
reproduced and every change is visible as code.

## 5. Retime

`cutter retime cut1` moves picture cuts under music onto beats and section changes onto downbeats, within a window
(`retime.window_s`, default 1.7 s) and without making shots shorter than `retime.min_duration_s`; dialogue
shots and shots marked `free_timing` keep their cuts. Rules in cutter.toml say which cuts must land on a downbeat (`required_downbeats`) and where a
cue enters (`[retime.cues.M2] entry = "first_downbeat"`). The edit list is rewritten in place; the input is kept as
`cuts/cut1/work/edit.before-retime.json`, the decisions as `work/retime_log.json`.

## 6. Timeline

`cutter timeline cut1` sets the camera audio levels (picture shots under music are lowered by
`audio.under_music_db`, others brought to `audio.ambience_target_lufs`) and builds:

- `cuts/cut1/cut1.otio` – OpenTimelineIO, the master
- `cuts/cut1/cut1.fcp7.xml` – FCP 7 XML for FilmCraft, DaVinci Resolve, Premiere Pro, Kdenlive
- `cuts/cut1/cut1.srt` – the subtitles

Tracks: V1 picture, V2 map, V3 route, V4 cutaways, V5 overview, A1 dialogue, A2 music (names configurable).
Media paths come from the footage roots in cutter.toml (see "Moving footage" below).

## 7. FilmCraft

`cutter filmcraft build cut1` imports the XML into a FilmCraft project and adds titles, credits, the map zoom, the
audio chain for dialogue, music levels (lowered under dialogue, faded out), stabilisation and the subtitle track.
It runs headless in the Docker image and writes `cuts/cut1/cut1.fcproj`, starting from the previous cut's project
(which stays unchanged). `cutter filmcraft export cut1` renders `exports/cut1.mp4` with burned-in subtitles. Details,
the live mode with the desktop app and the configuration: [filmcraft.md](filmcraft.md).

## 8. Review

```sh
cutter review-data cut1          # exports/cut1.mp4 (or a rough render) -> .cutter/review/data/cuts/cut1/
cutter review password alice     # once per reviewer; only a scrypt hash is stored
cutter serve                     # prints the addresses: localhost, the LAN address, <name>.local on macOS
```

The review app runs in the container and is published on all interfaces by default (`--bind 127.0.0.1` keeps it
on this computer; `--port` or `CUTTER_REVIEW_PORT` changes the port). It never starts without a password. It speaks
plain HTTP: share it only in networks you trust. For reviewers elsewhere, deploy it to a server with HTTPS
([deploy.md](deploy.md)).

Reviewers comment on a moment or a range, pick a quick action (shorter, remove, other shot, music softer …) or react
with an emoji. The agent's notes from `decisions.json` appear in violet at their places; its replies appear under
each comment. When a new cut is published, the app opens it automatically.

**Where the comments live:** in the project, `.cutter/review/data/feedback.sqlite` (local review app), or on the
server (deployed app). `cutter feedback fetch` copies the comments of a cut to `.cutter/review/feedback-<cut>.json`
in either case, so the project keeps the whole history.

## 9. Next cut

1. `cutter feedback fetch` – the comments on the **newest** cut (older cuts do not count any more).
2. `cutter plan cut2 --new` – `cuts/cut2/plan.py` from the template. Write each change as code, with a comment
   naming the person, the entry number and why. `cutter plan cut2` writes `cuts/cut2/edit.json`.
3. `cuts/cut2/decisions.json` – one entry per change or decision, placed in the new cut, with references to the
   comments it answers.
4. retime → timeline → filmcraft build → export → review-data → serve or deploy.
5. `cutter feedback reply cut1 --file replies.json` – a status (done, rejected) and a short reply for every
   comment; rejected ones always with a reason. The database is backed up first.

Manual changes in the FilmCraft desktop app are not part of the edit list: tell the agent before the next cut, so
they are carried over (or made again in `plan.py`).

## Moving footage, other computers

Footage folders have names in cutter.toml:

```toml
[media]
cam = "/Volumes/Card A/DCIM"
phone = "/home/me/Footage/Phone"
```

`clips.json` stores every clip as `{"root": "cam", "path": "100MEDIA/C0012.MP4"}`. Timelines and FilmCraft projects
are always built from these names, so after moving the footage (or opening the project on another computer) change
the path in cutter.toml and run `cutter timeline <cut>` and `cutter filmcraft build <cut> --overwrite` again.

The launcher mounts the project and every footage folder at the **same absolute path** inside the container, so the
paths written into `.otio`, `.fcp7.xml` and `.fcproj` are valid on the host: open them directly in FilmCraft or any
other editor. On macOS, footage on an external drive must be shared with Docker Desktop
(Settings → Resources → File sharing; `/Volumes` usually is).
