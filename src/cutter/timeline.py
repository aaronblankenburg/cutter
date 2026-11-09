"""Build timelines (OTIO, FCP 7 XML, SRT) from edit.json and assemble audio levels.

build():     edit.json + clips.json -> <out>.otio, <out>.fcp7.xml, <out>.srt
  Tracks: V1 picture, V2 map, V3 route (a map over the first shot after the intro, from edit.intro), V4 cutaways
  (picture over running dialogue), V5 overview (optional), A1 dialogue, A2 music, plus markers.
  Sequence 1920x1080. Clips with a smaller resolution are scaled to fill the frame through "Basic Motion"
  (e.g. 150 %); maps stay at 100 %. Music is taken from "file" (relative to the project) or looked up by title
  ("search") in the music folders (cutter.toml → timeline.music_dirs). "source_start" moves the entry point into the track, e.g. so that a
  downbeat falls on a cut.
assemble():  build the timeline from the beat-aligned edit list, set the camera audio of picture shots (lowered
  under music, otherwise brought to a target loudness) and write the final edit.json.

Legacy (German-keyed) edit lists are read transparently; their timelines keep the legacy OTIO metadata keys, so
existing timelines are reproduced exactly.
Requires opentimelineio and otio-fcp-adapter; ffprobe/ffmpeg on PATH.
"""
import json
import pathlib
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from xml.dom import minidom

from .legacy import EXTERNAL_KEYS, edit_from_legacy, otio_meta_in, otio_meta_out
from .util import CutterError, MISSING, dump_json, lufs, read_json

AUDIO_EXT = {".ogg", ".mp3", ".wav", ".flac", ".m4a", ".opus"}
FRAME_W, FRAME_H = 1920, 1080
TRACK_NAMES = {"V1": "V1 Picture", "V2": "V2 Map", "V3": "V3 Route", "V4": "V4 Cutaways", "V5": "V5 Overview",
               "A1": "A1 Dialogue", "A2": "A2 Music"}
TEXTS = {"overview_note": "Overview, cross-fades into the map during the zoom",
         "reason_under_music": "Camera audio lowered under music",
         "reason_ambience": "Ambience at about {target:g} LUFS (raw {raw:.1f})"}


def probe_size(path):
    """Width and height of the first video stream (also PNG), else None."""
    if not shutil.which("ffprobe") or not pathlib.Path(path).exists():
        return None
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        w, h = (int(x) for x in r.stdout.strip().split(",")[:2])
        return w, h
    except ValueError:
        return None


def probe_duration(path):
    if not shutil.which("ffprobe") or not pathlib.Path(path).exists():
        return None
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def fill_scale(w, h, rotation=0):
    """Scale in percent so that the picture fills the 1920x1080 frame."""
    if rotation in (90, 270, -90):
        w, h = h, w
    return max(FRAME_W / w, FRAME_H / h) * 100


def fcp_file_meta(w, h):
    return {"media": {"video": {"samplecharacteristics": {"width": w, "height": h, "pixelaspectratio": "square",
                                                          "anamorphic": "FALSE", "fielddominance": "none"}},
                      "audio": {}}}


def fcp_motion(scale):
    return {"filter": [{"effect": {"name": "Basic Motion", "effectid": "basic", "effectcategory": "motion",
                                   "effecttype": "motion", "mediatype": "video",
                                   "parameter": [{"parameterid": "scale", "name": "Scale", "valuemin": 0,
                                                  "valuemax": 1000, "value": f"{scale:.4g}"}]}}]}


