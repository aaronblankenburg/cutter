# Demo

A complete, synthetic example: five generated clips (ffmpeg test patterns, each with its own tone, one at 29.97 fps)
and a 120 BPM drum loop (CC BY 4.0, generated) as music. Nothing in it comes from a real film, so it can be
shared, tested and rebuilt anywhere.

```sh
examples/demo/run.sh                 # NO_TRANSCRIPTS=1 skips the Whisper model download
```

This runs the whole cycle through the launcher, as an agent would:

| Step | Result |
|---|---|
| `cutter demo media footage` | `footage/camera/*.mp4` |
| `cutter new film --media footage` | `film/cutter.toml`, `AGENTS.md`, `CLAUDE.md` |
| `cutter analyze` | `film/analysis/` (the clips have tones, not speech: no transcripts) |
| `cutter demo cut` | `film/cuts/cut1/edit.json` and `decisions.json`, `film/music/click_120bpm.wav`, `film/filmcraft.json` |
| `cutter music analyze` | 120 BPM, first downbeat at 0.5 s |
| `cutter retime cut1` | section ends on downbeats |
| `cutter timeline cut1` | `cut1.otio`, `cut1.fcp7.xml`, `cut1.srt` |
| `cutter filmcraft build cut1` / `export cut1` | `cut1.fcproj` with title, credits and subtitles; `exports/cut1.mp4` |
| `cutter review-data cut1` | the cut for the review app |

Then add yourself as a reviewer and start the app:

```sh
cd examples/demo/film
../../../bin/cutter review password me
../../../bin/cutter serve
```

The public golden tests (`tests/golden_demo.py`) run the same edit list from a fixed analysis and compare every
output with `tests/golden/demo/expected/`.
