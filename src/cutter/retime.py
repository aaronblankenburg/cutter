"""Put cuts on the beat: picture cuts under music on beats, section changes on downbeats.

Inputs: the edit list of a cut (cuts/<cut>/edit.json, written by the agent or by plan.py), clips.json, the music
analysis (music/music.json from `cutter music analyze`) and optionally the image analysis (sharpness, shake,
exposure per second from `cutter analyze --image`).

Which shots lie under which music follows from the anchors of the music cues in the edit list ("from"/"to":
section+edge, tag+edge, time, end). Per cue, cutter.toml → [retime.cues.<cue>] sets:
  phase        beat index (0–3) of the first downbeat, otherwise "phase" from music.json
  beats_from   use the beats of another cue (a second excerpt of the same track)
  entry        "first_downbeat": the track starts on its first downbeat;
               {"kind": "downbeat_before_picture", "max_s": 0.9}: the first picture shot after dialogue shots starts
               on a downbeat, the entry point lies at most max_s seconds into the track (silence before the first note)
Only picture shots (kind picture/ambience) move; dialogue, external clips, titles (TXT) and shots with
"free_timing" stay. "out_limits" on a shot limits its new out point. Required downbeats (required_downbeats) and
absolute limits for cuts (limits) are also set in retime.
"""
import copy
import json
import pathlib

from .legacy import analysis_from_legacy, music_from_legacy
from .util import CutterError, MISSING, read_json


def _beats(music, cue_config, cue, files):
    import numpy as np
    from .music import beats_for
    k = cue_config.get(cue, {})
    source = k.get("beats_from", cue)
    d = beats_for(music, source, files.get(source))
    if d is None:
        raise CutterError(f"Music cue {cue}: no beats for {source} in the music analysis", MISSING,
                          "Analyze first: cutter music analyze")
    phase = k.get("phase", d.get("phase", 0))
    b = np.array(d["beats"])
    return {"b": b, "db": b[phase::4], "phase": phase, "beats": d["beats"]}


def _anchor_index(anchor, shots, first, last, default_edge="start"):
    """Index of the shot at whose start (start) or after whose end (end) the anchor lies; (kind, index)."""
    if "time" in anchor:
        return ("time", anchor["time"])
    if anchor.get("end"):
        return ("end", None)
    edge = anchor.get("edge", default_edge)
    if "tag" in anchor:
        i = next((i for i, (_, s) in enumerate(shots) if s.get("tag") == anchor["tag"]), None)
        if i is None:
            raise CutterError(f"Music anchor: tag {anchor['tag']!r} does not exist in the edit list", MISSING)
        return ("start", i) if edge == "start" else ("end", i)
    if "section" in anchor:
        a = anchor["section"]
        if a not in first:
            raise CutterError(f"Music anchor: section {a!r} does not exist in the edit list", MISSING)
        return ("start", first[a]) if edge == "start" else ("end", last[a])
    raise CutterError(f"Music anchor without time/tag/section/end: {anchor}", MISSING)


def _required(rules, shots, first, last, start_at, end_after):
    need = set()
    if rules is None:      # default: section ends and music boundaries on downbeats
        need |= set(last.values())
        need |= {i - 1 for i in start_at if i > 0} | set(end_after)
        return need
    for r in rules:
        if "last_of" in r and r["last_of"] in last:
            need.add(last[r["last_of"]])
        elif "before_tag" in r:
            i = next((i for i, (_, s) in enumerate(shots) if s.get("tag") == r["before_tag"]), None)
            if i:
                need.add(i - 1)
        elif "tag" in r:
            need |= {i for i, (_, s) in enumerate(shots) if s.get("tag") == r["tag"]}
        elif "last_with_block" in r:
            m = [i for i, (_, s) in enumerate(shots) if s.get("block") == r["last_with_block"]]
            if m:
                need.add(m[-1])
    return need


def _matches(rule, section, s):
    return all((k == "section" and section == v) or (k in ("nr_previous", "nr", "tag") and s.get(k) == v)
               for k, v in rule.items() if k in ("section", "nr_previous", "nr", "tag"))