def tidy_fcp_xml(path):
    """Move inserted elements to where FCP 7 expects them (filter/media last)."""
    tree = ET.parse(path)
    for el in tree.iter():
        if el.text is not None and not el.text.strip():
            el.text = None
        if el.tail is not None and not el.tail.strip():
            el.tail = None
    root = tree.getroot()
    for parent, tag in ((e, "filter") for e in root.iter("clipitem")):
        for ch in parent.findall(tag):
            parent.remove(ch); parent.append(ch)
    for parent in root.iter("file"):
        for ch in parent.findall("media"):
            parent.remove(ch); parent.append(ch)
    for seq in root.iter("sequence"):          # media after name/duration/rate
        med, rate = seq.find("media"), seq.find("rate")
        if med is not None and rate is not None:
            seq.remove(med); seq.insert(list(seq).index(rate) + 1, med)
    # OTIO sorts metadata keys alphabetically; restore the FCP 7 order
    order = {"samplecharacteristics": ["rate", "width", "height", "anamorphic", "pixelaspectratio", "fielddominance"],
             "effect": ["name", "effectid", "effectcategory", "effecttype", "mediatype", "parameter"],
             "parameter": ["parameterid", "name", "valuemin", "valuemax", "value"],
             "media": ["video", "audio"], "rate": ["timebase", "ntsc"]}
    for tag, keys in order.items():
        for el in root.iter(tag):
            kids = sorted(el, key=lambda c: keys.index(c.tag) if c.tag in keys else len(keys))
            el[:] = kids
    xml = minidom.parseString(ET.tostring(root, encoding="UTF-8")).toprettyxml(indent="    ", encoding="UTF-8")
    pathlib.Path(path).write_bytes(xml)


def external_clips(edit, config_external=None):
    """Clips outside clips.json (e.g. a finished intro): {id: {file, duration, fps}}.

    In edit.json as "external_clips": {"V0": {"file": "Intro.mp4", "duration": 23.1, "fps": 25}}. The project
    configuration can map older edit lists: timeline.external_clips = {"V0": {"key": "intro_clip"}} takes the
    values from edit[<key>].
    """
    out = dict(edit.get("external_clips") or {})
    for cid, spec in (config_external or {}).items():
        if cid in out or not isinstance(spec, dict):
            continue
        if spec.get("key") and spec["key"] in edit:
            out[cid] = {EXTERNAL_KEYS.get(k, k): v for k, v in edit[spec["key"]].items()}
        elif spec.get("file"):
            out[cid] = spec
    return out


def resolve_clips(clips, media_roots=None):
    """Clip paths from the named footage folders of cutter.toml (portable), falling back to the stored absolute path."""
    for c in clips.values():
        ref = c.get("media")
        if isinstance(ref, dict) and media_roots and ref.get("root") in media_roots:
            c["path"] = str(pathlib.Path(media_roots[ref["root"]]) / ref["path"])
    return clips


def load_edit(path):
    """Read an edit list (English or legacy). Returns (edit, was_legacy)."""
    return edit_from_legacy(read_json(path))


