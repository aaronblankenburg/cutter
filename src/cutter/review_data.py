"""Review data: prepares one cut for the review app.

Writes to <review data>/cuts/<CUT>/:
  review.mp4                 720p, 25 fps, H.264/AAC (at most 2.5 Mbit/s), keyframe every 2 s, faststart
  thumbs_NN.jpg, thumbs.vtt  thumbnails every 2 s as sprites with WebVTT (#xywh=...)
  poster.jpg                 still frame shown before the video loads
  timeline.json              shots (nr, start, end, clip, section, kind, note, spoken text), cuts, sections, music,
                             subtitles, markers and, if present, the changes and decisions from decisions.json;
                             written last

Video:
  video=<file.mp4>   FilmCraft export (720p25 is only remuxed, anything else is converted to 720p25)
  without video      rough review render from the timeline: no titles or transitions, with music, small shot
                     number in the top left corner (numbers=False turns it off)

Times come from the OTIO (rounded to 25 fps, as in FilmCraft), metadata from edit.json, spoken text from the
transcripts (<transcripts>/<clip>.json). Legacy (German-keyed) edit lists, decisions and OTIO metadata are read
transparently. Requires ffmpeg/ffprobe on PATH; the rough render needs numpy.
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import urllib.parse

from .legacy import decisions_from_legacy, edit_from_legacy, otio_meta_in
from .util import CutterError, MISSING, log, read_json

W, H, FPS, SR = 1280, 720, 25, 48000
THUMB_W, THUMB_H, THUMB_COLS, THUMB_ROWS, THUMB_STEP = 192, 108, 10, 20, 2
FONT = next((p for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                         "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
                         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf") if os.path.exists(p)), None)
# Music as in FilmCraft: base level -16 LUFS, under dialogue -35 (with "music_lower" -40) LUFS, 0.4 s ramp down,
# 0.6 s up, fade-out at the end. Fade-out times per cue come from the project configuration (music.fade_out_s);
# "fade_out_s" on a cue in edit.json overrides. Without a value the music stops hard.
MUSIC_BASE, MUSIC_UNDER, MUSIC_LOW = -16.0, -35.0, -40.0
VERSION_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def secs(rt):
    return rt["value"] / rt["rate"]


def q(t):
    """Round to whole frames (that is how clips sit in FilmCraft)."""
    return round(t * FPS) / FPS


def media_path(item):
    ref = item.get("media_reference")
    if ref is None:                         # OTIO 0.15 and later: several references, one active
        refs = item.get("media_references") or {}
        ref = refs.get(item.get("active_media_reference_key", "DEFAULT_MEDIA")) or next(iter(refs.values()), None)
    url = (ref or {}).get("target_url") or ""
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path) if url.startswith("file:") else url


def track_items(track):
    """(start, end, item) for every item of an OTIO track, in seconds."""
    t, out = 0.0, []
    for it in track["children"]:
        if not it["OTIO_SCHEMA"].startswith(("Clip", "Gap")):
            raise CutterError(f"OTIO element {it['OTIO_SCHEMA']} is not supported (no transitions expected)")
        d = secs(it["source_range"]["duration"])
        out.append((t, t + d, it))
        t += d
    return out


def probe(path):
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate", "-of", "json", path],
            capture_output=True, text=True)
    d = json.loads(r.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    num, den = (v.get("r_frame_rate") or "0/1").split("/")
    return {"duration": float(d.get("format", {}).get("duration") or 0), "codec": v.get("codec_name"),
            "width": v.get("width"), "height": v.get("height"), "fps": float(num) / float(den or 1),
            "audio": any(s.get("codec_type") == "audio" for s in d.get("streams", []))}


# ---------------------------------------------------------------- timeline

class Transcripts:
    def __init__(self, folder):
        self.folder, self.cache = pathlib.Path(folder), {}

    def sentences(self, clip, q_in, q_out, t0):
        """Spoken sentences within the source range [q_in, q_out) as timeline times from t0."""
        if clip not in self.cache:
            p = self.folder / f"{clip}.json"
            self.cache[clip] = read_json(p) if p.exists() else None
        tr, out = self.cache[clip], []
        if not tr:
            return out
        for seg in tr.get("segments", []):
            if seg.get("suspect") or seg["end"] <= q_in or seg["start"] >= q_out:
                continue
            words = [w for w in seg.get("words") or [] if q_in - 0.05 <= (w[0] + w[1]) / 2 < q_out]
            if words:
                text, a, b = "".join(w[2] for w in words).strip(), words[0][0], words[-1][1]
            elif not seg.get("words") and q_in <= (seg["start"] + seg["end"]) / 2 < q_out:
                text, a, b = seg["text"].strip(), seg["start"], seg["end"]
            else:
                continue
            if text:
                out.append({"start": round(t0 + max(0.0, a - q_in), 2),
                            "end": round(t0 + min(q_out - q_in, b - q_in), 2), "text": text})
        return out


def build_timeline(version, edit, otio, transcripts, ns="cutter", external=("TXT",), fade_out=None):
    """timeline.json content plus internal fields (file paths) for the render."""
    tracks = {tr["name"]: tr for tr in otio["tracks"]["children"]}
    track = lambda p: next((tr for n, tr in tracks.items() if n.startswith(p)), None)
    v1, v4, a2 = track("V1"), track("V4"), track("A2")
    if v1 is None:
        raise CutterError("OTIO without track V1", MISSING)
    edit_shots = {s["nr"]: (sec["name"], s) for sec in edit["sections"] for s in sec["shots"]}
    meta = lambda it: otio_meta_in(it.get("metadata", {}).get(ns, {}))

    shots = []
    for a, b, it in track_items(v1):
        md = meta(it)
        nr = md.get("nr")
        section, es = edit_shots.get(nr, (md.get("section"), {}))
        is_clip = it["OTIO_SCHEMA"].startswith("Clip")
        q_in = round(secs(it["source_range"]["start_time"]), 3) if is_clip else None
        start, end = q(a), q(b)
        clip = es.get("clip") or (it.get("name", "").split()[1] if is_clip and len(it.get("name", "").split()) > 1 else "TXT")
        audio = es.get("audio") or {}
        s = {"nr": nr, "start": round(start, 3), "end": round(end, 3), "section": section,
             "kind": es.get("kind") or md.get("kind"), "clip": clip,
             "file": os.path.basename(media_path(it)) if is_clip else None,
             "source_in": q_in, "source_out": round(q_in + (b - a), 3) if is_clip else None,
             "note": es.get("note") or md.get("note") or "", "block": es.get("block"),
             "nr_previous": es.get("nr_previous"), "tag": es.get("tag") or md.get("tag"),
             "audio_db": audio.get("gain_db"), "music_lower": bool(audio.get("music_lower")),
             "cutaways": [], "subtitles": [], "sentences": [], "text": "",
             "_path": media_path(it) if is_clip else None}
        if is_clip and clip not in external:
            s["sentences"] = transcripts.sentences(clip, q_in, q_in + (b - a), start)
            s["text"] = " ".join(x["text"] for x in s["sentences"])
        for u in es.get("subtitles", []):
            s["subtitles"].append({"start": round(max(start, start + u["from"]), 2),
                                   "end": round(min(end, start + u["to"]), 2), "text": u["text"]})
        shots.append(s)
    by_nr = {s["nr"]: s for s in shots}

    # cutaways (V4): picture from another clip over running dialogue
    if v4 is not None:
        for a, b, it in track_items(v4):
            md = meta(it)
            if not it["OTIO_SCHEMA"].startswith("Clip") or md.get("cutaway_of") not in by_nr:
                continue
            q_in = secs(it["source_range"]["start_time"])
            name = it.get("name", "").split()
            by_nr[md["cutaway_of"]]["cutaways"].append({
                "start": round(q(a), 3), "end": round(q(b), 3), "clip": name[1] if len(name) > 1 else "?",
                "file": os.path.basename(media_path(it)), "source_in": round(q_in, 3),
                "source_out": round(q_in + (b - a), 3), "note": md.get("note", ""), "_path": media_path(it)})

    # edit points: shot changes and the start/end of cutaways
    edits = [{"t": shots[i]["start"], "before": shots[i - 1]["nr"], "after": shots[i]["nr"], "kind": "shot"}
             for i in range(1, len(shots))]
    for s in shots:
        for e in s["cutaways"]:
            if e["start"] > s["start"] + 0.02:
                edits.append({"t": e["start"], "before": s["nr"], "after": s["nr"], "kind": "cutaway_in"})
            if e["end"] < s["end"] - 0.02:
                edits.append({"t": e["end"], "before": s["nr"], "after": s["nr"], "kind": "cutaway_out"})
    edits.sort(key=lambda c: c["t"])

    sections = []
    for s in shots:
        if sections and sections[-1]["name"] == s["section"]:
            sections[-1]["end"] = s["end"]
        else:
            sections.append({"name": s["section"], "start": s["start"], "end": s["end"]})

    music = []
    for a, b, it in (track_items(a2) if a2 is not None else []):
        if not it["OTIO_SCHEMA"].startswith("Clip"):
            continue
        cue = it.get("name", "M?").split()[0]
        m = next((m for m in edit.get("music", []) if m.get("cue") == cue), {})
        music.append({"cue": cue, "title": m.get("title"), "artist": m.get("artist"), "license": m.get("license"),
                      "start": round(q(a), 3), "end": round(q(b), 3),
                      "source_start": round(secs(it["source_range"]["start_time"]), 3),
                      "bpm": m.get("bpm"), "note": m.get("note"), "file": os.path.basename(media_path(it)),
                      "fade_out_s": m.get("fade_out_s", (fade_out or {}).get(cue, 0.0)), "_path": media_path(it)})

    markers = [{"name": mk.get("name"), "t": round(secs(mk["marked_range"]["start_time"]), 3),
                "duration": round(secs(mk["marked_range"]["duration"]), 3)}
               for mk in otio["tracks"].get("markers", [])]
    subtitles = sorted((u for s in shots for u in s["subtitles"]), key=lambda u: u["start"])

    return {"format": 2, "version": version, "title": edit.get("title") or otio.get("name"), "fps": FPS,
            "duration": shots[-1]["end"] if shots else 0.0, "sections": sections, "shots": shots,
            "edit_points": edits, "music": music, "subtitles": subtitles, "markers": markers}


def decisions(path, tl):
    """Changes and decisions relative to the previous cut (decisions.json) for the app: places given as shot
    numbers, a cut or a music cue are converted to seconds of this cut."""
    d = decisions_from_legacy(read_json(path))
    shots = {s["nr"]: s for s in tl["shots"]}
    music = {m["cue"]: m for m in tl["music"]}

    def fail(text):
        raise CutterError(f"{pathlib.Path(path).name}: {text}", MISSING)
    out, ids = [], set()
    for e in d.get("entries", []):
        eid = e.get("id")
        if not eid or eid in ids or not e.get("title"):
            fail(f"every entry needs its own id and a title ({eid!r})")
        ids.add(eid)
        t = t_to = None
        if "t" in e:
            t, t_to = float(e["t"]), (float(e["t_to"]) if e.get("t_to") is not None else None)
        elif "nr" in e:
            a, b = shots.get(e["nr"]), shots.get(e.get("nr_to", e["nr"]))
            if not a or not b or b["end"] <= a["start"]:
                fail(f"{eid}: shot {e['nr']} to {e.get('nr_to', e['nr'])} does not exist in the timeline")
            t, t_to = a["start"], b["end"]
        elif "cut_before" in e:
            s = shots.get(e["cut_before"])
            if not s:
                fail(f"{eid}: shot {e['cut_before']} does not exist in the timeline")
            t, t_to = max(0.0, s["start"] - 2.0), min(tl["duration"], s["start"] + 2.0)
        elif "music" in e:
            m = music.get(e["music"])
            if not m:
                fail(f"{eid}: music cue {e['music']} does not exist in the timeline")
            t, t_to = m["start"], min(m["end"], tl["duration"])
        if t is not None and not (0 <= t < tl["duration"] and (t_to is None or t < t_to <= tl["duration"] + 0.05)):
            fail(f"{eid}: place {t}–{t_to} is not inside the version (0–{tl['duration']} s)")
        refs = []
        for r in e.get("refs", []):
            if not r.get("person") or not r.get("text"):
                fail(f"{eid}: a reference needs person and text")
            refs.append({"version": r.get("version", d.get("compared_to")), "id": r.get("id"), "person": r["person"],
                         "t": r.get("t"), "text": r["text"]})
        out.append({"id": eid, "title": e["title"], "t": None if t is None else round(t, 3),
                    "t_to": None if t_to is None else round(t_to, 3), "text": e.get("text", ""),
                    "why": e.get("why"), "question": e.get("question"), "refs": refs})
    out.sort(key=lambda x: (x["t"] is not None, x["t"] or 0.0))     # general entries first, then by time
    return {"title": d.get("title") or "Changes and decisions", "intro": d.get("intro"),
            "compared_to": d.get("compared_to"), "date": d.get("date"), "entries": out}


def without_internal(obj):
    """Remove internal fields (_path) for timeline.json."""
    if isinstance(obj, dict):
        return {k: without_internal(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [without_internal(v) for v in obj]
    return obj


# ---------------------------------------------------------------- video

def drawtext(text, large=False):
    font = f"fontfile={FONT}:" if FONT else ""
    text = text.replace("\\", "").replace("'", "").replace(":", " ").replace("%", "")
    if large:
        return (f"drawtext={font}text='{text}':x=(w-tw)/2:y=(h-th)/2:fontsize=30:fontcolor=white@0.55")
    return (f"drawtext={font}text='{text}':x=14:y=12:fontsize=26:fontcolor=white:"
            f"box=1:boxcolor=black@0.5:boxborderw=6")


def rough_video(tl, tmp, numbers, jobs):
    """Picture track without sound: every shot (and every cutaway) as its own piece, then concatenated."""
    pieces = []        # (frame_from, frame_to, path|None, source_time_at_frame_from, label, hint)
    for s in tl["shots"]:
        f0, f1 = round(s["start"] * FPS), round(s["end"] * FPS)
        if f1 <= f0:
            continue
        if s["_path"]:
            parts = [(f0, f1, s["_path"], s["source_in"] + (f0 / FPS - s["start"]), str(s["nr"]), None)]
        else:
            hint = "Black" if s["kind"] == "black" else "Title missing in the rough render"
            parts = [(f0, f1, None, 0.0, str(s["nr"]), hint)]
        for e in s["cutaways"]:
            e0, e1 = round(e["start"] * FPS), round(e["end"] * FPS)
            new = []
            for p0, p1, path, qz, lab, hw in parts:
                if e1 <= p0 or e0 >= p1:
                    new.append((p0, p1, path, qz, lab, hw))
                    continue
                if e0 > p0:
                    new.append((p0, e0, path, qz, lab, hw))
                a0 = max(p0, e0)
                new.append((a0, min(p1, e1), e["_path"], e["source_in"] + (a0 / FPS - e["start"]), f"{s['nr']}e", None))
                if e1 < p1:
                    new.append((e1, p1, path, qz + (e1 - p0) / FPS, lab, hw))
            parts = new
        pieces.extend(parts)

    def command(i, st):
        p0, p1, path, qz, lab, hw = st
        out = tmp / f"piece_{i:04d}.mp4"
        vf = []
        if path:
            inp = ["-ss", f"{max(0.0, qz):.3f}", "-i", path]
            vf += [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}", f"fps={FPS}", "setsar=1",
                   "tpad=stop_mode=clone:stop_duration=2"]
        else:
            inp = ["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}"]
            if hw:
                vf.append(drawtext(hw, large=True))
        vf.append("format=yuv420p")
        if numbers:
            vf.append(drawtext(lab))
        return out, ["ffmpeg", "-nostdin", "-v", "error", "-y", *inp, "-frames:v", p1 - p0, "-vf", ",".join(vf), "-an",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-maxrate", "2500k", "-bufsize", "5000k",
                     "-g", 2 * FPS, "-r", FPS, "-video_track_timescale", 12800, "-threads", 2, out]

    jobs_ = [command(i, st) for i, st in enumerate(pieces)]
    log(f"  Picture: rendering {len(jobs_)} pieces …")
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(lambda a: run(a[1], capture_output=True), jobs_))
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{o}'\n" for o, _ in jobs_), encoding="utf-8")
    picture = tmp / "picture.mp4"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", picture])
    return picture


def loudness(path, cache={}):
    if path not in cache:
        r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-vn", "-af", "ebur128=framelog=quiet",
                            "-f", "null", "-"], capture_output=True, text=True)
        m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
        cache[path] = float(m[-1]) if m else -14.0
    return cache[path]


def music_keyframes(a, b, base, under, regions, fade):
    """Level keyframes (time, dB) as in the FilmCraft project: lowered under dialogue, faded out at the end."""
    kf = {}

    def put(t, v):
        kf[round(t, 3)] = v
    put(a, base if not regions or regions[0][0] > a + 0.05 else under(regions[0][2]))
    for i, (s_, e_, low) in enumerate(regions):
        prev = regions[i - 1] if i else None
        if prev and abs(prev[1] - s_) < 0.05:
            put(s_ - 0.04, kf[max(kf)]); put(s_, under(low))
        elif s_ > a + 0.05:
            put(max(a, s_ - 0.4), base); put(s_, under(low))
        nxt = regions[i + 1] if i + 1 < len(regions) else None
        if nxt and abs(nxt[0] - e_) < 0.05:
            continue
        put(e_, under(low))
        if e_ < b - 0.05:
            put(min(b, e_ + 0.6), base)
    if fade:
        t0 = b - fade
        last = [v for t, v in sorted(kf.items()) if t <= t0]
        for t in [t for t in kf if t > t0]:
            del kf[t]
        put(t0, last[-1] if last else base); put(b - 0.04, -60.0)
    return sorted(kf.items())


def rough_audio(tl, external=("TXT",), levels=None):
    """Mix dialogue/camera audio and music (float32, 48 kHz, stereo)."""
    import numpy as np

    def decode(path, start, duration, af=None):
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{duration:.3f}", "-i", path,
               "-vn", "-ac", 2, "-ar", SR] + (["-af", af] if af else []) + ["-f", "f32le", "-"]
        r = subprocess.run([str(c) for c in cmd], capture_output=True)
        if r.returncode:
            return np.zeros((0, 2), np.float32)
        return np.frombuffer(r.stdout, dtype="<f4").reshape(-1, 2)

    n = round(tl["duration"] * SR)
    mix = np.zeros((n + SR, 2), np.float32)
    ramp = np.linspace(0, 1, int(0.005 * SR), dtype=np.float32)[:, None]       # 5 ms against clicks
    for s in tl["shots"]:
        if not s["_path"]:
            continue
        dur = s["end"] - s["start"]
        db = s["audio_db"] if s["audio_db"] is not None else (0.0 if s["clip"] in external or s["kind"] == "dialogue" else -10.0)
        x = decode(s["_path"], s["source_in"], dur, "highpass=f=100" if s["kind"] == "dialogue" else None)
        x = x[:round(dur * SR)] * np.float32(10 ** (db / 20))
        if len(x) > 2 * len(ramp):
            x[:len(ramp)] *= ramp
            x[-len(ramp):] *= ramp[::-1]
        i0 = round(s["start"] * SR)
        mix[i0:i0 + len(x)] += x
    dialogue = [(s["start"], s["end"], s["music_lower"]) for s in tl["shots"] if s["kind"] == "dialogue"]
    p = {"base": MUSIC_BASE, "under": MUSIC_UNDER, "low": MUSIC_LOW, **(levels or {})}
    for m in tl["music"]:
        a, b = m["start"], m["end"]
        L = loudness(m["_path"])
        base = p["base"] - L
        under = lambda low, L=L: (p["low"] if low else p["under"]) - L
        reg = [(s_, e_, low) for s_, e_, low in dialogue if s_ < b - 0.05 and e_ > a + 0.05]
        kf = music_keyframes(a, b, base, under, reg, m.get("fade_out_s") or 0.0)
        x = decode(m["_path"], m["source_start"], b - a)
        t = a + np.arange(len(x)) / SR
        hull = np.interp(t, [k for k, _ in kf], [v for _, v in kf])
        x = x * (10 ** (hull / 20)).astype(np.float32)[:, None]
        i0 = round(a * SR)
        mix[i0:i0 + len(x)] += x[:len(mix) - i0]
        log(f"  Music {m['cue']}: {L:.1f} LUFS, base level {base:+.1f} dB, lowered at {len(reg)} dialogue places")
    mix = mix[:n]
    over = np.abs(mix) > 0.9                                                   # soft limiter
    mix[over] = np.sign(mix[over]) * (0.9 + 0.1 * np.tanh((np.abs(mix[over]) - 0.9) / 0.1))
    return mix.astype("<f4").tobytes()


def rough_render(tl, target, tmp, numbers, jobs, external=("TXT",), levels=None):
    picture = rough_video(tl, tmp, numbers, jobs)
    log("  Mixing audio …")
    audio = rough_audio(tl, external, levels)
    log("  Joining picture and sound …")
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", picture, "-f", "f32le", "-ar", SR, "-ac", 2, "-i", "-",
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
         "-shortest", target], input=audio)


def numbers_ass(tl, path):
    def ts(x):
        cs = round(x * 100); h_, cs = divmod(cs, 360000); m_, cs = divmod(cs, 6000); s_, cs = divmod(cs, 100)
        return f"{h_}:{m_:02d}:{s_:02d}.{cs:02d}"
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            "Style: Nr,DejaVu Sans,26,&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,-1,0,0,0,100,100,0,0,3,6,0,7,14,14,12,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = [f"Dialogue: 0,{ts(s['start'])},{ts(s['end'])},Nr,,0,0,0,,{s['nr']}" for s in tl["shots"]]
    pathlib.Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


def export_video(source, tl, target, tmp, numbers):
    """Take over a FilmCraft export: 720p25 H.264 is only remuxed, anything else is converted."""
    p = probe(source)
    if abs(p["duration"] - tl["duration"]) > 0.5:
        log(f"  Warning: video {p['duration']:.2f} s, timeline {tl['duration']:.2f} s – does the export match the version?")
    if not numbers and p["codec"] == "h264" and (p["height"] or 0) <= 720 and abs(p["fps"] - FPS) < 0.01 and p["audio"]:
        log("  Export is already 720p25 H.264 – only remuxing (faststart)")
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source, "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
             "-movflags", "+faststart", target])
        return
    vf = [f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos", f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
          f"fps={FPS}", "setsar=1", "format=yuv420p"]
    if numbers:
        numbers_ass(tl, tmp / "nr.ass")
        vf.append(f"ass={tmp / 'nr.ass'}")
    log(f"  Export {p['width']}x{p['height']} {p['fps']:.3g} fps is converted to {W}x{H} {FPS} fps …")
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source, "-map", "0:v:0", "-map", "0:a:0?", "-vf", ",".join(vf),
         "-c:v", "libx264", "-preset", "medium", "-crf", "26", "-maxrate", "2500k", "-bufsize", "5000k",
         "-g", 2 * FPS, "-keyint_min", FPS,
         "-c:a", "aac", "-b:a", "160k", "-ac", 2, "-movflags", "+faststart", target])


def thumbnails(video, folder, duration):
    for old in folder.glob("thumbs_*.jpg"):
        old.unlink()
    per_sheet = THUMB_COLS * THUMB_ROWS
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", video, "-an", "-vf",
         f"select='not(mod(n\\,{FPS * THUMB_STEP}))',scale={THUMB_W}:{THUMB_H},tile={THUMB_COLS}x{THUMB_ROWS}",
         "-fps_mode", "vfr", "-q:v", 5, folder / "thumbs_%02d.jpg"])
    count = int(duration // THUMB_STEP) + 1

    def ts(x):
        ms = round(x * 1000); h_, ms = divmod(ms, 3600000); m_, ms = divmod(ms, 60000); s_, ms = divmod(ms, 1000)
        return f"{h_:02d}:{m_:02d}:{s_:02d}.{ms:03d}"
    lines = ["WEBVTT", ""]
    for k in range(count):
        sheet, pos = divmod(k, per_sheet)
        a, b = k * THUMB_STEP, min(duration, (k + 1) * THUMB_STEP)
        if b <= a:
            break
        x, y = (pos % THUMB_COLS) * THUMB_W, (pos // THUMB_COLS) * THUMB_H
        lines += [f"{ts(a)} --> {ts(b)}", f"thumbs_{sheet + 1:02d}.jpg#xywh={x},{y},{THUMB_W},{THUMB_H}", ""]
    (folder / "thumbs.vtt").write_text("\n".join(lines), encoding="utf-8")
    missing = [b for b in range((count - 1) // per_sheet + 1) if not (folder / f"thumbs_{b + 1:02d}.jpg").exists()]
    if missing:
        raise CutterError(f"Thumbnail sprite missing: {missing}")
    return count


def create(version, edit_path, otio_path, target, transcripts, video=None, decisions_path=None, numbers_in_export=False,
           no_numbers=False, timeline_only=False, jobs=None, ns="cutter", external=("TXT",), fade_out=None, levels=None):
    """Write the review data of one version. decisions_path: changes and decisions (None = none). Returns a report."""
    if not VERSION_RE.fullmatch(version):
        raise CutterError("Cut names use letters, digits, - and _ (for example cut4)", 2)
    edit_path, otio_path, target = pathlib.Path(edit_path), pathlib.Path(otio_path), pathlib.Path(target)
    for p in (edit_path, otio_path):
        if not p.exists():
            raise CutterError(f"Not found: {p}", MISSING)
    if video is not None and not pathlib.Path(video).exists():
        raise CutterError(f"Video not found: {video}", MISSING)
    if decisions_path is not None and not pathlib.Path(decisions_path).exists():
        raise CutterError(f"Not found: {decisions_path}", MISSING)
    jobs = jobs or min(8, os.cpu_count() or 2)
    edit = edit_from_legacy(read_json(edit_path))[0]
    otio = read_json(otio_path)
    folder = target / version
    folder.mkdir(parents=True, exist_ok=True)

    log(f"Cut {version}: {edit_path.name} + {otio_path.name}")
    tl = build_timeline(version, edit, otio, Transcripts(transcripts), ns, external, fade_out)
    tl["created"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    tl["source"] = {"edit": edit_path.name, "otio": otio_path.name,
                    "video": pathlib.Path(video).name if video else "rough render (no titles or transitions)",
                    "video_kind": "export" if video else "rough"}
    if decisions_path is not None:
        decisions_path = pathlib.Path(decisions_path)
        tl["decisions"] = decisions(decisions_path, tl)
        tl["source"]["decisions"] = decisions_path.name
    video_target = folder / "review.mp4"
    old_tl = folder / "timeline.json"
    if timeline_only:
        if old_tl.exists():
            from .legacy import timeline_from_legacy
            old = timeline_from_legacy(read_json(old_tl))
            tl["source"]["video"], tl["source"]["video_kind"] = old["source"]["video"], old["source"]["video_kind"]
            for k in ("video", "thumbnails", "poster"):
                if k in old:
                    tl[k] = old[k]
    else:
        numbers = numbers_in_export if video else not no_numbers
        with tempfile.TemporaryDirectory(prefix="review_") as tmp_:
            tmp = pathlib.Path(tmp_)
            new = tmp / "review.mp4"
            if video:
                export_video(pathlib.Path(video), tl, new, tmp, numbers)
            else:
                log("Rough review render from the timeline (no titles or transitions, with music):")
                rough_render(tl, new, tmp, numbers, jobs, external, levels)
            between = folder / "review.mp4.new"           # copy next to it first, then replace atomically
            shutil.copyfile(new, between)
            os.replace(between, video_target)
        p = probe(video_target)
        if abs(p["duration"] - tl["duration"]) > 0.5:
            log(f"Warning: review.mp4 is {p['duration']:.2f} s long, the timeline {tl['duration']:.2f} s")
        n = thumbnails(video_target, folder, p["duration"])
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{min(35.0, p['duration'] / 2):.2f}", "-i", video_target,
             "-frames:v", 1, "-q:v", 3, folder / "poster.jpg"])
        tl["video"] = {"file": "review.mp4", "width": p["width"], "height": p["height"], "fps": round(p["fps"], 3),
                       "duration": round(p["duration"], 3), "numbers_burned_in": numbers}
        tl["thumbnails"] = {"vtt": "thumbs.vtt", "interval": THUMB_STEP, "width": THUMB_W, "height": THUMB_H, "count": n}
        tl["poster"] = "poster.jpg"
    tmp_tl = folder / "timeline.json.new"
    tmp_tl.write_text(json.dumps(without_internal(tl), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp_tl, old_tl)
    spoken = sum(1 for s in tl["shots"] if s["text"])
    log(f"Done: {folder}")
    log(f"  {len(tl['shots'])} shots ({spoken} with text), {len(tl['edit_points'])} edit points, {len(tl['music'])} music cues, "
        f"{len(tl['subtitles'])} subtitles, duration {tl['duration']:.2f} s")
    report = {"version": version, "folder": str(folder), "timeline": str(old_tl), "shots": len(tl["shots"]),
              "with_text": spoken, "edit_points": len(tl["edit_points"]), "music": len(tl["music"]), "subtitles": len(tl["subtitles"]),
              "duration": tl["duration"], "video": str(video_target) if video_target.exists() else None}
    if "decisions" in tl:
        ent = tl["decisions"]["entries"]
        log(f"  {len(ent)} changes/decisions from {decisions_path.name} ({sum(1 for x in ent if x['question'])} with a question)")
        report["decisions"] = len(ent)
    if video_target.exists() and not timeline_only:
        log(f"  review.mp4 {video_target.stat().st_size / 1e6:.0f} MB, thumbnails: {tl.get('thumbnails', {}).get('count')}")
    return report