def align(plan, clips, music, cfg, image_analysis_dir=None, external=("TXT",)):
    """Beat alignment. Returns (new edit list, log, entry points per cue, total duration)."""
    import numpy as np
    fps = float(cfg.get("fps", 25))
    cue_cfg = cfg.get("cues", {})
    music = music_from_legacy(music)
    C = clips
    E = copy.deepcopy(plan)
    shots = [(sec["name"], s) for sec in E["sections"] for s in sec["shots"]]
    first, last = {}, {}
    for i, (sn, s) in enumerate(shots):
        first.setdefault(sn, i); last[sn] = i

    cues = [m["cue"] for m in E.get("music", [])]
    files = {m["cue"]: m.get("file") for m in E.get("music", [])}
    M = {c: _beats(music, cue_cfg, c, files) for c in cues}
    Q = {m["cue"]: m.get("source_start", 0.0) for m in E.get("music", [])}
    for c in cues:
        if cue_cfg.get(c, {}).get("entry") == "first_downbeat":
            Q[c] = round(round(M[c]["beats"][M[c]["phase"]] * fps) / fps, 2)

    # music cues from their anchors: start at a shot or at a time, end after a shot
    start_at, end_after, time_start = {}, {}, []
    for m in E.get("music", []):
        kind, i = _anchor_index(m["from"], shots, first, last, "start")
        if kind == "time":
            time_start.append((m["cue"], i))
        elif kind == "start":
            start_at[i] = m["cue"]
        elif kind == "end" and i is not None:
            start_at[i + 1] = m["cue"]
        kind, j = _anchor_index(m["to"], shots, first, last, "end")
        if kind == "start":
            end_after[j - 1] = m["cue"]
        elif kind == "end" and j is not None:
            end_after[j] = m["cue"]
    end_of = {c: j for j, c in end_after.items()}
    need_db = _required(cfg.get("required_downbeats"), shots, first, last, start_at, end_after)
    limits = cfg.get("limits", [])

    AN = {}

    def analysis(cid):
        if cid not in AN:
            f = pathlib.Path(image_analysis_dir) / f"{cid}.json" if image_analysis_dir else None
            AN[cid] = analysis_from_legacy(read_json(f)) if f and f.exists() else None
        return AN[cid]

    def badness(cid, a, b):
        """Sum of 'bad' seconds between a and b: strong shake, clear blur, over- or under-exposure."""
        A = analysis(cid)
        if not A or b <= a:
            return 0.0
        ps = [p for p in A["per_second"] if "sharpness" in p]
        if not ps:
            return 0.0
        med_s = np.median([p["sharpness"] for p in ps]) or 1
        tot = 0.0
        for p in ps:
            ov = max(0.0, min(b, p["t"] + 1) - max(a, p["t"]))
            if ov <= 0:
                continue
            bad = max(0.0, (p["shake"] - 2.0) / 2.0) + (1.0 if p["sharpness"] < 0.35 * med_s else 0.0) \
                + (0.5 if p["exposure"]["clipped"] > 0.25 or p["exposure"]["too_dark"] > 0.5 else 0.0)
            tot += ov * min(bad, 2.0)
        return tot

    def rate(cid):
        c = C.get(cid, {}); return 30000 / 1001 if c.get("fps_raw") == "30000/1001" else fps

    def tl_dur(s):
        if s["clip"] in external:
            return s["out"] - s["in"]
        r = rate(s["clip"]); return round((s["out"] - s["in"]) * r) / r

    window, min_dur = cfg.get("window_s", 1.7), cfg.get("min_duration_s", 0.8)
    w_an, penalty, bonus = cfg.get("analysis_weight", 0.4), cfg.get("penalty_off_downbeat", 50.0), cfg.get("downbeat_bonus", 0.25)
    snap = cfg.get("cutaway_snap_s", 0.3)
    t, active, t0, log, started = 0.0, None, None, [], set()
    for i, (sn, s) in enumerate(shots):
        if i in start_at:
            active, t0 = start_at[i], t
            started.add(active)
            e = cue_cfg.get(active, {}).get("entry")
            if isinstance(e, dict) and e.get("kind") == "downbeat_before_picture":
                # montage start (first picture shot) on a downbeat; entry only in the silence before the first note
                off = 0.0; j = i
                while j < len(shots) and shots[j][1]["kind"] == "dialogue":
                    off += tl_dur(shots[j][1]); j += 1
                mx = e.get("max_s", 0.9)
                cand = [b - off for b in M[active]["db"] if 0 <= b - off <= mx] or \
                       [b - off for b in M[active]["b"] if 0 <= b - off <= mx] or [0.0]
                Q[active] = round(round(cand[0] * fps) / fps, 2)
        for cue, zt in time_start:
            if active is None and cue not in started and i <= end_of.get(cue, len(shots)) and t + tl_dur(s) > zt:
                active, t0 = cue, zt
                started.add(cue)
        d0 = tl_dur(s)
        if active and s["kind"] in ("picture", "ambience") and s["clip"] not in external and not s.get("free_timing"):
            m = M[active]; r = rate(s["clip"]); c = C[s["clip"]]; q = Q[active]
            beats = t0 + (m["b"] - q); dbs = set(np.round(t0 + (m["db"] - q), 4))
            g = s.get("out_limits")
            lo_out, hi_out = (g if g else (s["in"] + max(min_dur, d0 - window), s["in"] + d0 + window))
            hi_out = min(hi_out, c["duration"])
            lo, hi = t + (lo_out - s["in"]), t + (hi_out - s["in"])
            for rule in limits:
                if _matches(rule, sn, s):
                    if "cut_min" in rule:
                        lo = max(lo, rule["cut_min"])
                    if "cut_max" in rule:
                        hi = min(hi, rule["cut_max"])
            if "cut_min" in s:
                lo = max(lo, s["cut_min"])
            if "cut_max" in s:
                hi = min(hi, s["cut_max"])
            best = None
            for bt in beats:
                E25 = round(bt * fps) / fps
                if not (lo <= E25 <= hi):
                    continue
                isdb = round(bt, 4) in dbs
                D = E25 - t
                # analysis: extra seconds with flaws cost, removed ones save
                qa = badness(s["clip"], s["in"] + d0, s["in"] + D) if D > d0 else -badness(s["clip"], s["in"] + D, s["in"] + d0)
                cost = abs(D - d0) + w_an * qa + (0 if isdb or i not in need_db else penalty) - (bonus if isdb else 0)
                if best is None or cost < best[0]:
                    best = (cost, E25, bt, isdb, qa)
            if best is None:
                log.append(dict(nr=s["nr"], clip=s["clip"], cue=active, error="no beat in the allowed range"))
            else:
                _, E25, bt, isdb, qa = best
                N = round((E25 - t) * r); D = N / r
                old = (s["in"], s["out"])
                nd = 4 if r != fps else 2
                s["out"] = round(s["in"] + D, nd)
                if r == fps:
                    s["out"] = round(round(s["out"] * fps) / fps, 2)
                mt = bt - t0 + q; k = int(np.argmin(np.abs(m["b"] - mt)))
                log.append(dict(nr=s["nr"], nr_previous=s.get("nr_previous"), clip=s["clip"], cue=active, old=old,
                                new=(s["in"], s["out"]), d_old=d0, d_new=tl_dur(s), cut=t + tl_dur(s),
                                beat=(k - m["phase"]) % 4 + 1, downbeat=isdb, required=i in need_db, analysis=round(qa, 2)))
        # cutaways under music: start on the nearest beat (moved by at most snap seconds)
        if active and s.get("cutaways"):
            m = M[active]; q = Q[active]; beats = t0 + (m["b"] - q)
            for e in s["cutaways"]:
                a_abs = t + e["at"]; j = int(np.argmin(np.abs(beats - a_abs)))
                if abs(beats[j] - a_abs) <= snap:
                    new_at = round(round((beats[j] - t) * fps) / fps, 2)
                    dur = e["out"] - e["in"]
                    if new_at + dur > tl_dur(s):
                        e["out"] = round(e["in"] + (tl_dur(s) - new_at), 2)
                    e["at"] = new_at
        t += tl_dur(s)
        if i in end_after:
            active = None
    for sec in E["sections"]:
        for x in sec["shots"]:
            x.pop("out_limits", None)
    for m in E.get("music", []):
        m["source_start"] = Q[m["cue"]]
    return E, log, Q, t