def build(edit_path, clips_path, base, out, ns="cutter", music_dirs=("music",), config_external=None,
          track_names=None, texts=None, metadata_keys="auto", media_roots=None, xml_suffix=".fcp7.xml", project_dir=None):
    """Build the timeline. Returns a report (dict); writes <out>.otio, <out>_fcp7.xml and, if needed, <out>.srt."""
    try:
        import opentimelineio as otio
    except ImportError:
        raise CutterError("opentimelineio is missing (it is in the Docker image: bin/cutter timeline ...)", MISSING)
    RT, TR = otio.opentime.RationalTime, otio.opentime.TimeRange
    tracks = dict(TRACK_NAMES, **(track_names or {}))
    text = dict(TEXTS, **(texts or {}))

    edit, legacy = load_edit(edit_path)
    if metadata_keys in ("legacy", "en"):
        legacy = metadata_keys == "legacy"
    meta_out = lambda m: otio_meta_out(m, legacy)
    clips = resolve_clips({c["id"]: c for c in read_json(clips_path)}, media_roots)
    base, fps = pathlib.Path(base), float(edit.get("fps", 25))
    folders = [base / o for o in music_dirs] or [base]      # absolute music_dirs stay absolute
    for cid, x in external_clips(edit, config_external).items():
        wh = probe_size(base / x["file"]) or (FRAME_W, FRAME_H)
        clips[cid] = {"path": str(base / x["file"]), "file": x["file"], "duration": x["duration"], "fps": x["fps"],
                      "fps_raw": f'{x["fps"]}/1', "width": wh[0], "height": wh[1], "rotation": 0}

    def rate_of(c):
        return 30000 / 1001 if c.get("fps_raw") == "30000/1001" else float(c.get("fps") or fps)

    def rt(sec, rate=fps):
        return RT(round(sec * rate), rate)

    def ext_ref(path, length=None, rate=fps):
        rng = TR(rt(0, rate), rt(length, rate)) if length else None
        return otio.schema.ExternalReference(target_url=pathlib.Path(path).as_uri(), available_range=rng)

    def gap(sec):
        return otio.schema.Gap(source_range=TR(RT(0, fps), rt(sec)))

    # --- picture and dialogue
    tl = otio.schema.Timeline(name=edit["title"], global_start_time=RT(0, fps))
    tl.metadata["fcp_xml"] = {"media": {"video": {"format": {"samplecharacteristics": {
        "rate": {"timebase": int(fps), "ntsc": "FALSE"}, "width": FRAME_W, "height": FRAME_H,
        "anamorphic": "FALSE", "pixelaspectratio": "square", "fielddominance": "none"}}}, "audio": {}}}
    scaled = []
    V1 = otio.schema.Track(name=tracks["V1"], kind=otio.schema.TrackKind.Video)
    A1 = otio.schema.Track(name=tracks["A1"], kind=otio.schema.TrackKind.Audio)
    for sec in edit["sections"]:
        for s in sec["shots"]:
            base_meta = {"nr": s["nr"], "section": sec["name"], "kind": s["kind"], "note": s["note"], "tag": s.get("tag")}
            if s["clip"] == "TXT":
                for tr in (V1, A1):
                    g = gap(s["out"] - s["in"]); g.metadata.update({ns: meta_out(base_meta)}); tr.append(g)
                continue
            if s["clip"] not in clips:
                raise CutterError(f'Shot {s["nr"]}: clip {s["clip"]} is not in {clips_path}', MISSING)
            c = clips[s["clip"]]
            r = rate_of(c)
            if s["out"] > c["duration"] + 0.05:
                raise CutterError(f'Shot {s["nr"]}: out {s["out"]} is past the end of {s["clip"]} ({c["duration"]:.2f} s)')
            name = f'{s["nr"]:02d} {s["clip"]} {pathlib.Path(c["path"]).name}'
            w, h = c.get("width") or FRAME_W, c.get("height") or FRAME_H
            scale = fill_scale(w, h, c.get("rotation") or 0)
            for tr in (V1, A1):
                m = dict(base_meta)
                extra = {}
                ref = ext_ref(c["path"], c["duration"], r)
                ref.metadata["fcp_xml"] = fcp_file_meta(w, h)
                if tr is V1:
                    m.update({"source_resolution": f"{w}x{h}", "scale_percent": round(scale, 4)})
                    extra["fcp_xml"] = fcp_motion(scale)
                elif s.get("audio"):
                    # clip level for the dialogue; FCP 7 allows at most +12 dB, the full value is in the metadata
                    m["audio"] = s["audio"]
                    g = 10 ** (max(-60.0, min(12.0, s["audio"].get("gain_db", 0.0))) / 20)
                    extra["fcp_xml"] = {"filter": [{"effect": {"name": "Audio Levels", "effectid": "audiolevels",
                                                               "effectcategory": "audiolevels", "effecttype": "audiolevels",
                                                               "mediatype": "audio",
                                                               "parameter": [{"parameterid": "level", "name": "Level", "valuemin": 0,
                                                                              "valuemax": 3.98109, "value": f"{g:.5f}"}]}}]}
                meta = json.loads(json.dumps({ns: meta_out(m), **extra}))
                tr.append(otio.schema.Clip(name=name, media_reference=ref,
                                           source_range=TR(rt(s["in"], r), rt(s["out"] - s["in"], r)), metadata=meta))
            if abs(scale - 100) > 0.01:
                scaled.append(f'Shot {s["nr"]} {s["clip"]} {w}x{h} -> {scale:.0f} %')

    # --- positions from the finished picture track (29.97 fps clips round to whole frames)
    pos, sec_rng = {}, {}
    for ch in V1:
        m = otio_meta_in(ch.metadata.get(ns, {}))
        rng = V1.range_of_child(ch)
        a_, b_ = rng.start_time.to_seconds(), rng.end_time_exclusive().to_seconds()
        if m.get("tag"):
            pos[m["tag"]] = (a_, b_)
        lo, hi = sec_rng.get(m.get("section"), (a_, b_))
        sec_rng[m.get("section")] = (min(lo, a_), max(hi, b_))
    total = V1.duration().to_seconds()

    def anchor(d):
        if "time" in d:
            return d["time"]
        if d.get("end"):
            return total
        try:
            rng = pos[d["tag"]] if "tag" in d else sec_rng[d["section"]]
        except KeyError as ex:
            raise CutterError(f"Anchor {d} does not exist in the edit list ({ex})", MISSING)
        return rng[0] if d.get("edge", "start") == "start" else rng[1]

    # --- map over the first shot after the intro (optional)
    intro = edit.get("intro") or {}
    maps = []
    for key, track_name in (("map", tracks["V2"]), ("route", tracks["V3"])):
        rel = intro.get(key)
        if not rel:
            continue
        tr = otio.schema.Track(name=track_name, kind=otio.schema.TrackKind.Video)
        tr.append(gap(intro["map_start"]))
        ref = ext_ref(base / rel, intro["map_duration"])
        wh = probe_size(base / rel)
        if wh:
            ref.metadata["fcp_xml"] = fcp_file_meta(*wh)
        # maps are not scaled to the frame: 100 %
        tr.append(otio.schema.Clip(name=pathlib.Path(rel).name, media_reference=ref,
                                   source_range=TR(rt(0), rt(intro["map_duration"])),
                                   metadata={ns: meta_out({"note": intro.get("map_note", ""), "scale_percent": 100}),
                                             "fcp_xml": fcp_motion(100)}))
        maps.append(tr)
    # --- overview map: above everything, cross-fades into the map during the zoom
    V5 = None
    if intro.get("overview"):
        V5 = otio.schema.Track(name=tracks["V5"], kind=otio.schema.TrackKind.Video)
        V5.append(gap(intro["map_start"]))
        rel = intro["overview"]
        ref = ext_ref(base / rel, intro["map_duration"])
        wh = probe_size(base / rel)
        if wh:
            ref.metadata["fcp_xml"] = fcp_file_meta(*wh)
        V5.append(otio.schema.Clip(name=pathlib.Path(rel).name, media_reference=ref,
                                   source_range=TR(rt(0), rt(intro["map_duration"])),
                                   metadata={ns: meta_out({"note": intro.get("overview_note", text["overview_note"]),
                                                           "scale_percent": 100}), "fcp_xml": fcp_motion(100)}))

    # --- music
    files = [p for d in folders if d.is_dir() for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT]
    A2, cur, report, music_report = otio.schema.Track(name=tracks["A2"], kind=otio.schema.TrackKind.Audio), 0.0, [], []
    norm = lambda x: re.sub(r"[\s_\-]+", " ", x.lower()).strip()
    for m in edit.get("music", []):
        start, end = anchor(m["from"]), anchor(m["to"])
        if m.get("file"):                      # relative to the project folder (music/…)
            path = pathlib.Path(m["file"]) if pathlib.Path(m["file"]).is_absolute() else pathlib.Path(project_dir or base) / m["file"]
            hit = path if path.exists() else None
        else:
            hit = next((p for p in sorted(files) if norm(m["search"]) in norm(p.stem)), None)
            path = hit or (folders[0] / f'{m["search"]}.mp3')
        length = probe_duration(path)
        need = m.get("source_start", 0.0) + (end - start)
        if start > cur:
            A2.append(gap(start - cur))
        A2.append(otio.schema.Clip(name=f'{m["cue"]} {m["title"]} ({m["artist"]}, {m["license"]})',
                                   media_reference=ext_ref(path, length or need),
                                   source_range=TR(rt(m.get("source_start", 0.0)), rt(end - start)),
                                   metadata={ns: meta_out({"note": m.get("note", "")})}))
        cur = end
        warn = "" if hit else "  (file not found)"
        if length and need > length + 0.05:
            warn += f"  (track only {length:.1f} s long, needed {need:.1f} s – loop or shorten)"
        report.append(f'  {m["cue"]} {start:7.2f}–{end:7.2f} s  {path.name}{warn}')
        music_report.append({"cue": m["cue"], "start": round(start, 3), "end": round(end, 3), "file": str(path),
                             "found": bool(hit), "too_short": bool(length and need > length + 0.05)})

    # --- cutaways: picture over running dialogue (field "cutaways" of a shot)
    V4, cur4, n_cut = otio.schema.Track(name=tracks["V4"], kind=otio.schema.TrackKind.Video), 0.0, 0
    shot_rng = {}
    for ch in V1:
        m = otio_meta_in(ch.metadata.get(ns, {}))
        if m.get("nr") is not None:
            r_ = V1.range_of_child(ch)
            shot_rng[m["nr"]] = (r_.start_time.to_seconds(), r_.end_time_exclusive().to_seconds())
    for sec in edit["sections"]:
        for s in sec["shots"]:
            for e in s.get("cutaways", []):
                a0, a1 = shot_rng[s["nr"]]
                c = clips[e["clip"]]
                r = rate_of(c)
                start = round((a0 + e["at"]) * fps) / fps
                dur = e["out"] - e["in"]
                if e["out"] > c["duration"] + 0.05 or e["in"] < 0:
                    raise CutterError(f'Cutaway in shot {s["nr"]}: {e["clip"]} {e["in"]}-{e["out"]} is outside the clip')
                if start + dur > a1 + 0.05:
                    raise CutterError(f'Cutaway in shot {s["nr"]} runs past the end of the shot')
                if start < cur4 - 0.01:
                    raise CutterError(f'Cutaway in shot {s["nr"]} overlaps the previous cutaway')
                if start > cur4:
                    V4.append(gap(start - cur4))
                w, h = c.get("width") or FRAME_W, c.get("height") or FRAME_H
                scale = fill_scale(w, h, c.get("rotation") or 0)
                ref = ext_ref(c["path"], c["duration"], r)
                ref.metadata["fcp_xml"] = fcp_file_meta(w, h)
                cl = otio.schema.Clip(name=f'{s["nr"]:02d}e {e["clip"]} {pathlib.Path(c["path"]).name}', media_reference=ref,
                                      source_range=TR(rt(e["in"], r), rt(dur, r)),
                                      metadata={ns: meta_out({"cutaway_of": s["nr"], "note": e.get("note", ""),
                                                              "scale_percent": round(scale, 4)}),
                                                "fcp_xml": fcp_motion(scale)})
                V4.append(cl)
                cur4 = start + V4.range_of_child(cl).duration.to_seconds()
                n_cut += 1

    # --- subtitles (field "subtitles" of a shot, times relative to the start of the shot) -> <out>.srt
    subs = []
    for sec in edit["sections"]:
        for s in sec["shots"]:
            for u in s.get("subtitles", []):
                a0, a1 = shot_rng[s["nr"]]
                subs.append((max(a0, a0 + u["from"]), min(a1, a0 + u["to"]), u["text"]))
    subs.sort()

    tl.tracks.extend([V1] + maps + ([V4] if n_cut else []) + ([V5] if V5 else []) + [A1, A2])
    for mk in edit.get("markers", []):
        t0 = anchor(mk)
        tl.tracks.markers.append(otio.schema.Marker(name=mk["name"], marked_range=TR(rt(t0), rt(mk.get("duration", 0.0))),
                                                    color=getattr(otio.schema.MarkerColor, mk.get("color", "YELLOW"))))
    out = str(out)
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(tl, f"{out}.otio")
    otio.adapters.write_to_file(tl, f"{out}{xml_suffix}", adapter_name="fcp_xml")
    tidy_fcp_xml(f"{out}{xml_suffix}")
    srt = None
    if subs:
        def ts(x):
            ms = round(x * 1000); h, ms = divmod(ms, 3600000); m_, ms = divmod(ms, 60000); s_, ms = divmod(ms, 1000)
            return f"{h:02d}:{m_:02d}:{s_:02d},{ms:03d}"
        srt_text = "\n".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(subs, 1))
        srt = f"{out}.srt"
        pathlib.Path(srt).write_text(srt_text, encoding="utf-8")
    n = sum(1 for _ in V1.find_clips())
    lines = [f"{out}.otio and {out}{xml_suffix} written: {int(total // 60)}:{total % 60:05.2f}, {n} clips",
             f"Scaled to 1920x1080 ({len(scaled)}):", *("  " + x for x in scaled or ["none"])]
    if n_cut:
        lines.append(f"Cutaways over dialogue on V4: {n_cut}")
    if subs:
        lines.append(f"Subtitles: {len(subs)} to {out}.srt")
    lines += ["Music:", *report]
    return {"otio": f"{out}.otio", "xml": f"{out}{xml_suffix}", "srt": srt, "duration": round(total, 3), "clips": n,
            "scaled": len(scaled), "cutaways": n_cut, "subtitles": len(subs), "music": music_report,
            "legacy_metadata": legacy, "text": "\n".join(lines)}


