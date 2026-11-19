"""Reframing: which part of a shot stays in the picture when the footage does not have the shape of the frame.

The footage fills the frame (frame.py), so a landscape clip in a 9:16 cut loses most of its width, a portrait clip in
a 16:9 cut most of its height. By default the middle stays. Here the part with the subject stays instead: faces first,
then people, then animals, found once a second on the clip as analysed (the stand-in is enough) and stored per clip
in analysis/reframe.json:

    {"version": 1, "clips": {"C005": {"size": …, "mtime": …, "frames": [[t, [[label, cx, cy, w, h, score], …]], …]}}}

(centre and size as shares of the upright picture). Per shot the window is placed where it holds the most of the
subjects over the shot's in/out range; it does not move during the shot. The result is a focus: the point of the
picture (shares of width and height) that comes to the centre of the frame, as far as the picture still fills it.

edit.json can set it per shot or cutaway: "focus": [0.3, 0.5] (or 0.3 for the width only, or "centre"). Off for a
project: [timeline] reframe = false in cutter.toml.

FilmCraft gets the focus as the clip's position, the review render crops there. The exchange formats (FCP 7 XML,
FCPXML, OTIO) keep the footage centred and get a marker "Reframe shot N" with the focus; their position units are not
documented well enough to set it blind.
"""
import os

from . import frame as frame_mod
from .util import log as progress, read_json, write_json

VERSION = 1
STEP = 1.0
KEEP = 8                         # boxes per frame, the largest
WEIGHT = {"face": 3.0, "person": 1.5}
ANIMALS = {"bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"}
ANIMAL_WEIGHT = 1.0
MIN_CROP = 0.03                  # a shot loses less than this share of its width or height: nothing to place
HEADROOM = 0.1                   # faces sit this share of the window above its middle


def measure_clip(path, duration=None, detector=None, step=STEP):
    """[[t, [[label, cx, cy, w, h, score], …]], …] for one frame per `step` seconds; None without OpenCV or models."""
    try:
        import cv2
    except ImportError:
        return None
    if detector is None:
        detector = make_detector()
    if not detector:
        return None
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
            boxes = [b for b in detector.detect(bgr)["boxes"] if b[0] in WEIGHT or b[0] in ANIMALS]
            boxes.sort(key=lambda b: -b[3] * b[4])
            out.append([round(t, 2), [[b[0]] + [round(float(v), 3) for v in b[1:]] for b in boxes[:KEEP]]])
            t += step
        return out
    finally:
        cap.release()


def make_detector():
    """Faces and objects from the models the analysis uses (not downloaded here); None without them."""
    try:
        import importlib.util
        from . import image_analysis as ia
        faces = ia.model_file("faces", download=False)
        objects = ia.model_file("objects", download=False) if importlib.util.find_spec("onnxruntime") else None
        if not faces and not objects:
            return None
        return ia.Detector(faces=faces, objects=objects, threads=2)
    except Exception:                       # noqa: BLE001 – no OpenCV, a broken model: the middle stays
        return None


def measure(p, clip_ids, clips, log=progress):
    """Measure the clips that are missing or changed in analysis/reframe.json. Returns the stored data."""
    target = p.path("reframe")
    data = read_json(target) if target.exists() else {}
    if data.get("version") != VERSION:
        data = {"version": VERSION, "clips": {}}
    todo = []
    for cid in clip_ids:
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
    detector = make_detector() if todo else None
    if todo and not detector:
        if log:
            log("Reframing: no face or object model (cutter analyze downloads them); the middle of each shot stays")
        return data
    for i, (cid, c, st) in enumerate(todo, 1):
        frames = measure_clip(c["path"], c.get("duration"), detector)
        if frames is None:
            return data
        data["clips"][cid] = {"size": st.st_size, "mtime": int(st.st_mtime), "frames": frames}
        if log and (i % 20 == 0 or i == len(todo)):
            log(f"Reframing: {i}/{len(todo)} clips looked at")
    if todo:
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, data)
    return data


# ---------------------------------------------------------------- where the window goes

def shown(fr, w, h, rotation=0):
    """The share of the picture's width and height that the frame shows when the picture fills it."""
    if rotation in (90, 270, -90):
        w, h = h, w
    s = max(fr.w / w, fr.h / h)
    return min(1.0, fr.w / (w * s)), min(1.0, fr.h / (h * s))


def _weight(label, bw, bh, score):
    base = WEIGHT.get(label, ANIMAL_WEIGHT if label in ANIMALS else 0.0)
    return base * score * (bw * bh) ** 0.5


def _place(subjects, share):
    """The centre (0..1) of a window `share` wide holding the most subject weight; None without subjects.
    subjects: [(centre, weight)]."""
    if not subjects:
        return None
    total = sum(wt for _, wt in subjects)
    mean = sum(c * wt for c, wt in subjects) / total
    half = share / 2
    lo, hi = half, 1 - half

    def held(c):
        return sum(wt for x, wt in subjects if abs(x - c) <= half)
    best = None
    for cand in [mean] + [x for x, _ in subjects]:
        c = min(max(cand, lo), hi)
        key = (round(held(c), 6), -abs(c - mean))
        if best is None or key > best[0]:
            best = (key, c)
    return best[1]


