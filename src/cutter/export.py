"""A package for another editing application whose media references point to the original files on that computer.

    export_package(project, cut, target, out, to="premiere")

writes into `out` (the folder that goes next to the footage as `<root>/_cutlery`, see paths.py):

    <title>_<cut>.xml      the timeline: FCP 7 XML for Premiere Pro (fcp7.py),
    <title>_<cut>.fcpxml   or FCPXML 1.10 for Final Cut Pro and DaVinci Resolve (fcpxml.py)
    <title>_<cut>.srt      subtitles
    music/                 the music files and LICENSES.txt
    titles/                titles, credits and end-text cards as transparent PNG (titles.py)
    maps/                  overview and route maps

The timeline is the one `cutter timeline` builds (timeline.make), never a draft with proxies: the same cuts at the
same frames. Everything FilmCraft adds that the target application can take is carried over: picture size (Basic
Motion), dialogue levels, music ducking as volume keyframes (filmcraft.music_keyframes), titles as pictures with
opacity keyframes. What it cannot take (stabilisation, audio cleanup) and the review decisions become markers.
The clip metadata comes from clips.json: for stand-ins (standin.py) it is the original's, so start timecode, frame
rate, duration and size match the files the editor relinks to.
"""
import json
import pathlib
import re
import shutil
from fractions import Fraction
from urllib.parse import unquote, urlsplit

from . import color, filmcraft, paths, timeline, titles
from .legacy import otio_meta_in
from .util import CutterError, MISSING, read_json

FORMATS = ("premiere", "fcp", "resolve", "filmcraft")
TIMELINE_SUFFIX = {"premiere": ".xml", "fcp": ".fcpxml", "resolve": ".fcpxml", "filmcraft": ".fcproj"}
AUDIO_EXT = timeline.AUDIO_EXT


# ---------------------------------------------------------------- rates and timecode

def rate(value):
    """(Fraction fps, timebase, ntsc) for a frame rate given as number or "30000/1001"."""
    if isinstance(value, str) and "/" in value:
        num, den = value.split("/")
        fps = Fraction(int(num), int(den))
    else:
        fps = Fraction(str(value or 25)).limit_denominator(1001)
    for base in (24, 30, 48, 60, 120):
        if abs(float(fps) - base * 1000 / 1001) < 0.005:
            return Fraction(base * 1000, 1001), base, True
    return fps, max(1, round(float(fps))), False


