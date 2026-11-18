"""Colour correction: a suggestion per shot, measured on the footage and applied in FilmCraft.

The suggestion corrects what is clearly off and leaves the rest alone:

- exposure: a picture that is clearly too dark or too bright is moved towards a normal middle, never so far that
  its highlights would clip;
- white balance: a colour cast is measured on the near-neutral parts of the picture (grey rock, white walls,
  clouds) and partly removed; pictures without enough neutral area are not touched, and warm light (sunsets,
  desert sand) is cooled only half as much as a blue cast is warmed;
- flat, hazy pictures get deeper blacks.

Shots of the same camera within a section are pulled towards their common value, so that a sequence from one
camera does not flicker between corrections.

Measurement (`measure`): one frame per second of the clip as analysed (the stand-in or proxy is enough: only
averages and percentiles are used), stored per clip in analysis/color.json:

    {"version": 1, "clips": {"C005": {"size": …, "mtime": …, "frames": [[t, p02, p50, p98, r, g, b, neutral], …]}}}

p02/p50/p98: percentiles of the luma (0..1, as encoded); r/g/b: mean of the near-neutral pixels; neutral: their
share of the picture.

The suggestion is in the units of FilmCraft's Color Grading effect (`lumetri`), measured on rendered frames:
exposure in stops (a value v becomes v·2^(e/2.2) as encoded), temperature t multiplies R/B by e^(0.0034·t),
tint t multiplies G by e^(−0.0015·t), blacks b lifts black by 0.0014·b.

Switching off: [color] auto = false in cutter.toml, a shot's "color": false in edit.json, or a choice in the review
(entries with "color": "all/off", "12/off", "12/on"; see review_app.server). A shot's "color": {…} in edit.json
replaces the suggestion for it.
"""
import math
import os
import pathlib

from .util import log as progress, read_json, write_json

VERSION = 1
PARAMS = ("exposure", "temperature", "tint", "blacks")
K_TEMP, K_TINT, K_BLACKS = 0.0034, 0.0015, 0.0014
# Tuned on real footage (desert, roads, camp fire, dusk): leave mood alone, correct what is clearly off.
LIMITS = {"exposure": (-0.7, 0.6), "temperature": (-20.0, 25.0), "tint": (-12.0, 12.0), "blacks": (-20.0, 0.0)}
SMALL = {"exposure": 0.15, "temperature": 8.0, "tint": 6.0, "blacks": 6.0}
MID_LOW, MID_HIGH, MID_TARGET_LOW, MID_TARGET_HIGH = 0.25, 0.62, 0.36, 0.55
NIGHT = 0.08                  # p50 below this: night (camp fire, stars); darkness is the picture, not brightened
BRIGHT_PARTS = 0.8            # p98 above this: the picture has bright parts (sky, lamp, fire); it is not brightened
LOW_LIGHT = 0.25              # p50 below this: dusk, night, fire light – the colour of the light is the mood
NEUTRAL_MIN = 0.15            # share of near-neutral pixels needed to judge the white balance
STRENGTH = 0.5                # share of a colour cast that is removed
HAZE = 0.12                   # p02 above this: flat picture
FRAME_W = 160


# ---------------------------------------------------------------- measuring

def frame_stats(rgb):
    """[p02, p50, p98, r, g, b, neutral] of an RGB picture (floats 0..1, H×W×3, as encoded)."""
    import numpy as np
    y = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    p02, p50, p98 = (float(v) for v in np.percentile(y, (2, 50, 98)))
    chroma = rgb.max(axis=-1) - rgb.min(axis=-1)
    mask = (chroma < 0.05 + 0.3 * y) & (y > 0.15) & (y < 0.92)       # neutral, also under a cast of ±15 %
    share = float(mask.mean())
    if share > 0:
        r, g, b = (float(v) for v in rgb[mask].mean(axis=0))
    else:
        r = g = b = 0.0
    return [round(v, 4) for v in (p02, p50, p98, r, g, b, share)]


