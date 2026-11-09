# The first cut

From a folder of footage to `cut1`, ready for review. Formats: [docs/formats.md](../../../docs/formats.md).

## 1. Briefing: at most five questions

Ask only what the request does not answer already, in one message:

1. **Purpose and audience** – a keepsake for friends and family, a festival entry, a social media clip?
2. **Target length** – a number of minutes.
3. **Style** – calm or upbeat, chronological or by theme, with text cards or a narrator?
4. **Music** – any preferences? Which licenses are fine? (Default: CC0 and CC BY only; non-commercial licenses
   only if the film is non-commercial.)
5. **No-gos** – people who must not appear, scenes to leave out, private moments.

Then create the project: `cutter new <folder> --media <footage>` (`--language de` when the footage is German),
`cd` into it, run `cutter doctor`.

## 2. Analysis

`cutter analyze` writes `analysis/clips.json` (one entry per clip: id `C001`…, time, length, size, words spoken),
transcripts with a time for every word (`analysis/transcripts/C001.json`), `analysis/transcript_all.txt`, contact
sheets (`analysis/sheets/`) and film strips (`analysis/strips/`). Hours of footage take hours on a CPU: start it
in the background and say so. `--image` adds sharpness, shake and exposure per second (useful for picking takes).

Then look for yourself:
- Read `transcript_all.txt` completely. Mark the sentences that carry the story.
- Look at the contact sheets of every clip; `cutter tool window <out.jpg> C017:30:8` shows a time window frame by
  frame, `cutter tool frames <out.jpg> "C017:31,35;C023:4"` single frames.
- Whisper mishears names and quiet speech: check doubtful sentences with `cutter tool asr C017:30:38:full`.

## 3. The story

- Sections with a common thread: an opening that sets the scene, a few dialogue scenes that carry the story,
  B-roll between them, an ending that closes the circle.
- Dialogue scenes first; B-roll is chosen to fit around them.
- Plan for the target length; a first cut may run 10–20 % long, the reviewers will shorten it.
- Leave out everything on the no-go list, also in the background of other shots.

## 4. Music: license first, then beats

1. Put candidate tracks into `music/`. Note title, artist, license and source.
2. `cutter music license` checks the tags against `music.allowed_licenses`. **ND** (no derivatives) and **BY-SA**
   (share-alike) are never used. If the tags say nothing, ask the user; never guess.
3. `cutter music analyze` measures beats, bars and loudness (`music/music.json`). Listen to the analysis: if the
   first downbeat is wrong, set `[retime.cues.M1] phase = <0–3>` in cutter.toml.
4. Credits: list every track with its license (`credits_music_lines` in the edit list).

## 5. The edit list

Write `cuts/cut1/edit.json` (format: docs/formats.md) and `cuts/cut1/decisions.json` with the main decisions of the
cut – what the story is, what was left out and why, open questions for the reviewers. Then:

```sh
cutter retime cut1          # picture cuts under music onto beats, section changes onto downbeats
cutter timeline cut1        # audio levels; cuts/cut1/cut1.otio, .fcp7.xml, .srt
cutter filmcraft build cut1 # titles, credits, audio chain, music ducking, subtitles (headless)
cutter filmcraft export cut1
cutter review-data cut1
```

Look at the export yourself before anyone else does: frames at every section change, the subtitles, the end.

## 6. Review

Ask before starting the review app in the network. The user adds reviewers with `cutter review password <name>`
(they type the password themselves), then `cutter serve` prints the addresses to share. Check the app on a phone
first; most reviewers watch there.

## Lessons from real cuts

- **Always subtitle dialogue**, even when it is easy to understand; burn the subtitles in on export.
- **Mild audio chain** for dialogue (high-pass 80 Hz, noise reduction 20 %, compressor). The full chain with speech
  enhancement sounded tinny.
- **Music changes at bar lines**, never mid-bar; `retime` puts section ends on downbeats.
- **No fade in the middle of a solo** or a vocal line; end a cue where the music breathes.
- **Let the final chord ring** – the last cue ends with the music, not with the picture.
- **Maps** move with ease-in-out and stay still long enough to read the names.
- **Text cards** have at most two lines and stay long enough to be read twice.
- **Check the review app on a phone first** – iPhone Safari is the usual reviewer.