def log_text(log, Q, total, before=None):
    lines = ["Music entry points: " + ", ".join(f"{c} at {q:.3f} s" for c, q in Q.items()),
             "shot (in the previous cut)  cue  clip  in - out -> new out  duration  cut at  beat"]
    for L in log:
        if "error" in L:
            lines.append(f"{L['nr']:3d} {L['cue']} {L['clip']} ERROR {L['error']}"); continue
        lines.append(f"{L['nr']:3d} ({'was ' + str(L['nr_previous']) if L['nr_previous'] else 'new':>7}) {L['cue']} {L['clip']} "
                     f"{L['old'][0]:7.2f}-{L['old'][1]:8.3f} -> {L['new'][1]:8.3f}  duration {L['d_old']:5.2f}->{L['d_new']:5.2f}  "
                     f"cut {L['cut']:7.2f}  beat {L['beat']}{' DOWNBEAT' if L['downbeat'] else ''}"
                     f"{' (required)' if L['required'] else ''}  analysis {L['analysis']:+.2f}")
    lines.append(f"Length now {total:.2f} s" + (f" (before {before:.2f} s, difference {total - before:+.2f} s)" if before else ""))
    return "\n".join(lines)


def duration(edit, clips, fps=25.0, external=("TXT",)):
    """Timeline duration of an edit list (29.97 fps clips rounded to whole frames)."""
    t = 0.0
    for sec in edit["sections"]:
        for s in sec["shots"]:
            if s["clip"] in external:
                t += s["out"] - s["in"]
            else:
                c = clips.get(s["clip"], {})
                r = 30000 / 1001 if c.get("fps_raw") == "30000/1001" else fps
                t += round((s["out"] - s["in"]) * r) / r
    return t