# ---------------------------------------------------------------- assemble audio levels

def assemble(retimed_path, clips_path, base, out_intermediate, target_edit, ns="cutter", music_dirs=("music",),
             config_external=None, track_names=None, texts=None, audio=None, external_ids=("TXT",), media_roots=None,
             project_dir=None):
    """Build the timeline from the beat-aligned edit list, set camera audio levels and write the final edit list.

    Picture shots (picture/ambience) under music (overlap > overlap_s) get under_music_db, the others are brought
    to ambience_target_lufs by EBU R128 (limited to ambience_min_db…ambience_max_db). Shots that are already muted
    (gain_db <= muted_below_db) stay as they are. The result is always written with English keys.
    """
    import opentimelineio as otio
    audio = audio or {}
    text = dict(TEXTS, **(texts or {}))
    E, _ = load_edit(retimed_path)
    C = resolve_clips({c["id"]: c for c in read_json(clips_path)}, media_roots)
    report = build(retimed_path, clips_path, base, out_intermediate, ns, music_dirs, config_external, track_names, texts,
                   media_roots=media_roots, project_dir=project_dir)
    tl = otio.adapters.read_from_file(report["otio"])
    A2 = next(t for t in tl.tracks if t.name.startswith("A2"))
    V1 = next(t for t in tl.tracks if t.name.startswith("V1"))
    music = [(A2.range_of_child(c).start_time.to_seconds(), A2.range_of_child(c).end_time_exclusive().to_seconds())
             for c in A2 if isinstance(c, otio.schema.Clip)]
    overlap = audio.get("overlap_s", 0.3)
    under = set()
    for ch in V1:
        m = otio_meta_in(ch.metadata.get(ns, {})); r = V1.range_of_child(ch)
        if any(min(r.end_time_exclusive().to_seconds(), mb) - max(r.start_time.to_seconds(), ma) > overlap for ma, mb in music):
            under.add(m.get("nr"))
    n_under = n_measured = 0
    for sec in E["sections"]:
        for s in sec["shots"]:
            if s["kind"] in ("picture", "ambience") and s["clip"] not in external_ids and \
                    not (s.get("audio", {}).get("gain_db", 0) <= audio.get("muted_below_db", -20.0)):
                if s["nr"] in under:
                    s["audio"] = {"gain_db": audio.get("under_music_db", -10.0), "reason": text["reason_under_music"]}
                    n_under += 1
                else:
                    L = lufs(C[s["clip"]]["path"], s["in"], s["out"] - s["in"])
                    if L is not None:
                        target = audio.get("ambience_target_lufs", -28.0)
                        s["audio"] = {"gain_db": round(max(audio.get("ambience_min_db", -12.0), min(audio.get("ambience_max_db", 10.0), target - L)), 1),
                                      "reason": text["reason_ambience"].format(target=target, raw=L)}
                        n_measured += 1
            s.pop("out_limits", None)
    if audio.get("note"):
        E["audio_note"] = audio["note"]
    pathlib.Path(target_edit).parent.mkdir(parents=True, exist_ok=True)
    dump_json(target_edit, E, ensure_ascii=False, indent=1)
    n_sub = sum(1 for sec in E["sections"] for s in sec["shots"] if s.get("subtitles"))
    return {"edit": str(target_edit), "under_music": len(under), "lowered": n_under, "measured": n_measured,
            "shots_with_subtitles": n_sub, "intermediate": report}