def suggest(frames, t_in, t_out, share_x, share_y):
    """(focus [x, y], why) for a shot from in to out of a clip: why is "faces", "people", "animals" or "centre"."""
    xs, ys, kinds = [], [], set()
    for t, boxes in frames or []:
        if t < t_in - STEP / 2 or t > t_out + STEP / 2:
            continue
        for label, cx, cy, bw, bh, score in boxes:
            wt = _weight(label, bw, bh, score)
            if wt <= 0:
                continue
            kinds.add("faces" if label == "face" else "people" if label == "person" else "animals")
            xs.append((cx, wt))
            ys.append((cy - (HEADROOM * share_y if label == "face" else 0.0), wt))
    fx = _place(xs, share_x) if share_x < 1 - MIN_CROP else None
    fy = _place(ys, share_y) if share_y < 1 - MIN_CROP else None
    why = next((k for k in ("faces", "people", "animals") if k in kinds), "centre")
    if fx is None and fy is None:
        why = "centre"
    return [round(fx if fx is not None else 0.5, 3), round(fy if fy is not None else 0.5, 3)], why


def own_focus(value):
    """A focus from edit.json: [x, y], x, "centre"/"center"; None when not set."""
    if value is None:
        return None
    if isinstance(value, str):
        return [0.5, 0.5] if value in ("centre", "center", "middle") else None
    if isinstance(value, (int, float)):
        return [float(value), 0.5]
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [float(value[0]), float(value[1])]
    return None


def offset(fr, w, h, rotation, focus):
    """Where the picture goes in the frame: [dx, dy] in frame pixels from the centre, so that `focus` comes as close to
    the centre as the picture allows (it always fills the frame)."""
    if rotation in (90, 270, -90):
        w, h = h, w
    s = max(fr.w / w, fr.h / h)
    sw, sh = w * s, h * s
    dx = min(max((0.5 - focus[0]) * sw, -(sw - fr.w) / 2), (sw - fr.w) / 2)
    dy = min(max((0.5 - focus[1]) * sh, -(sh - fr.h) / 2), (sh - fr.h) / 2)
    return [round(dx, 1) + 0.0, round(dy, 1) + 0.0]


def _one(fr, c, item, frames):
    if not c:
        return None
    w, h, rot = c.get("width") or fr.w, c.get("height") or fr.h, c.get("rotation") or 0
    sx, sy = shown(fr, w, h, rot)
    if sx >= 1 - MIN_CROP and sy >= 1 - MIN_CROP:
        return None
    own = own_focus(item.get("focus"))
    if own is not None:
        focus, why = own, "edit"
    else:
        focus, why = suggest(frames, item.get("in", 0.0), item.get("out", 0.0), sx, sy)
    off = offset(fr, w, h, rot, focus)
    if why == "centre" and off == [0.0, 0.0]:
        return None
    return {"focus": focus, "offset": off, "why": why, "shown": [round(sx, 3), round(sy, 3)]}


def plan(edit, clips, data, fr, enabled=True):
    """{"frame", "on", "shots": {nr: {"focus", "offset", "why", "shown", "cutaways": [… | None]}}} for the shots
    whose picture is cut off; shots that stay centred are left out. Disabled: only edit.json's own focus counts."""
    fr = frame_mod.get(fr)
    data = data or {}
    out = {"frame": fr.name, "on": enabled, "shots": {}}
    frames_of = lambda cid: ((data.get("clips") or {}).get(cid) or {}).get("frames") if enabled else None
    for sec in edit.get("sections", []):
        for s in sec.get("shots", []):
            if s.get("clip") not in clips:
                continue
            main = _one(fr, clips[s["clip"]], s, frames_of(s["clip"]))
            cuts = [_one(fr, clips.get(e.get("clip")), e, frames_of(e.get("clip"))) for e in s.get("cutaways") or []]
            if main or any(cuts):
                out["shots"][s["nr"]] = dict(main or {"focus": [0.5, 0.5], "offset": [0.0, 0.0], "why": "centre"},
                                             cutaways=cuts)
    return out


def for_cut(p, cut, edit, fr, clips=None, log=progress):
    """The reframing plan of a cut: measures the clips that are cut off and not measured yet."""
    from .color import load_clips
    from .legacy import edit_from_legacy
    edit = edit_from_legacy(edit)[0]
    fr = frame_mod.get(fr)
    if clips is None:
        clips = load_clips(p)
    enabled = p["timeline"].get("reframe", True)
    need = set()
    if enabled:
        for sec in edit.get("sections", []):
            for s in sec.get("shots", []):
                for item in [s, *(s.get("cutaways") or [])]:
                    c = clips.get(item.get("clip"))
                    if c and own_focus(item.get("focus")) is None:
                        sx, sy = shown(fr, c.get("width") or fr.w, c.get("height") or fr.h, c.get("rotation") or 0)
                        if sx < 1 - MIN_CROP or sy < 1 - MIN_CROP:
                            need.add(item["clip"])
    data = measure(p, sorted(need), clips, log=log) if need else {}
    return plan(edit, clips, data, fr, enabled=enabled)


def of_cut(p, cut, edit, log=progress):
    """(frame, plan) of a cut, as the timeline, FilmCraft and the review use them."""
    from .legacy import edit_from_legacy
    edit = edit_from_legacy(edit)[0]
    fr = frame_mod.of(edit, p["timeline"].get("frame"))
    return fr, for_cut(p, cut, edit, fr, log=log)


def describe(item):
    fx, fy = item["focus"]
    return f"keep {fx * 100:.0f} % from the left, {fy * 100:.0f} % from the top ({item['why']})"
