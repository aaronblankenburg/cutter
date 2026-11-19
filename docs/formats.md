# File formats

All files are JSON (UTF-8) except `cutter.toml`. Keys are English. Times are seconds (floats) unless stated
otherwise. Files written by the earlier German-keyed version of the pipeline are read transparently; see
[Files from earlier versions](#files-from-earlier-versions).

Contents: [cutter.toml](#cuttertoml) · [clips.json](#clipsjson) · [transcripts](#transcripts) ·
[music.json](#musicjson) · [edit.json](#editjson) · [decisions.json](#decisionsjson) ·
[alternatives.json](#alternativesjson) · [plan.py](#planpy) ·
[timelines](#timelines) · [filmcraft.json](#filmcraftjson) · [timeline.json (review app)](#timelinejson-review-app) ·
[feedback export](#feedback-export) · [replies](#replies) · [maps](#maps) · [earlier versions](#files-from-earlier-versions)

## cutter.toml

```toml
title = "Iceland"
language = "en"                 # spoken language ("" = Whisper detects it per clip)
ui_language = "en"              # review app: "en" or "de"
project_license = "CC BY-NC-SA 4.0"

[media]                         # named footage folders, mounted read-only at the same path
cam = "/Volumes/Card A/DCIM"
phone = "~/Footage/Phone"

[music]
allowed_licenses = ["CC0", "CC BY"]   # add "CC BY-NC", "CC BY-NC-SA" only for non-commercial films
base_lufs = -16.0               # music level without dialogue
under_dialogue_lufs = -35.0     # music under dialogue
low_lufs = -40.0                # under dialogue shots with audio.music_lower
fade_out_s = { M3 = 3.0 }       # fade-out per cue (a cue's own fade_out_s in edit.json wins)

[review]
people = [{ name = "Alice", color = "#38bdf8" }]
agent = "Claude"                # name shown at the agent's notes and replies
url = ""                        # address of a deployed review app
# for an app that embeds the review frontend (written to data/project.json, see the review app below):
# person = "Alice"              the fixed reviewer name     submit = false   no "Send feedback" box
# embedded = true               no brand, full width, frames of the same origin, messages to the parent page

[timeline]
otio_namespace = "cutter"       # key of cutter's metadata in OTIO items
music_dirs = ["music"]          # where music files are found by title (relative to the project, or absolute)
external_clips = {}             # clips outside the footage: { E1 = { file = "intro.mp4" } }
track_names = {}                # e.g. { V1 = "V1 Picture", A2 = "A2 Music" }
frame = "16:9"                  # picture format of the cuts (see Frames); edit.json "frame" sets it per cut
reframe = true                  # footage cut off by the frame keeps its subject in the picture (see Frames)

[audio]
under_music_db = -10.0          # camera audio of picture shots under music
ambience_target_lufs = -28.0    # picture shots without music: brought to this loudness …
ambience_min_db = -12.0         # … within these limits
ambience_max_db = 10.0
muted_below_db = -20.0          # shots already at or below this gain stay as they are
overlap_s = 0.3                 # how much music overlap counts as "under music"

[retime]
window_s = 1.7                  # how far a cut may move
min_duration_s = 0.8            # shortest shot after moving
analysis_weight = 0.4           # weight of image quality (analyze --image) at a cut point
penalty_off_downbeat = 50.0     # cost of a required downbeat that is missed
downbeat_bonus = 0.25           # preference for downbeats over other beats
required_downbeats = [{ last_of = "Intro" }, { before_tag = "finish" }, { last_with_block = "fast cuts" }]
limits = [{ section = "Intro", nr_previous = 2, cut_min = 28.6, cut_max = 29.2 }]

[retime.cues.M2]                # per music cue
phase = 2                       # which beat (0-3) is the first downbeat, overriding music.json
entry = "first_downbeat"        # or { kind = "downbeat_before_picture", max_s = 0.9 }
beats_from = "M1"               # use another cue's beats (a second excerpt of the same track)

[image_analysis]
recognize_faces = false         # do not work out who is who (default: true; see people.json)

[color]
auto = true                     # a colour correction per shot, suggested from the footage (default: true; see Colour)

[standins]
sidecars = "../sidecars"        # [media] holds small stand-in copies; this folder has the originals' metadata (see below)

[proxies]
make = true                     # every cutter analyze makes the missing proxies, as --proxies does (default: false)
height = 720                    # of the proxies (default: 720)

[filmcraft]
config = "filmcraft.json"       # titles, credits, map, captions … (see below)
export_preset = "Match Source – Adaptive Low Bitrate"
export_height = 360             # export at 360 lines (--scale 1/3 of the 1080-line sequence) instead of the preset's
                                # size; for stand-ins of that size, faster to render (default: 0 = the preset's size).
                                # cutter review-data then keeps the size, and a rough render uses it too

[paths]                         # where files go; {cut} becomes the cut name. The defaults:
# clips = "analysis/clips.json"   transcripts = "analysis/transcripts"   music_analysis = "music/music.json"
# edit = "cuts/{cut}/edit.json"   decisions = "cuts/{cut}/decisions.json"   timeline = "cuts/{cut}/{cut}"
# alternatives = "cuts/{cut}/alternatives.json"   color = "analysis/color.json"
# exports = "exports"   review = ".cutter/review/data"   htpasswd = ".cutter/review/htpasswd"
# review_media = ""      the rendered cuts of the review app (cuts/<cut>/); empty: inside review, next to the comments
```

Only the keys you set are needed; everything else has the defaults shown. `cutter.json` with the same structure
works as well. Cut names: `cut1`, `cut2`, …; commands also accept `3` for `cut3`.

## clips.json

`analysis/clips.json`, a list in recording order:

```json
{"id": "C001", "file": "C0012.MP4", "media": {"root": "cam", "path": "100MEDIA/C0012.MP4"},
 "rel_path": "cam/100MEDIA/C0012.MP4", "path": "/Volumes/Card A/DCIM/100MEDIA/C0012.MP4",
 "duration": 16.0, "created": "2024-05-01 09:00:00", "created_source": "metadata", "device": "…",
 "width": 1920, "height": 1080, "rotation": 0, "fps": 25.0, "fps_raw": "25/1", "vfr": false,
 "has_video": true, "has_audio": true, "audio_rate": 48000, "audio_channels": 2,
 "words": 120, "speech_seconds": 41.5, "gps": {"lat": 48.1374, "lon": 11.5755, "alt": 519.0}}
```

`media` is what counts: the clip's footage root and its path below it. `path` is informational; timelines are always
built from `media` and the roots in cutter.toml. `created_source` is `metadata`, `filename` or `file_date`. `gps` is
there when the file names the place (phones do).

### Stand-ins and sidecars

A tool that cannot hand over the original footage (a web app whose browser makes small copies, say) gives cutter a
folder of **stand-ins** in `[media]` and a folder of **sidecars** in `[standins] sidecars`. cutter analyses the
stand-ins, but `clips.json` states what the *originals* are, so that timelines built from it match the originals.

For a stand-in `<media root>/<name>.mp4` the files are `<sidecars>/<name>.json` (required), `<name>.gpmd` (the GoPro
GPMF samples of the original back to back, as `ffmpeg -map 0:<gpmd track> -c copy -f data` writes them) and
`<name>.srt` (DJI flight data), both optional. A stand-in without a valid sidecar is skipped with a message.

The sidecar (format `cutlery.sidecar/1`, written by [Cutlery](https://github.com/aaronblankenburg/cutlery), whose
`docs/sidecar.md` describes every field) must have `file`, `rel_path`, `size`, `duration` (the video's; `format_duration`, the whole file's as ffprobe
reports it, is the clip's `duration` when present), `fingerprint` and, for
video, `video.{codec,width,height,rotation,r_frame_rate,avg_frame_rate}`. `video.rotation` is degrees clockwise;
clips.json keeps ffprobe's convention (`rotation` 270 for a clip that is turned 90° clockwise).

What changes in `clips.json`: `file`, `rel_path`, `size_mb`, `duration`, `created` (the original's name first, then its
tags), `codec`, `width`, `height`, `rotation`, `fps*`, `timecode`, `device`, `gps` and the audio fields are the
original's. `path` and `media` are the stand-in's, because that is the file every tool reads. Added:

```json
"standin": {"width": 640, "height": 360},
"original": {"name": "IMG_4821.MOV", "size": 1234567890, "mtime": "2024-06-01T10:15:00Z",
             "fingerprint": {"size": 1234567890, "head_sha256": "…", "tail_sha256": "…"}, "bit_depth": 10, "codec_string": "hvc1.2.4.L153.B0"},
"telemetry_files": {"gpmd": "/…/IMG_4821.MOV.gpmd"}
```

Clip IDs follow the fingerprint, so a renamed original keeps its ID. Stand-ins never get proxies (`proxies.needed`),
and `low_detail` compares clips of the same *analysed* size. A stand-in whose length differs from the original's by
more than 2 frames + 0.02 s (the proxy contract) is reported.

## Transcripts

`analysis/transcripts/C001.json`: `{"id", "file", "language", "language_probability", "segments": [{"start", "end",
"text", "words": [{"start", "end", "word", "probability"}]}]}` – times in seconds of the clip.

## Image and sound analysis

`analysis/quality.txt` is the part for reading: a few lines per clip (usable ranges, flaws, camera moves, cuts inside
the clip, people and objects, sound, place, other takes). The numbers behind it are in `analysis/image/C001.json`:

```json
{"id": "C001", "version": 3, "duration": 34.2, "units": {…},
 "usable": [{"start": 2.4, "end": 11.0, "camera": "static", "steady": true}, …],
 "flaws":  [{"start": 0.0, "end": 2.4, "why": ["shaky"]}, …],
 "camera": [{"start": 0.0, "end": 11.2, "move": "static", "speed": 0.2, "shake": 0.1},
            {"start": 11.2, "end": 15.0, "move": "pan_right", "speed": 12.4, "shake": 0.4}, …],
 "scenes": [{"start": 0.0, "end": 20.3}, …],
 "audio": {"lufs": -23.1, "peak_db": -1.2, "channels": 2, "speech": [{"start": 2.0, "end": 9.6}], "wind": [],
           "clipping": [], "silence": []},
 "telemetry": {"source": "gopro", "gps": {"lat": 36.8, "lon": 10.18, "alt": 12.0}, "track": [[0.0, 36.8, 10.18, 12.0, 18.0], …],
               "distance_m": 340, "speed_kmh": {"mean": 18.0, "max": 31.5}, "gyro": [{"t": 0.0, "turn": 4.1, "shake": 9.3}, …]},
 "faces": [{"seconds": [3, 4, 5, 9], "faces": 4, "size": 0.22, "thumb": "faces/C001_0.jpg", "vector": [0.031, …]}],
 "per_second": [{"t": 0, "sharpness": 212.4, "exposure": {"mean": 118.0, "too_dark": 0.0, "clipped": 0.02},
                 "color": {"warmth": 0.06, "saturation": 0.31}, "pan": 0.3, "pan_x": -0.2, "pan_y": 0.1, "zoom": 0.0,
                 "shake": 0.2, "motion": 1.1, "faces": 1, "face": 0.22, "people": 2, "person": 0.8,
                 "objects": {"car": 1}, "shot": "medium close-up", "level": -24.0, "wind": 0.0}, …],
 "summary": {"usable_s": 25.2, "steady_s": 19.0, "camera": {"static": 22.0, "pan_right": 3.8}, "people_max": 2,
             "shots": ["medium close-up"], "objects": ["car"], "speech_s": 7.6, "similar": ["C002"], …}}
```

- **Times** are seconds of the clip. **Speeds** are % of the picture width per second; `units` in the file explains
  every measure.
- **`usable`** are the ranges worth cutting from: sharp, not shaky, not a whip pan, neither black nor blown out, at
  least one second long. `steady` means as calm as a tripod. In a clip that is unsteady throughout (an action
  camera in the hand) only the worst moments count as shaky, so the calmer parts remain; their `camera` says `shaky`. **`flaws`** is the rest with the reason: `soft`, `shaky`,
  `whip`, `dark`, `clipped`, or `short` for a good moment under a second. Sharpness depends on the subject, so
  `soft` means clearly softer than the sharp frames a few seconds around it, or far softer than the clip's sharp
  frames; a clip that is soft throughout shows up as `low_detail` in the index instead, compared with the other
  clips of its size.
- **`camera`** segments are `static`, `handheld`, `shaky`, `pan_left`, `pan_right`, `tilt_up`, `tilt_down`,
  `push_in` and `pull_out` (a zoom or the camera itself moving), found to about half a second.
- **`shot`** comes from the largest face (`close-up`, `medium close-up`, `medium`), else from the largest person
  (`full`, `wide`, `very wide`). Objects are the 80 everyday classes of COCO.
- **`audio`:** `speech` is voice activity, not a transcript. `wind` needs two microphones (low rumble that the
  channels do not share); cameras that record one microphone get `rumble` instead, which is wind or an engine.
- **`telemetry`** has the place from the file's metadata, and from GoPro (GPMF) and DJI (flight log `.SRT`) the
  track as `[second, lat, lon, alt, km/h]` and, for GoPro, the gyroscope in degrees per second.
- **`faces`** are the people of the clip, told apart by their faces: the seconds each is seen in, a picture
  (`analysis/image/faces/`) and the vector that describes the face. Who they are across clips is in `people.json`
  (below).
- **`similar`** names clips recorded within half an hour that look alike: probably other takes of the same shot.
- `horizon_tilt` (degrees) appears only when one long, nearly level line stays put through the clip.

`analysis/image/_index.json` holds every clip's `summary`. Files of an older `version` (before these fields) are
measured again by the next `cutter analyze`.

### Proxies

`cutter analyze --proxies` writes `analysis/proxies/C001.mp4` for every clip that is larger than 720 lines or not
H.264: the same frames at the same times in H.264 at 720 lines (a key frame every half second, no B-frames), the
first sound track as AAC, upright where the footage is stored rotated, without the footage's metadata. Clips that
are small H.264 already get none and are used as they are. `analysis/proxies/proxies.json` says which is which:

```json
{"version": 1, "height": 720,
 "clips": {"C001": {"file": "C001.mp4", "width": 1280, "height": 720, "duration": 34.2, "for_height": 720,
                    "source": {"size": 412345678, "mtime": 1714546800}},
           "C002": {"file": null, "for_height": 720, "source": {…}}}}
```

A proxy stands in for its clip as long as the footage file has the size and date in `source`; a proxy that did not
come out as long as its clip is thrown away. Where a proxy exists, the image analysis reads the picture from it
(`"picture_from": "proxy 1280x720"` in `analysis/image/C001.json`; sound and track still come from the footage),
and `cutter timeline <cut> --proxies` builds a draft that plays the proxies. The measurements agree with those
from the footage to within a few tenths of a second; faces count for who is who when they are 48 pixels high in
the picture that is read, so in a proxy of 4K footage small faces are found but not told apart.

### people.json

`analysis/people.json` lists the people found by their faces, most seen first; `analysis/people.jpg` shows a row
of faces for each, and `quality.txt` says who is in a clip and when (`who`).

```json
{"version": 1, "sheet": "people.jpg", "unassigned_faces": 7,
 "people": [{"id": "P1", "name": "Ada", "seconds": 148, "clips": {"C001": [{"start": 3.0, "end": 12.0}], …},
             "pictures": ["faces/C001_0.jpg", …], "vectors": [[0.031, …]]}]}
```

- A face is compared with others when it is at least 48 pixels high and clearly a face, once a second. Two faces
  are the same person when their vectors (SFace, 128 numbers) are similar enough; groups of faces are joined when
  their members are similar on average. A face seen for less than three
  seconds in the whole project gets no ID (`unassigned_faces`).
- `cutter people name P1 "Ada"` names a person; `cutter people merge P1 P4` says two IDs are one person (the first
  stays). IDs, names, merges and splits survive a new analysis, because a person is found again through `vectors`:
  one for every group of faces filed under it, and such a group stays with that person from then on. The
  grouping errs on the careful side: next to the people it is sure of, expect a few IDs with one or two dark,
  blurred or turned-away faces of people who already have an ID, and the same person with and without sunglasses
  or a helmet may get two IDs (merge them). Faces in footage that was recorded upside down are found but not
  recognised. Check `people.jpg` before relying on it.
- Two people who look alike, or small and blurred faces, may share an ID. `cutter people split P3 C004 C007_1`
  takes the faces of P3 in clip C004 and the picture C007_1 (the names under the pictures of `people.jpg`; all of
  a person's are in `cutter people list --json`) out of P3 and gives them a new ID; `--to P1` files them under P1
  instead. Like a merge this survives a new analysis: a group of faces whose vector a person holds stays with that
  person. What cannot be taken apart is a single picture: the faces of one clip that the analysis put into one
  group move together.
- The vectors are biometric data. The file stays in the project folder; it is not part of the review data and is
  never deployed. `recognize_faces = false` in `[image_analysis]` leaves the step out.

## music.json

`music/music.json`, keyed by file name:

```json
{"track.mp3": {"file": "track.mp3", "duration": 184.2, "bpm": 120.2, "beats": [0.53, 1.02, …],
               "downbeats": [0.53, 2.52, …], "phase": 0, "scores": [2.9, 1.2, 1.4, 0.4], "conf": 1.64, "lufs": -19.8}}
```

`phase` is the index of the first downbeat among the beats; `scores` are the evidence for each phase, `conf` the
margin of the best one.

## edit.json

`cuts/<cut>/edit.json` is the cut. Example (shortened from the demo):

```json
{
 "title": "Demo (cut1)", "fps": 25,
 "sections": [
  {"name": "Opening", "shots": [
    {"nr": 1, "clip": "C001", "in": 1.0, "out": 4.9, "kind": "picture", "note": "test pattern, slow start"}]},
  {"name": "Talk", "shots": [
    {"nr": 4, "clip": "C004", "in": 2.0, "out": 10.0, "kind": "dialogue", "note": "the tone that speaks",
     "audio": {"chain": "mild", "highpass_hz": 80, "noise_reduction": 20, "speech_mix": 0, "compressor": true},
     "subtitles": [{"from": 0.3, "to": 3.5, "text": "This is the demo talking."}],
     "cutaways": [{"clip": "C005", "in": 2.0, "out": 4.0, "at": 3.0, "note": "cutaway over the talk"}]}]},
  {"name": "End", "shots": [{"nr": 8, "clip": "TXT", "in": 0.0, "out": 3.0, "kind": "black", "note": "end card"}]}
 ],
 "music": [{"cue": "M1", "title": "Click 120", "artist": "cutter demo", "license": "CC BY 4.0",
            "file": "music/click_120bpm.wav", "from": {"section": "Opening", "edge": "start"},
            "to": {"section": "End", "edge": "end"}, "source_start": 0.5, "fade_out_s": 2.0}],
 "markers": [{"name": "Talk starts", "section": "Talk", "edge": "start", "color": "GREEN"}],
 "credits_music_lines": ["Click 120 – cutter demo – CC BY 4.0"]
}
```

**Shot**

| Key | Meaning |
|---|---|
| `nr` | running number in the cut (1, 2, …) |
| `clip` | clip id from clips.json; `TXT` for black or text; ids of `external_clips` for clips outside the footage |
| `in`, `out` | seconds in the clip |
| `kind` | `dialogue`, `picture`, `ambience`, `title` or `black` |
| `note` | what the shot is for (shown in the review app) |
| `audio` | dialogue: `chain` (`mild`, `gentle`, `full`), `highpass_hz`, `noise_reduction` (0–100), `speech_mix` (0–100), `compressor`, `music_lower`; set by `timeline`: `gain_db`, `reason` |
| `subtitles` | `[{"from", "to", "text"}]`, seconds from the start of the shot |
| `cutaways` | `[{"clip", "in", "out", "at", "note"}]`: picture of another clip over this shot from `at` seconds (on V4) |
| `block` | a group of shots (e.g. `"fast cuts"`) for retime rules |
| `tag` | a name for anchors and retime rules |
| `nr_previous` | the shot's number in the previous cut (written by plan.py) |
| `free_timing` | `true`: retime leaves the shot alone |
| `out_limits` | `[min, max]` for the out point when retiming |
| `fade_in_s`, `stabilize` | fade-in at the start; stabilise in FilmCraft (`true`) |
| `color` | `false`: no colour correction for this shot; `{"exposure", "temperature", "tint", "blacks"}`: these values instead of the suggestion (see Colour) |
| `focus` | where the picture is cut when its shape differs from the frame: `[x, y]` (shares of width and height that come to the middle), `x` alone, or `"centre"`; also on a cutaway. Default: found by reframe (see Frames) |

**Music cue:** `cue` (M1, M2 …), `title`, `artist`, `license`, `file` (relative to the project, or found by
`title`/`search` in `timeline.music_dirs`), `from` and `to` (anchors), `source_start` (seconds into the track at
`from`), `fade_out_s`, `note`. Anchors: `{"section": "Talk", "edge": "start"|"end"}`, `{"tag": "finish", "edge": …}`,
`{"time": 12.5}` or `{"end": true}`.

**Markers:** `name`, an anchor (`section`/`edge`, or `time`), optional `duration` and `color`.

**Other top-level keys:** `frame` (the cut's picture format: `"16:9"`, `"9:16"`, `"4:5"`, `"3:4"`, `"1:1"`,
`"2:3"`, `"4:3"`, `"21:9"` or `"WIDTHxHEIGHT"`; default `[timeline] frame`), `external_clips` (`{"E1": {"file", "duration"}}`), `credits_music_lines` (lines for
`{music_lines}` in the credits), `intro` (map intro: `map`, `route`, `overview` images, `map_start`, `map_duration`,
`map_zoom` with `start_scale`, `start_center`, `target_center`, `route_center`, `zoom_from`, `zoom_to`, `labels` …),
`end_text` (text cards over the ending: `source`, `style`, `cards: [{"from", "to", "text"}]`, `music_dip_db`),
`audio_note`, `note`.

## decisions.json

`cuts/<cut>/decisions.json` explains a cut: what changed since the previous cut and why. The review app shows the
entries in violet at their places.

```json
{"format": 2, "version": "cut2", "compared_to": "cut1", "date": "2024-05-09", "title": "Changes in cut2",
 "intro": "Shorter opening, music under the talk lowered.",
 "entries": [
  {"id": "overview", "title": "cut2 at a glance", "text": "…"},
  {"id": "opening", "nr": 2, "nr_to": 3, "title": "Opening shorter", "text": "Shots 2 and 3 are 1.5 s shorter.",
   "why": "Two reviewers found the start slow.", "question": "Better now?",
   "refs": [{"version": "cut1", "id": 12, "person": "Alice", "t": 8.2, "text": "Too slow here"}]}]}
```

An entry with `"kind": "assumption"` records something the agent decided without asking (non-interactive mode,
skills/video-editing/references/non-interactive.md); the review shows it as such.

Place an entry with `t`/`t_to` (seconds in this cut), `nr`/`nr_to` (shots of this cut), `cut_before` (the cut before
shot n, ±2 s) or `music` (a cue). Entries without a place are general notes. Every entry needs a unique `id` and a
`title`; `refs` point to the comments it answers.

## alternatives.json

`cuts/<cut>/alternatives.json` (optional) lists the places of a cut where the agent weighed other clips or ranges –
only real candidates, at most 3 per place. `cutter review-data` renders a preview of every option and puts the places
into timeline.json; the review app shows them next to the cut, where reviewers choose one or keep the cut as it is.

```json
{"places": [
  {"id": "arrival", "nr": 12, "clip": "C031", "reason": "Two takes of the arrival at the camp; the one in the cut is shorter.",
   "options": [{"clip": "C047", "in": 3.0, "out": 9.5, "why": "The car arriving, steadier picture"},
               {"clip": "C012", "in": 20.0, "out": 26.0, "why": "Wide shot with the sunset"}]}]}
```

A place is a shot (`nr`, optionally to `nr_to`) or a time of the cut (`t`, `t_to`); `id` (letters, digits, `-`, `_`)
is unique in the file. `clip` names the clip in the cut at that place: shot numbers move while a cut is planned, so
when shot `nr` does not show `clip`, the nearest shot that does is taken (and logged); without `nr`, the one shot with
that clip. An option names `clip` (ID in clips.json), `in` and `out` in seconds of the clip, and `why`.
The previews (`alt-<id>-<n>.mp4`, at most 360 lines with sound, at most 30 s, and `alt-<id>-<n>.jpg`) are taken from
the proxy of the clip if there is one, else from the clip; unchanged options are not rendered again.

A reviewer's choice is a comment with `"alternative": "<id>/<n>"` (use option n instead) or `"<id>/keep"` (keep the
cut as it is), see the [feedback export](#feedback-export); the next cut applies it.

## selects.json

The user's own selection from viewing the footage (written by a tool such as Cutlery, read by the video-editing
skill): favourites to use where they fit, rejects to leave out.

```json
{"version": 1, "selects": [
  {"id": "s1", "clip": "C005", "file": "2012-07-14 09-26-28 DSC_0669.MOV", "kind": "favorite",
   "in": 3.2, "out": 8.0, "note": "the sunrise", "created": "2026-11-19T08:12:00+01:00"},
  {"id": "s2", "clip": "C017", "file": "GOPR0175.MP4", "kind": "reject", "in": null, "out": null, "note": ""}]}
```

`clip`: ID in clips.json (`file` for people reading it); `kind`: `favorite` or `reject`; `in`/`out`: seconds in the
clip, both null for the whole clip; `note`: free text (the user's language).

## plan.py

`cuts/<cut>/plan.py` turns the previous cut's edit list into this cut's: `cutter plan cut3` runs
`python plan.py <cuts/cut2/edit.json> <cuts/cut3/edit.json>`. `cutter plan cut3 --new` writes the template, which
copies every shot, keeps its old number as `nr_previous`, renumbers, and shows commented examples (remove, shorten,
subtitle). Each change carries a comment naming the person, the comment number and why.

## Timelines

`cutter timeline <cut>` writes `cuts/<cut>/<cut>.otio` (OpenTimelineIO), `<cut>.fcp7.xml` (FCP 7 XML) and
`<cut>.srt`. Tracks: V1 picture, V2 map, V3 route, V4 cutaways, V5 overview, A1 dialogue, A2 music. Every item
carries metadata under the key `timeline.otio_namespace` (default `cutter`): `nr`, `section`, `kind`, `note`, `tag`,
`source_resolution`, `scale_percent`, `offset`, `audio`, `cutaway_of`. The sequence has the cut's frame (see Frames;
1920×1080 by default); clips are scaled to fill it. Media references are `file://` URLs built from the footage roots.

## Frames

The picture format of a cut (module `frame`): `edit.json` `"frame"`, else `[timeline] frame`, else 16:9.

| Frame | Size | For |
|---|---|---|
| `16:9` | 1920×1080 | landscape: YouTube, TV, presentations |
| `9:16` | 1080×1920 | vertical full screen: Instagram Reels and Stories, TikTok, YouTube Shorts |
| `4:5` | 1080×1350 | portrait feed post: Instagram, Facebook |
| `3:4` | 1080×1440 | portrait: Instagram profile grid, phone photos |
| `1:1` | 1080×1080 | square feed post |
| `2:3` | 1080×1620 | tall pin: Pinterest |
| `4:3` | 1440×1080 | classic TV, old footage |
| `21:9` | 2560×1080 | cinema widescreen |
| `WxH` | e.g. `3840x2160` | any other size (even numbers, 128–8192) |

- **Footage** fills the frame and is cut off where its shape differs (a landscape clip in 9:16 keeps about a third of
  its width). **Reframing** (module `reframe`) decides which part stays: faces first, then people, then animals,
  found once a second on the analysed file (stand-ins are enough; needs the face/object models of `cutter analyze`)
  and stored in `analysis/reframe.json` (`{"version": 1, "clips": {"C005": {"size", "mtime", "frames": [[t, [[label,
  cx, cy, w, h, score], …]], …]}}}`). Per shot (and cutaway) the window is placed where it holds the most subject
  weight between `in` and `out`; it does not move within the shot. A shot's `focus` in edit.json wins; without
  subjects the middle stays. `[timeline] reframe = false` turns the search off (`focus` still counts). The plan is
  kept in `cuts/<cut>/work/reframe_plan.json`: `{"frame", "on", "shots": {"<nr>": {"focus", "offset", "why",
  "shown", "cutaways": [… | null]}}}`, `offset` in frame pixels from the middle.
- **FilmCraft** gets a sequence in the frame's size and the offset as each clip's position; the **review render**
  crops at the same place. The exchange formats (FCP 7 XML, FCPXML, OTIO) get the sequence size and keep the footage
  centred, with a cyan marker "Reframe shot 12" saying where the subject is.
- **Titles, credits, end-text cards** stay laid out for 1920×1080 in filmcraft.json and edit.json and are moved into
  the frame: positions keep their share of width and height, sizes follow the shorter side, a text that would not fit
  the width gets smaller (the credits as one block). In 9:16 nothing is placed in the top 14 % and the bottom 22 %,
  where the apps put their buttons and captions; subtitles keep that bottom margin too.
- **Map intro**: `cutter map overview` with `"frame": "9:16"` in its config frames the country for that format.
- **Review**: `review.mp4` is 720 lines on the shorter side (720×1280 for 9:16), thumbnails 108 lines;
  `timeline.json` has `"frame": {"name", "width", "height"}` when it is not 16:9, and the player takes that shape.
  `[filmcraft] export_height` counts the shorter side too (360 gives 360×640).

### Packages for other editors (`cutter export`)

`cutter export cut3 --to premiere|fcp|resolve|filmcraft --target-os windows --target-root 'D:\Videos\Iceland'
[--relink map.json] --out DIR` writes into DIR what goes into `<root>/_cutlery` on the editing computer: the timeline
`<title>_<cut>.<ext>`, `<title>_<cut>.srt`, `music/` with `LICENSES.txt`, `titles/` (titles, credits and end-text cards
of filmcraft.json as transparent PNG; not for FilmCraft, which makes its own) and `maps/`. Clips are at
`<root>/<rel_path>` (clips.json), written as file URLs the way editors expect them (`file://localhost/D:/…`, UNC,
percent-encoded UTF-8; `--normalize nfc|nfd` for macOS). The timeline is the one of `cutter timeline`, sized for the
originals (never for stand-ins or proxies), with the clips' own metadata: start timecode, NTSC rates, duration and
size. `--relink` maps old to new `rel_path`s of originals renamed since the analysis.

| `--to` | Timeline | What is carried over |
|---|---|---|
| `premiere` | `.xml`, FCP 7 XML (xmeml 4, `fcp7.py`) | picture size (Basic Motion), dialogue levels, music ducking as volume keyframes, titles as PNG with opacity keyframes; stabilisation, audio cleanup and the decisions of decisions.json as sequence markers |
| `fcp`, `resolve` | `.fcpxml`, FCPXML 1.10 (`fcpxml.py`, checked against Apple's DTD) | the same, plus stabilisation (`adjust-stabilization`) and noise reduction (`adjust-noiseReduction`) applied, subtitles as captions; markers sit on the clip they start in |
| `filmcraft` | `.fcproj` | the project `cutter filmcraft build` makes (titles, credits, map, audio chain, stabilisation, ducking, captions), built from a timeline that points to the originals; FilmCraft keeps the paths of the media that are offline on the server and finds them on the editing computer; the files in the package (music, maps) are referred to relative to the project file, so they are found wherever the package is unpacked |

DaVinci Resolve gets FCPXML rather than FCP 7 XML or OTIO: it keeps start timecode, frame rates, volume keyframes,
markers with notes and captions, and Resolve's FCPXML import is the one Blackmagic maintains for round trips with
Final Cut. OTIO has no standard place for keyframes or effects.

## filmcraft.json

Optional; named by `[filmcraft] config` in cutter.toml. All keys are optional and merge over the defaults
(`src/cutter/filmcraft.py`, `DEFAULTS`):

| Key | Meaning |
|---|---|
| `titles` | `[{"text", "font", "size", "position": [x, y], "time", "duration", "style": {…}, "opacity": [[t, %], …]}]` |
| `credits` | `{"duration", "styles": {name: {font, fill_color, shadow …}}, "layers": [{"text", "style", "size", "position", "line_height", "align"}]}`; `{music_lines}` in a text becomes `credits_music_lines` |
| `audio_chain` | `highpass_hz`, `compressor` (`comp_threshold`, `comp_ratio`, `comp_attack`, `comp_release`), `limiter` (`lim_on`, `lim_threshold`) |
| `music` | `base_lufs`, `under_lufs`, `low_lufs`, `fade_out_s`, `ramp_down_s`, `ramp_up_s` |
| `captions` | `name`, `language`, `style` (`font`, `fontStyle`, `size`, `color`, `background`, `backgroundColor`, `align`, `anchor`, `margin`) |
| `map` | the map zoom of an intro: `background_nr`, `route_opacity`, `fonts`, `point`, `outline` … |
| `end_text`, `stabilize`, `marker_colors`, `export` | text card ramps; maximum scale; marker colours by name; export `preset` and `settings` |

## timeline.json (review app)

`.cutter/review/data/cuts/<cut>/timeline.json`, written by `cutter review-data`:

```json
{"format": 2, "version": "cut1", "title": "Demo (cut1)", "fps": 25, "duration": 29.0,
 "sections": [{"name", "start", "end"}],
 "shots": [{"nr", "section", "kind", "clip", "file", "source_in", "source_out", "start", "end", "note", "text",
            "subtitles", "cutaways", "sentences", "audio_db", "music_lower", "block", "tag", "nr_previous"}],
 "edit_points": [{"t", "before", "after", "kind": "shot|cutaway_in|cutaway_out"}],
 "music": [{"cue", "title", "artist", "license", "file", "start", "end", "source_start", "fade_out_s", "bpm", "note"}],
 "subtitles": [{"start", "end", "text"}], "markers": [{"name", "t", "duration"}],
 "decisions": {"title", "intro", "compared_to", "date", "entries": [{"id", "title", "t", "t_to", "text", "why", "question", "refs", "kind"}]},
 "alternatives": [{"id", "nr", "nr_to", "t", "t_to", "reason", "current": {"clip", "in", "out"},
                   "options": [{"n", "clip", "in", "out", "why", "file", "poster"}]}],
 "color": {"on", "in_video", "shots": {"<nr>": {"suggested", "on", "video", "text", "still"}}},
 "video": {"file", "width", "height", "fps", "duration", "numbers_burned_in"},
 "thumbnails": {"vtt", "interval", "width", "height", "count"}, "poster": "poster.jpg",
 "source": {"edit", "otio", "video", "video_kind", "decisions", "alternatives"}, "created": "…"}
```

`"frame": {"name", "width", "height"}` is there for cuts that are not 16:9 (see Frames).

Next to it: `review.mp4` (at most 720 lines on the shorter side; an export of 720 lines or fewer keeps its size; shot numbers are burned into rough renders), `thumbs_NN.jpg` with `thumbs.vtt`, and
`poster.jpg`, the before/after stills of the colour suggestion (`color-<nr>.jpg`), and the previews of the alternatives (`alt-<id>-<n>.mp4`/`.jpg`; `file`/`poster` are null when the clip
was not found). In `alternatives`, `t`/`t_to` are the place in this cut, `current` is what the cut uses there.
`data/project.json` holds the title, reviewers, the agent's name and the UI language; an app that embeds the review
frontend may add `"person"` (fixed reviewer name), `"submit": false` (no “Send feedback”) and `"embedded": true`
(no brand in the header, full width; the page then posts `{source: "cutter-review", cut, open, entries}` to its parent
window whenever the cut or its comments change). Embedded on a wide frame (1000 px and more) the page fits the
frame's height, the video getting smaller, and only the list of comments scrolls; on a narrower one it posts
`{source: "cutter-review", height, wide: false}` so the parent can make the frame as tall as the page (`height` 0 and
`wide: true` on a wide frame). `?cut=<id>` in the page URL opens that cut.

## Colour

A colour correction per shot, suggested from the footage and applied in FilmCraft (module `color`):

- **Measured** once per clip (one frame per second of the analysed file; stand-ins and proxies are enough) into
  `analysis/color.json`: `{"version": 1, "clips": {"C005": {"size", "mtime", "frames": [[t, p02, p50, p98, r, g, b,
  neutral], …]}}}` – luma percentiles and the mean colour of the near-neutral pixels with their share.
- **Suggested** per shot from the frames between its `in` and `out`: exposure towards a normal middle (not for night
  pictures or pictures with bright parts), white balance from the near-neutral parts (only with enough of them, not
  in low light, warm light cooled only half as much as a blue cast is warmed), deeper blacks for hazy pictures. Shots
  of one camera within a section are pulled halfway towards their common value. Units of FilmCraft's Color Grading
  effect: `exposure` in stops, `temperature` (warmer +), `tint` (magenta +), `blacks`.
- **Applied** by `cutter filmcraft build` and `cutter export --to filmcraft` as a Color Grading effect per shot and
  cutaway; the plan used is kept in `cuts/<cut>/work/color_plan.json`. The other export formats get a pink marker
  per corrected shot ("Colour shot 12: exposure +0.4, temperature +12").
- **Switched off** with `[color] auto = false`, a shot's `"color": false` in edit.json, or in the review: entries
  with `"color": "all/off"`, `"all/on"`, `"<nr>/off"`, `"<nr>/on"` (one per reviewer and target; the latest of
  anyone wins). The export follows these switches at once, the review video from the next build. The next cut takes
  over the switches of the previous cut's review (a shot's by its `nr_previous`) unless its own review changes them.

## Feedback export

`cutter feedback fetch` (or `GET /api/export?cut=…`) writes `.cutter/review/feedback-<cut>.json`:

```json
{"format": 2, "cut": "cut1", "title": "…", "duration": 29.0, "exported": "…",
 "people": [{"name": "Alice", "entries": 3}], "counts": {"total": 3, "open": 2, "done": 1, "rejected": 0},
 "actions": {"shorter": "Shorter", …},
 "entries": [{"id": 12, "t": 8.2, "t_to": null, "nr": 2, "nr_to": null, "clip": "C002", "source_t": 3.1,
              "cutaway": null, "section": "Opening", "music": "M1", "edit_point": null, "action": "shorter",
              "action_text": "Shorter", "text": "Too slow here", "reaction": null, "alternative": null,
              "person": "Alice", "status": "open", "reply": null, "created": "…", "changed": "…"}]}
```

An entry with `"t": null` is about the whole film (no shot, clip, section or music; listed first). `"alternative"`
is a reviewer's choice among the alternatives of the cut ([alternatives.json](#alternativesjson)): `"<id>/<n>"`
means use option n of that place, `"<id>/keep"` keep the cut as it is; one per reviewer and place.

`cutter feedback wait` prints the same result as `fetch` plus the request it took, when a reviewer sent the
feedback off with the button in the review app (`"request": null` when nothing was sent off):

```json
{"request": {"id": 3, "cut": "cut1", "person": "Alice", "created": "…", "taken": "…"},
 "cut": "cut1", "file": "…/.cutter/review/feedback-cut1.json", "counts": {"total": 3, "open": 2, "done": 1, "rejected": 0}}
```

Actions: `remove`, `shorter`, `longer`, `cut_earlier`, `cut_later`, `other_shot`, `music_softer`, `music_louder`,
`unclear`, `like`, `question`. Status: `open`, `done`, `rejected`.

## Replies

Input of `cutter feedback reply`: a list (or `{"entries": [...]}`) of `{"id": 12, "status": "done"|"rejected"|"open",
"reply": "…"}`; replies have at most 2000 characters.

## Maps

`cutter map overview --config map.json` and `cutter map route --config route.json` draw maps for an intro. Paths in
the config are relative to the config file.

| Key | overview | route |
|---|---|---|
| `output` | PNG to write | PNG to write |
| `base_map` | the map the zoom ends on (water black, land transparent) | – |
| `topojson`, `object`, `country`, `neighbours` | Natural Earth TopoJSON (e.g. world-atlas `countries-10m.json`, public domain), its object name, the country to frame, neighbours to draw | – |
| `georef_start` | two known points to start the georeferencing from | – |
| `factor`, `size`, `downsample` | scale relative to the base map, picture size, quality | `size` |
| `stages`, `origin` | – | stage images and their offsets on the base map |
| `labels`, `point`, `fonts`, `colors` | place names (written to `meta` as text layers, not into the picture) | labels drawn into the picture |
| `meta` | JSON with the georeference and label positions | – |
| `route_image` | – | optional finished route image |

## Files from earlier versions

The first version of this pipeline used German keys. Every reader passes its input through `src/cutter/legacy.py`,
which recognises the old format and renames keys and enumerated values on the fly; content (notes, subtitles,
names) is never translated, unknown keys pass through. `cutter migrate <file>…` rewrites files permanently (the
original is kept as `<file>.bak`). The review server migrates an old comment database once at start-up, after a
backup copy (`feedback.sqlite.backup-before-migration-<time>`), and reads old `data/fassungen/` folders.

Timelines built from an old edit list write their OTIO metadata with the old keys, so they are byte-identical to
the timelines written back then.

The complete mapping (generated from `legacy.py`, checked by `tests/test_docs.py`):

**edit.json**

| Where | Old → new |
|---|---|
| top level | `titel` → `title`, `hinweis` → `note`, `externe_clips` → `external_clips`, `musik` → `music`, `marker` → `markers`, `abspann_musikzeilen` → `credits_music_lines`, `ton_hinweis` → `audio_note`, `schlusstext` → `end_text` |
| shot | `art` → `kind`, `notiz` → `note`, `ton` → `audio`, `untertitel` → `subtitles`, `einschnitte` → `cutaways`, `baustein` → `block`, `nr_vorher` → `nr_previous`, `takt_frei` → `free_timing`, `einblenden_s` → `fade_in_s`, `stabilisieren` → `stabilize`, `out_grenzen` → `out_limits`, `teil` → `part`, `schnitt_min` → `cut_min`, `schnitt_max` → `cut_max` |
| shot `kind` values | `dialog` → `dialogue`, `bild` → `picture`, `atmo` → `ambience`, `titel` → `title`, `schwarz` → `black` |
| shot `audio` | `grund` → `reason`, `kette` → `chain`, `hochpass_hz` → `highpass_hz`, `rauschminderung` → `noise_reduction`, `sprache_mix` → `speech_mix`, `kompressor` → `compressor`, `musik_tiefer` → `music_lower` |
| `audio.chain` values | `voll` → `full`, `sanft` → `gentle` |
| subtitles | `von` → `from`, `bis` → `to` |
| cutaways | `ab` → `at`, `notiz` → `note` |
| anchors (music `from`/`to`) | `zeit` → `time`, `ende` → `end`, `abschnitt` → `section`, `kante` → `edge` |
| `edge` values | `ende` → `end` |
| music cue | `titel` → `title`, `kuenstler` → `artist`, `lizenz` → `license`, `suche` → `search`, `datei` → `file`, `von` → `from`, `bis` → `to`, `quelle_start` → `source_start`, `notiz` → `note`, `taktanfang_erster` → `first_downbeat`, `ausblenden_s` → `fade_out_s` |
| markers | `zeit` → `time`, `dauer` → `duration`, `farbe` → `color`, `abschnitt` → `section`, `kante` → `edge`, `ende` → `end` |
| external clips | `datei` → `file`, `dauer` → `duration` |
| optional shots | `notiz` → `note`, `passt_nach` → `fits_after` |
| intro | `karte` → `map`, `etappe` → `route`, `ueberblick` → `overview`, `karte_start` → `map_start`, `karte_dauer` → `map_duration`, `karte_ausblenden` → `map_note`, `titel_text` → `title_text`, `titel_von` → `title_from`, `titel_bis` → `title_to`, `karte_position` → `map_position`, `ueberblick_notiz` → `overview_note`, `karte_zoom` → `map_zoom` |
| intro map position | `notiz` → `note` |
| map zoom | `start_skalierung` → `start_scale`, `start_mitte_karte` → `start_center`, `ziel_mitte_karte` → `target_center`, `etappe_mitte_karte` → `route_center`, `beschriftung_bis` → `labels_until`, `beschriftung_weg` → `labels_gone`, `zoom_von` → `zoom_from`, `zoom_bis` → `zoom_to`, `ausblenden_von` → `fade_from`, `ausblenden_bis` → `fade_to`, `ueberblendung_skalierung` → `crossfade_scale`, `hintergrund_weiss_bis` → `background_white_until`, `hintergrund_normal` → `background_normal`, `ueberblick` → `overview`, `beschriftung` → `labels`, `georeferenz` → `georeference`, `notiz` → `note` |
| overview | `origin_karte` → `origin`, `faktor` → `factor`, `mitte_karte` → `center` |
| labels | `farbe` → `color`, `stil` → `style`, `sperren` → `tracking`, `rechts` → `right` |
| georeference | `k_px_pro_rad` → `k_px_per_rad`, `guete_iou_wasser` → `fit_iou_water` |
| end text | `quelle` → `source`, `stil` → `style`, `musik_absenken_db` → `music_dip_db`, `tafeln` → `cards` |
| end text style | `mitte_y` → `center_y` |
| end text band | `farbe` → `color`, `deckkraft` → `opacity`, `hoehe` → `height` |
| end text cards | `ab` → `from`, `bis` → `to` |

**OTIO metadata**

| Where | Old → new |
|---|---|
| item metadata (read either way; written with the old keys when the edit list is old) | `abschnitt` → `section`, `art` → `kind`, `notiz` → `note`, `quelle_aufloesung` → `source_resolution`, `skalierung_prozent` → `scale_percent`, `ton` → `audio`, `einschnitt_in` → `cutaway_of` |

**decisions.json**

| Where | Old → new |
|---|---|
| top level | `fassung` → `version`, `vergleich` → `compared_to`, `stand` → `date`, `titel` → `title`, `einleitung` → `intro`, `eintraege` → `entries` |
| entry | `titel` → `title`, `warum` → `why`, `frage` → `question`, `nr_bis` → `nr_to`, `schnitt_vor` → `cut_before`, `musik` → `music`, `t_bis` → `t_to`, `bezug` → `refs` |
| reference | `fassung` → `version` |

**timeline.json (review app)**

| Where | Old → new |
|---|---|
| top level | `fassung` → `version`, `titel` → `title`, `dauer` → `duration`, `abschnitte` → `sections`, `einstellungen` → `shots`, `schnitte` → `edit_points`, `musik` → `music`, `untertitel` → `subtitles`, `marker` → `markers`, `erzeugt` → `created`, `quelle` → `source`, `entscheidungen` → `decisions`, `vorschau` → `thumbnails` |
| section | `ende` → `end` |
| shot | `ende` → `end`, `abschnitt` → `section`, `art` → `kind`, `datei` → `file`, `quelle_in` → `source_in`, `quelle_out` → `source_out`, `notiz` → `note`, `baustein` → `block`, `nr_vorher` → `nr_previous`, `ton_db` → `audio_db`, `musik_tiefer` → `music_lower`, `einschnitte` → `cutaways`, `untertitel` → `subtitles`, `saetze` → `sentences` |
| cutaway | `ende` → `end`, `datei` → `file`, `quelle_in` → `source_in`, `quelle_out` → `source_out`, `notiz` → `note` |
| edit point | `vor` → `before`, `nach` → `after`, `art` → `kind` |
| edit point `kind` values | `einstellung` → `shot`, `einschnitt_an` → `cutaway_in`, `einschnitt_aus` → `cutaway_out` |
| music | `titel` → `title`, `kuenstler` → `artist`, `lizenz` → `license`, `ende` → `end`, `quelle_start` → `source_start`, `notiz` → `note`, `datei` → `file`, `ausblenden_s` → `fade_out_s` |
| span | `ende` → `end` |
| marker | `dauer` → `duration` |
| source | `video_art` → `video_kind`, `entscheidungen` → `decisions` |
| `video_kind` values | `grob` → `rough` |
| video | `datei` → `file`, `breite` → `width`, `hoehe` → `height`, `dauer` → `duration`, `nummern_eingebrannt` → `numbers_burned_in` |
| thumbnails | `intervall` → `interval`, `breite` → `width`, `hoehe` → `height`, `anzahl` → `count` |
| decisions | `titel` → `title`, `einleitung` → `intro`, `vergleich` → `compared_to`, `stand` → `date`, `eintraege` → `entries` |
| decision | `titel` → `title`, `t_bis` → `t_to`, `warum` → `why`, `frage` → `question`, `bezug` → `refs` |

**analysis**

| Where | Old → new |
|---|---|
| image analysis per second | `schaerfe` → `sharpness`, `wackeln` → `shake`, `belichtung` → `exposure`, `bewegung` → `motion`, `schwenk` → `pan`, `schlagworte` → `tags`, `einstellung` → `shot_size` |
| exposure | `ausgefressen` → `clipped`, `zu_dunkel` → `too_dark`, `mittel` → `mean` |
| clips.json `created_source` values | `dateiname` → `filename`, `metadaten` → `metadata`, `dateidatum` → `file_date` |

**feedback export**

| Where | Old → new |
|---|---|
| top level | `fassung` → `cut`, `titel` → `title`, `dauer` → `duration`, `quelle` → `source`, `exportiert` → `exported`, `personen` → `people`, `anzahl` → `counts`, `aktionen` → `actions`, `eintraege` → `entries` |
| entry | `t_bis` → `t_to`, `nr_bis` → `nr_to`, `quelle_t` → `source_t`, `einschnitt` → `cutaway`, `abschnitt` → `section`, `musik` → `music`, `schnitt` → `edit_point`, `aktion` → `action`, `aktion_text` → `action_text`, `reaktion` → `reaction`, `antwort` → `reply`, `erstellt` → `created`, `geaendert` → `changed` |
| `status` values | `offen` → `open`, `erledigt` → `done`, `verworfen` → `rejected` |
| `action` values | `raus` → `remove`, `kuerzer` → `shorter`, `laenger` → `longer`, `schnitt_frueher` → `cut_earlier`, `schnitt_spaeter` → `cut_later`, `anderes_bild` → `other_shot`, `musik_leiser` → `music_softer`, `musik_lauter` → `music_louder`, `unverstaendlich` → `unclear`, `gefaellt` → `like`, `frage` → `question` |