def timecode_frames(tc, timebase, drop):
    """Frames since 00:00:00:00 of a timecode string (drop-frame for ';')."""
    if not tc:
        return 0
    h, m, s, f = (int(x) for x in re.split(r"[:;.]", tc)[:4])
    frames = ((h * 60 + m) * 60 + s) * timebase + f
    if drop:
        n = round(timebase / 15)                       # 2 at 29.97, 4 at 59.94
        minutes = h * 60 + m
        frames -= n * (minutes - minutes // 10)
    return frames


# ---------------------------------------------------------------- the model

def timeline_args(p):
    return dict(ns=p.otio_namespace, music_dirs=p.music_dirs(), config_external=p["timeline"]["external_clips"],
                track_names=p["timeline"]["track_names"], texts=p["timeline"]["texts"], media_roots=p.media_roots(),
                project_dir=p.folder)


def package_url(targ, path):
    """Where a file the package carries (music, maps) is on the editing computer."""
    path = pathlib.Path(path)
    return paths.url(targ, f'{"music" if path.suffix.lower() in AUDIO_EXT else "maps"}/{path.name}', under_package=True)


def _seconds(t):
    return t.to_seconds()


def build_model(p, cut, target, relink=None):
    """The NLE-neutral sequence of a cut (dict); see fcp7.write for how it is used.
    relink: {rel_path in clips.json: rel_path on the editing computer} for originals renamed or moved since."""
    relink = relink or {}
    edit_p = p.path("edit", cut)
    if not edit_p.exists():
        raise CutterError(f"Edit list not found: {edit_p}", MISSING)
    targ = paths.check_target(target)
    t = timeline.make(edit_p, p.path("clips"), p.media_base, originals=True, **timeline_args(p))
    tl, clips, edit, ns = t["timeline"], t["clips"], t["edit"], t["ns"]
    fps, timebase, ntsc = rate(edit.get("fps", 25))
    frames = lambda sec: round(sec * fps)
    fc = filmcraft.load_config(p.path_of(p["filmcraft"]["config"]) if p["filmcraft"].get("config") else None)
    model = {"name": edit.get("title") or f'{p.get("title") or "cutter"} ({cut})', "fps": fps, "timebase": timebase,
             "ntsc": ntsc, "width": timeline.FRAME_W, "height": timeline.FRAME_H, "video": [], "audio": [],
             "markers": [], "files": {}, "package": {}, "subtitles": t["subtitles"], "notes": []}
    shots = {s["nr"]: s for sec in edit["sections"] for s in sec["shots"]}

    def footage(cid):
        c = clips[cid]
        if not c.get("rel_path"):                      # an external clip (map animation, black): in the package
            return package_file(c["path"], "maps", "clip", c.get("width"), c.get("height"), seconds=c.get("duration"),
                                has_audio=timeline.probe_has_audio(c["path"]))
        if cid not in model["files"]:
            src_fps, src_base, src_ntsc = rate(c.get("fps_raw") or c.get("fps") or 25)
            rel = relink.get(c["rel_path"], c["rel_path"])
            model["files"][cid] = {
                "id": cid, "name": paths.display_name(rel), "url": paths.url(targ, rel),
                "native": paths.join(targ, rel), "duration": round(c["duration"] * src_fps),
                "timebase": src_base, "ntsc": src_ntsc, "timecode": c.get("timecode"),
                "width": c.get("width"), "height": c.get("height"), "has_video": c.get("has_video", True),
                "has_audio": c.get("has_audio", False), "channels": c.get("audio_channels") or 2,
                "sample_rate": c.get("audio_rate") or 48000, "kind": "footage"}
        return model["files"][cid]

    def package_file(src, folder, kind, width=None, height=None, seconds=None, has_audio=None):
        src = pathlib.Path(src)
        name = src.name
        key = f"{folder}/{name}"
        if key not in model["files"]:
            model["package"][key] = src
            model["files"][key] = {"id": key, "name": name, "url": paths.url(targ, key, under_package=True),
                                   "native": paths.join(targ, key, under_package=True), "timebase": timebase,
                                   "ntsc": ntsc, "duration": frames(seconds) if seconds else None, "timecode": None,
                                   "width": width, "height": height, "has_video": kind != "audio",
                                   "has_audio": kind == "audio" if has_audio is None else has_audio, "channels": 2,
                                   "sample_rate": 48000, "kind": kind}
        return model["files"][key]

    # --- tracks from the OTIO timeline (the same frames FilmCraft and the review get)
    import opentimelineio as otio
    v1_items = {}
    for track in tl.tracks:
        kind = "video" if track.kind == otio.schema.TrackKind.Video else "audio"
        items = []
        for ch in track:
            if not isinstance(ch, otio.schema.Clip):
                continue
            rng = track.range_of_child(ch)
            start, dur = _seconds(rng.start_time), _seconds(rng.duration)
            src_in = _seconds(ch.source_range.start_time)
            meta = otio_meta_in(ch.metadata.get(ns, {}))
            tokens = ch.name.split()
            cid = tokens[1] if len(tokens) > 1 and tokens[1] in clips else None
            item = {"name": ch.name, "start": frames(start), "end": frames(start + dur), "in": frames(src_in),
                    "scale": meta.get("scale_percent"), "level_db": None, "volume": [], "opacity": [], "meta": meta}
            item["out"] = item["in"] + item["end"] - item["start"]
            if cid:
                item["file"] = footage(cid)["id"]
                if kind == "audio" and meta.get("audio") and "gain_db" in meta["audio"]:
                    item["level_db"] = meta["audio"]["gain_db"]
                if kind == "video" and meta.get("nr") is not None and track.name == tl.tracks[0].name:
                    v1_items[meta["nr"]] = item
            else:
                local = pathlib.Path(unquote(urlsplit(ch.media_reference.target_url).path))
                if local.suffix.lower() in AUDIO_EXT:             # the same folders as package_url
                    f = package_file(local, "music", "audio", seconds=timeline.probe_duration(local) or dur)
                    item["cue"] = tokens[0] if tokens else None
                else:
                    wh = timeline.probe_size(local) or (timeline.FRAME_W, timeline.FRAME_H)
                    f = package_file(local, "maps", "still", *wh, seconds=dur)
                item["file"] = f["id"]
            items.append(item)
        model[kind].append({"name": track.name, "items": items})

    total = t["total"]
    sec = lambda f: f / fps

    # --- titles, credits and end text as pictures on a track of their own
    picture_items, n_title = [], 0
    out_titles = []

    def picture(layers, start, end, opacity, band=None):
        nonlocal n_title
        n_title += 1
        key = f"titles/title_{n_title:02d}.png"
        out_titles.append((key, layers, band))
        model["files"][key] = {"id": key, "name": pathlib.Path(key).name, "url": paths.url(targ, key, under_package=True),
                               "native": paths.join(targ, key, under_package=True), "timebase": timebase, "ntsc": ntsc,
                               "duration": frames(end - start), "timecode": None, "width": titles.W,
                               "height": titles.H, "has_video": True, "has_audio": False, "kind": "still"}
        a, b = frames(start), frames(end)
        picture_items.append({"name": pathlib.Path(key).stem, "file": key, "start": a, "end": b, "in": 0, "out": b - a,
                              "scale": 100, "level_db": None, "volume": [],
                              "opacity": [(frames(tt) - a, v) for tt, v in opacity], "meta": {}})

    for tt in fc.get("titles") or []:
        op = tt.get("opacity") or [[tt["time"], 100], [tt["time"] + tt["duration"], 100]]
        picture(titles.title_layers(tt), tt["time"], tt["time"] + tt["duration"], op)
    cr = fc.get("credits")
    if cr and cr.get("layers"):
        a0 = total - cr.get("duration", 20.0)
        lines = "\n".join(fc.get("credits_music_lines") or edit.get("credits_music_lines") or [])
        picture(titles.credit_layers(cr, lines), a0, total, [(a0, 0), (a0 + 1, 100), (total - 1, 100), (total - 0.04, 0)])
    dip = None
    et = edit.get("end_text")
    if et:
        style, ec = et["style"], fc["end_text"]
        ramp = ec.get("ramp_s", 0.5)
        tag_nr = {s["tag"]: nr for nr, s in shots.items() if s.get("tag")}
        spans = []
        for card in et["cards"]:
            t0 = sec(v1_items[tag_nr[card["tag"]]]["start"])
            a_, b_ = t0 + card["from"], t0 + card["to"]
            layers, band = titles.end_card_layers(card, style, ec.get("shadow"))
            picture(layers, a_, b_, [(a_, 0), (a_ + ramp, 100), (b_ - ramp, 100), (b_ - 0.04, 0)], band)
            spans.append((a_, b_))
        dip = (spans[0][0], spans[-1][1], et.get("music_dip_db", -4.0))
    if picture_items:
        model["video"].append({"name": "V6 Titles", "items": picture_items})
    model["titles"] = out_titles

    # --- music ducking: the levels FilmCraft sets, as keyframes
    mu = fc["music"]
    dialogue = sorted((sec(it["start"]), sec(it["end"]), nr) for nr, it in v1_items.items()
                      if shots.get(nr, {}).get("kind") == "dialogue")
    low = {nr for nr, s in shots.items() if s.get("audio", {}).get("music_lower")}
    fade = dict(mu.get("fade_out_s") or {})
    fade.update({m["cue"]: float(m["fade_out_s"]) for m in edit.get("music", []) if m.get("fade_out_s")})
    music_analysis = read_json(p.path("music_analysis")) if p.path("music_analysis").exists() else None
    files = {m["cue"]: m.get("file") for m in edit.get("music", [])}
    for track in model["audio"]:
        for it in track["items"]:
            if not it.get("cue"):
                continue
            try:
                lufs = filmcraft._music_lufs(it["cue"], fc, music_analysis, p["retime"].get("cues"), files)
            except CutterError:
                model["notes"].append(f"music {it['cue']}: no loudness measured, no volume keyframes")
                continue
            a, b = sec(it["start"]), sec(it["end"])
            kf = filmcraft.music_keyframes(a, b, lufs, dialogue, low, mu, fade.get(it["cue"]), dip)
            it["volume"] = [(frames(tt) - it["start"], v) for tt, v in sorted(kf.items())]

    # --- markers: the edit list's, what has to be redone, the review decisions
    for mk in tl.tracks.markers:
        r = mk.marked_range
        model["markers"].append({"name": mk.name, "comment": "", "start": frames(_seconds(r.start_time)),
                                 "duration": frames(_seconds(r.duration)), "color": str(mk.color).lower()})
    for nr, s in sorted(shots.items()):
        it = v1_items.get(nr)
        if not it:
            continue
        if s.get("stabilize"):
            it["stabilize"] = True
            model["markers"].append({"name": f"Stabilise shot {nr}", "comment": f"{s['clip']}: stabilise (FilmCraft: "
                                     f"Warp Stabilizer, max. {fc['stabilize'].get('max_scale', 125):g} %)",
                                     "start": it["start"], "duration": it["end"] - it["start"], "color": "red",
                                     "kind": "stabilize"})
        au = s.get("audio") or {}
        if s.get("kind") == "dialogue" and au.get("noise_reduction"):
            it["noise_reduction"] = au["noise_reduction"]
        if s.get("kind") == "dialogue" and any(au.get(k) for k in ("noise_reduction", "speech_mix", "compressor", "highpass_hz")):
            what = [f"high-pass {au.get('highpass_hz', fc['audio_chain'].get('highpass_hz', 100))} Hz"]
            if au.get("noise_reduction"):
                what.append(f"noise reduction {au['noise_reduction']}")
            if au.get("speech_mix"):
                what.append(f"speech enhancement {au['speech_mix']}")
            if au.get("compressor"):
                what.append("compressor")
            model["markers"].append({"name": f"Audio cleanup shot {nr}", "comment": ", ".join(what), "start": it["start"],
                                     "duration": it["end"] - it["start"], "color": "orange",
                                     "kind": "audio"})
    dec_p = p.path("decisions", cut)
    if dec_p.exists():
        for e in read_json(dec_p).get("entries", []):
            if "t" in e:
                a = frames(e["t"]); b = frames(e.get("t_to", e["t"]))
            elif e.get("nr") in v1_items:
                a = v1_items[e["nr"]]["start"]; b = v1_items.get(e.get("nr_to", e["nr"]), v1_items[e["nr"]])["end"]
            else:
                continue
            text = " ".join(x for x in (e.get("text"), f"Why: {e['why']}" if e.get("why") else None) if x)
            model["markers"].append({"name": f"Decision: {e.get('title', e.get('id'))}", "comment": text, "start": a,
                                     "duration": max(0, b - a), "color": "purple",
                                     "kind": "decision"})
    model["markers"].sort(key=lambda m: m["start"])
    model["duration"] = frames(total)
    model["music"] = [m for m in edit.get("music", [])]
    return model


# ---------------------------------------------------------------- the package

def safe_name(text):
    return re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("_") or "cut"


def export_package(p, cut, target, out, to="premiere", relink=None):
    if to not in FORMATS:
        raise CutterError(f"Unknown export format {to} (one of {', '.join(FORMATS)})")
    from . import fcp7, fcpxml
    model = build_model(p, cut, target, relink)
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stem = safe_name(f'{p.get("title") or "film"}_{cut}')
    written = []
    for key, src in model["package"].items():
        dst = out / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        if pathlib.Path(src).exists():
            shutil.copyfile(src, dst)
            written.append(key)
        else:
            model["notes"].append(f"missing file: {src}")
    if to != "filmcraft":                                 # FilmCraft makes its titles itself
        for key, layers, band in model["titles"]:
            titles.render(layers, out / key, band)
            written.append(key)
    xml = out / f"{stem}{TIMELINE_SUFFIX[to]}"
    if to == "premiere":
        xml.write_bytes(fcp7.write(model))
    elif to == "filmcraft":
        model["notes"] += filmcraft_project(p, cut, target, xml, relink)
    else:
        xml.write_bytes(fcpxml.write(model, language=(p.get("language") or "en")[:2].lower(),
                                     applied=("stabilize",) if to == "fcp" else ()))
    written.append(xml.name)
    if model["subtitles"]:
        srt = out / f"{stem}.srt"
        srt.write_text(srt_text(model["subtitles"]), encoding="utf-8")
        written.append(srt.name)
    if model["music"]:
        lic = out / "music" / "LICENSES.txt"
        lic.parent.mkdir(parents=True, exist_ok=True)
        lic.write_text(licenses_text(model["music"]), encoding="utf-8")
        written.append("music/LICENSES.txt")
    report = {"format": to, "folder": str(out), "timeline": xml.name, "files": sorted(written),
              "clips": sorted(f["native"] for f in model["files"].values() if f["kind"] == "footage"),
              "markers": len(model["markers"]), "titles": 0 if to == "filmcraft" else len(model["titles"]),
              "notes": model["notes"],
              "music": [{k: m.get(k) for k in ("cue", "title", "artist", "license", "source", "file")} for m in model["music"]],
              "duration": round(model["duration"] / float(model["fps"]), 3)}
    return report


def filmcraft_project(p, cut, target, out_file, relink=None):
    """The FilmCraft project of a cut, built like `cutter filmcraft build` but from a timeline that points to the
    originals on the editing computer (offline here) and is sized for them. FilmCraft keeps the paths of offline
    media and finds the files when the project is opened there. Returns notes for the report."""
    targ = paths.check_target(target)
    relink = relink or {}

    def url_of(path, clip):
        if clip and clip.get("rel_path"):
            return paths.url(targ, relink.get(clip["rel_path"], clip["rel_path"]))
        return package_url(targ, path)

    edit_p = p.path("edit", cut)
    work = pathlib.Path(out_file).parent / f".{pathlib.Path(out_file).stem}.build"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    try:
        t = timeline.make(edit_p, p.path("clips"), p.media_base, originals=True, url_of=url_of, **timeline_args(p))
        timeline.write(t, work / "timeline")
        out_file = pathlib.Path(out_file)
        if out_file.exists():
            out_file.unlink()
        client = filmcraft.CliClient(filmcraft.find_cli(), target=out_file, data_dir=work / "filmcraft-data")
        music_analysis = read_json(p.path("music_analysis")) if p.path("music_analysis").exists() else None
        srt = work / "timeline.srt"
        edit = read_json(edit_p)
        r = filmcraft.finish(client, filmcraft.project_config(p), edit, work / "timeline.fcp7.xml",
                             srt if srt.exists() else None, work / "filmcraft_batch.jsonl", base=p.media_base,
                             music_analysis=music_analysis, retime_cues=p["retime"].get("cues"),
                             color_plan=color.for_cut(p, cut, edit, log=None))
        notes = [x for x in r.get("log", []) if "error" in str(x).lower()]
        stray = [m for m in filmcraft_media(out_file) if not _on_target(m, targ)]
        if stray:
            notes.append("FilmCraft refers to files that are not on the editing computer: " + ", ".join(stray[:5]))
        relative_package_paths(out_file, targ)
        return notes
    finally:
        shutil.rmtree(work, ignore_errors=True)


def filmcraft_media(project_file):
    """The paths of the media files a FilmCraft project refers to."""
    data = read_json(project_file)
    out = []
    for item in (data.get("project", {}).get("items") or {}).values():
        f = ((item.get("kind") or {}).get("Media") or {}).get("media", {}).get("File") or {}
        if f.get("path"):
            out.append(f["path"])
    return out


def relative_package_paths(project_file, targ):
    """Media inside the package (music, maps) as paths relative to the project file, which lies in the package
    folder: FilmCraft resolves them against the project's folder, so they are found wherever the package is
    unpacked. The originals keep their absolute paths on the editing computer."""
    pkg = paths.join(targ, "x", under_package=True)[:-1].replace("\\", "/")
    data = read_json(project_file)
    changed = False
    for item in (data.get("project", {}).get("items") or {}).values():
        f = ((item.get("kind") or {}).get("Media") or {}).get("media", {}).get("File") or {}
        path = (f.get("path") or "").replace("\\", "/")
        if path.startswith(pkg):
            f["path"] = path[len(pkg):]
            changed = True
    if changed:
        pathlib.Path(project_file).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _on_target(path, targ):
    root = paths.join(targ, "x")[:-1].replace("\\", "/")
    return path.replace("\\", "/").startswith(root)


def srt_text(subs):
    def ts(x):
        ms = round(x * 1000); h, ms = divmod(ms, 3600000); m_, ms = divmod(ms, 60000); s_, ms = divmod(ms, 1000)
        return f"{h:02d}:{m_:02d}:{s_:02d},{ms:03d}"
    return "\n".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(subs, 1))


def licenses_text(music):
    lines = ["Music in this film", ""]
    for m in music:
        lines.append(f'{m.get("cue", "")}  {m.get("title", "")} – {m.get("artist", "")}')
        lines.append(f'     licence: {m.get("license", "?")}' + (f'   source: {m["source"]}' if m.get("source") else ""))
        if m.get("file"):
            lines.append(f'     file: {pathlib.Path(m["file"]).name}')
        lines.append("")
    return "\n".join(lines)
