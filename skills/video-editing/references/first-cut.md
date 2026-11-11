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
sheets (`analysis/sheets/`), film strips (`analysis/strips/`) and `analysis/quality.txt`: per clip the usable
ranges, the flaws, the camera moves, people and objects, the sound, the place and other takes of the same shot.
It also finds the same person again across clips by the face (`analysis/people.json`, pictures in
`analysis/people.jpg`).
Hours of footage take hours on a CPU: start it in the background and say so.

Then look for yourself:
- Read `transcript_all.txt` completely. Mark the sentences that carry the story.
- Read `quality.txt` next to the transcript. Take shots from the `usable` ranges and prefer the steady ones; cut
  into a pan or out of it where `camera` says it starts or has come to rest, not in the middle. `similar` names
  other takes of a shot: compare them and take the best. `wind`, `rumble` and `clipping` say where the original
  sound will not carry a scene. The bar under each contact sheet shows the same at a glance (green usable, red
  flawed, blue speech, orange wind). These are measurements, not judgments: a shaky shot can be the right one, and
  a "usable" one can be dull. The numbers per second are in `analysis/image/<id>.json` (`docs/formats.md`).
- Look at `analysis/people.jpg`: one row of faces per person (`P1`, `P2` …). Show it to the user and ask who is
  who; store the answers with `cutter people name P1 "Ada"`. Rows that show the same person: `cutter people merge
  P1 P4`. The `who` line of `quality.txt` then says who is in which clip and when – use it to find the shots of
  the main characters and to keep people on the no-go list out. It only knows faces that are large and turned
  towards the camera: a clip without a `who` line may still show the person, so check no-gos by eye as well. A row
  with mixed faces is a grouping error; do not rely on that ID.
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

Ask before starting the review app in the network, and ask for the reviewers' names. Add each one with
`cutter review password <name> --generate --json`: it makes a password, stores only its hash and returns the
password once in `password`. Give the user name, password and address to pass on; you cannot read the password
again later, only replace it (`--dry-run --json` shows in `exists` whether a name is already there). Never run
`cutter review password <name>` without `--generate` yourself: it asks for the password in a terminal, which you
do not have. A user who would rather choose the password runs that in a terminal of their own. Then
`cutter serve` prints the addresses to share. Check the app on a phone
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