def measure_clip(path, duration=None, step=1.0):
    """[[t, p02, p50, p98, r, g, b, neutral], …] for one frame per `step` seconds of a video file."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        dur = duration or (n / fps if n else 0)
        out, t = [], min(0.5, dur / 2) if dur else 0.0
        while t < max(dur, 0.01):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, bgr = cap.read()
            if not ok:
                break
            h, w = bgr.shape[:2]
            small = cv2.resize(bgr, (FRAME_W, max(1, round(FRAME_W * h / w))), interpolation=cv2.INTER_AREA)
            rgb = small[..., ::-1].astype(np.float32) / 255.0
            out.append([round(t, 2)] + frame_stats(rgb))
            t += step
        return out
    finally:
        cap.release()


def measure(p, clip_ids=None, clips=None, log=progress):
    """Measure the clips that are missing or changed in analysis/color.json (all clips, or `clip_ids`).
    Returns the stored data."""
    target = p.path("color")
    data = read_json(target) if target.exists() else {}
    if data.get("version") != VERSION:
        data = {"version": VERSION, "clips": {}}
    if clips is None:
        clips = load_clips(p)
    todo = []
    for cid in (clip_ids if clip_ids is not None else clips):
        c = clips.get(cid)
        if not c or not c.get("has_video", True) or not c.get("path"):
            continue
        try:
            st = os.stat(c["path"])
        except OSError:
            continue
        have = data["clips"].get(cid)
        if not have or have.get("size") != st.st_size or have.get("mtime") != int(st.st_mtime):
            todo.append((cid, c, st))
    for i, (cid, c, st) in enumerate(todo, 1):
        frames = measure_clip(c["path"], c.get("duration"))
        data["clips"][cid] = {"size": st.st_size, "mtime": int(st.st_mtime), "frames": frames}
        if log and (i % 20 == 0 or i == len(todo)):
            log(f"Colour measured: {i}/{len(todo)} clips")
    if todo:
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, data)
    return data


def load_clips(p):
    """clips.json with the paths resolved against [media]: {id: clip}."""
    from .timeline import resolve_clips
    return resolve_clips({c["id"]: c for c in read_json(p.path("clips"))}, p.media_roots())


# ---------------------------------------------------------------- the suggestion

def _median(values):
    v = sorted(values)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def _clamp(k, v):
    lo, hi = LIMITS[k]
    return max(lo, min(hi, v))


def suggest(frames, t_in=None, t_out=None):
    """The correction {exposure, temperature, tint, blacks} for the frames between t_in and t_out (all when None);
    only the values that are worth changing. Empty when there is nothing to measure or nothing to correct."""
    rows = [f for f in frames if (t_in is None or f[0] >= t_in - 0.5) and (t_out is None or f[0] <= t_out + 0.5)]
    if not rows and frames:
        mid = ((t_in or 0) + (t_out if t_out is not None else t_in or 0)) / 2
        rows = [min(frames, key=lambda f: abs(f[0] - mid))]
    if not rows:
        return {}
    p02 = _median([f[1] for f in rows])
    p50 = _median([f[2] for f in rows])
    p98 = _median([f[3] for f in rows])
    out = {}

    # exposure: towards a normal middle, never clipping the highlights
    e = 0.0
    if NIGHT < p50 < MID_LOW and p98 < BRIGHT_PARTS:
        e = 2.2 * math.log2(MID_TARGET_LOW / p50)
        e = max(0.0, min(e, 2.2 * math.log2(0.95 / max(p98, 0.01))))
    elif p50 > MID_HIGH:
        e = 2.2 * math.log2(MID_TARGET_HIGH / p50)
    out["exposure"] = e

    # white balance from the near-neutral pixels
    weight = sum(f[7] for f in rows)
    if weight / len(rows) >= NEUTRAL_MIN and p50 >= LOW_LIGHT:
        r = sum(f[4] * f[7] for f in rows) / weight
        g = sum(f[5] * f[7] for f in rows) / weight
        b = sum(f[6] * f[7] for f in rows) / weight
        if min(r, g, b) > 0.02:
            temp = STRENGTH * math.log(b / r) / K_TEMP
            if temp < 0:                         # a warm picture: keep most of its warmth
                temp /= 2
            out["temperature"] = temp
            out["tint"] = STRENGTH * math.log(g / math.sqrt(r * b)) / K_TINT

    # flat, hazy picture: deeper blacks
    if p02 > HAZE:
        out["blacks"] = -(p02 - 0.04) / K_BLACKS
    return tidy(out)


def tidy(values):
    """Clamped, rounded, and without the values too small to matter."""
    out = {}
    for k in PARAMS:
        v = values.get(k)
        if v is None:
            continue
        v = _clamp(k, float(v))
        if abs(v) >= SMALL[k]:
            out[k] = round(v, 2) if k == "exposure" else round(v)
    return out


def camera_key(c):
    """Clips from the same camera: codec, picture size and the letters of the file name (GOPR, DSC_, MVI_ …)."""
    name = (c.get("original") or {}).get("name") or pathlib.Path(c.get("file") or c.get("path") or "").name
    prefix = "".join(ch for ch in name.split(".")[0] if ch.isalpha())[:4].upper()
    codec = (c.get("original") or {}).get("codec_string") or c.get("codec") or ""
    return (codec.split(".")[0], c.get("width"), c.get("height"), prefix, c.get("device") or "")


# ---------------------------------------------------------------- per cut

def choices(entries):
    """The colour choices of the review: {"all": bool|None, "shots": {nr: bool}}, the latest entry winning.
    entries: dicts with "color" ("all/off", "all/on", "<nr>/off", "<nr>/on") and "changed"/"created"."""
    out = {"all": None, "shots": {}}
    for e in sorted((e for e in entries if e.get("color")),
                    key=lambda e: (e.get("changed") or e.get("created") or "", e.get("id") or 0)):
        what, _, state = str(e["color"]).partition("/")
        on = state == "on"
        if what == "all":
            out["all"] = on
        elif what.isdigit():
            out["shots"][int(what)] = on
    return out


def plan(edit, clips, data, enabled=True, review=None):
    """{"shots": {nr: {"suggested": {…}, "values": {…}, "on": bool, "shot_on": bool, "cutaways": [{…values…}]}},
    "on": bool}. values: what goes into the film (empty when switched off or nothing to correct); on: the shot is
    corrected; shot_on: the shot itself is not switched off (whatever the whole film's switch says)."""
    review = review or {"all": None, "shots": {}}
    film_on = enabled if review.get("all") is None else review["all"]
    measured = (data or {}).get("clips") or {}
    shots = [(sec.get("name"), s) for sec in edit.get("sections", []) for s in sec.get("shots", [])]

    def raw(cid, a, b):
        m = measured.get(cid)
        return suggest(m["frames"], a, b) if m and m.get("frames") else {}

    base = {s["nr"]: raw(s.get("clip"), s.get("in"), s.get("out")) for _, s in shots}
    # one camera within a section: halfway towards the group's median
    groups = {}
    for sec, s in shots:
        c = clips.get(s.get("clip"))
        if c and (measured.get(s.get("clip")) or {}).get("frames"):
            groups.setdefault((sec, camera_key(c)), []).append(s["nr"])
    smooth = {nr: dict(v) for nr, v in base.items()}
    for nrs in groups.values():
        if len(nrs) < 2:
            continue
        for k in PARAMS:
            med = _median([base[n].get(k, 0.0) for n in nrs])
            for n in nrs:
                smooth[n][k] = (base[n].get(k, 0.0) + med) / 2
    out = {}
    for _, s in shots:
        nr = s["nr"]
        suggested = tidy(smooth.get(nr) or {})
        own = s.get("color")
        on = film_on and own is not False and review["shots"].get(nr, True)
        if isinstance(own, dict):
            suggested = tidy(own)
        values = suggested if on else {}
        cut = [tidy(raw(c.get("clip"), c.get("in"), c.get("out"))) if on else {} for c in s.get("cutaways") or []]
        shot_on = own is not False and review["shots"].get(nr, True)
        out[nr] = {"suggested": suggested, "values": values, "on": bool(on), "shot_on": bool(shot_on), "cutaways": cut}
    return {"on": bool(film_on), "shots": out}


def for_cut(p, cut, edit, clips=None, review_entries=None, log=progress):
    """The plan of a cut: measures what is missing, reads [color] and the review's choices."""
    from .legacy import edit_from_legacy
    edit = edit_from_legacy(edit)[0]
    if clips is None:
        clips = load_clips(p)
    used = {s.get("clip") for sec in edit.get("sections", []) for s in sec.get("shots", [])}
    used |= {c.get("clip") for sec in edit.get("sections", []) for s in sec.get("shots", []) for c in s.get("cutaways") or []}
    data = measure(p, [c for c in used if c in clips], clips, log=log)
    if review_entries is None:
        review_entries = review_choices_of(p, cut)
    return plan(edit, clips, data, enabled=p["color"].get("auto", True), review=choices(review_entries))


def review_choices_of(p, cut):
    """The review's colour entries of a cut (empty when there is no review database)."""
    import sqlite3
    db = p.path("review") / "feedback.sqlite"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(entries)")}
            if "color" not in cols:
                return []
            rows = con.execute("SELECT color, created, changed, id FROM entries WHERE cut = ? AND color IS NOT NULL",
                               (cut,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return [{"color": r[0], "created": r[1], "changed": r[2], "id": r[3]} for r in rows]


# ---------------------------------------------------------------- preview

def apply_preview(rgb, values):
    """The suggestion applied to an RGB picture (floats 0..1) with the measured model of FilmCraft's effect: for
    the before/after stills of the review. Close to FilmCraft's result, not identical."""
    import numpy as np
    x = rgb.astype(np.float32).copy()
    if values.get("exposure"):
        x *= 2 ** (values["exposure"] / 2.2)
    if values.get("temperature"):
        k = math.exp(K_TEMP * values["temperature"] / 2)
        x[..., 0] *= k
        x[..., 2] /= k
    if values.get("tint"):
        x[..., 1] *= math.exp(-K_TINT * values["tint"])
    if values.get("blacks"):
        lift = K_BLACKS * values["blacks"]
        x = lift + x * (1 - lift)
    return np.clip(x, 0, 1)


def describe(values):
    """"exposure +0.40, temperature −12" for markers and logs."""
    names = {"exposure": "exposure", "temperature": "temperature", "tint": "tint", "blacks": "blacks"}
    return ", ".join(f"{names[k]} {v:+g}" for k, v in values.items())
