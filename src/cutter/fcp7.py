"""Final Cut Pro 7 XML (xmeml version 4) for Premiere Pro, from the model of export.build_model.

Written directly instead of through OTIO's adapter, because Premiere's relinking compares what the adapter leaves out
or gets wrong: the file's start timecode, its real frame rate (NTSC), its duration in its own frames, its name (never
URL-encoded). The file also carries Premiere-readable volume and opacity keyframes, links between picture and sound
of a shot, and sequence markers with comments.

Conventions (checked against Premiere's own exports, see docs/nle-roundtrip.md of Cutlery):
- clipitem start/end/in/out are frames of the sequence's rate; in/out count from the first frame of the media;
- keyframe <when> counts frames of the sequence's rate from the first frame of the media (like in/out);
- Audio Levels are linear gain (1 = 0 dB, at most 3.98109 = +12 dB).
"""
import xml.etree.ElementTree as ET
from xml.dom import minidom

from .export import timecode_frames

MAX_GAIN = 3.98109


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def _rate(parent, timebase, ntsc):
    r = _sub(parent, "rate")
    _sub(r, "timebase", timebase)
    _sub(r, "ntsc", "TRUE" if ntsc else "FALSE")
    return r


def _timecode(parent, tc, timebase, ntsc):
    drop = bool(tc) and ";" in tc and ntsc
    el = _sub(parent, "timecode")
    _rate(el, timebase, ntsc)
    _sub(el, "string", tc or "00:00:00:00")
    _sub(el, "frame", timecode_frames(tc, timebase, drop))
    _sub(el, "displayformat", "DF" if drop else "NDF")
    return el


def _gain(db):
    return max(0.0, min(MAX_GAIN, 10 ** (max(-96.0, db) / 20)))


def _effect(parent, name, effectid, category, mediatype, parameters):
    f = _sub(parent, "filter")
    e = _sub(f, "effect")
    _sub(e, "name", name)
    _sub(e, "effectid", effectid)
    _sub(e, "effectcategory", category)
    _sub(e, "effecttype", category)
    _sub(e, "mediatype", mediatype)
    for pid, pname, lo, hi, value, keys in parameters:
        p = _sub(e, "parameter", authoringApp="PremierePro")
        _sub(p, "parameterid", pid)
        _sub(p, "name", pname)
        _sub(p, "valuemin", lo)
        _sub(p, "valuemax", hi)
        _sub(p, "value", value)
        for when, v in keys:
            k = _sub(p, "keyframe")
            _sub(k, "when", when)
            _sub(k, "value", v)
    return f


class Writer:
    def __init__(self, model):
        self.m = model
        self.file_ids = {}
        self.n_items = 0

    def file(self, parent, f):
        if f["id"] in self.file_ids:
            _sub(parent, "file", id=self.file_ids[f["id"]])
            return
        fid = f"file-{len(self.file_ids) + 1}"
        self.file_ids[f["id"]] = fid
        el = _sub(parent, "file", id=fid)
        _sub(el, "name", f["name"])
        _sub(el, "pathurl", f["url"])
        _rate(el, f["timebase"], f["ntsc"])
        if f.get("duration") is not None:
            _sub(el, "duration", f["duration"])
        if f["kind"] == "footage":
            _timecode(el, f.get("timecode"), f["timebase"], f["ntsc"])
        media = _sub(el, "media")
        if f.get("has_video"):
            v = _sub(media, "video")
            sc = _sub(v, "samplecharacteristics")
            _rate(sc, f["timebase"], f["ntsc"])
            _sub(sc, "width", f.get("width") or self.m["width"])
            _sub(sc, "height", f.get("height") or self.m["height"])
            _sub(sc, "anamorphic", "FALSE")
            _sub(sc, "pixelaspectratio", "square")
            _sub(sc, "fielddominance", "none")
        if f.get("has_audio"):
            a = _sub(media, "audio")
            sc = _sub(a, "samplecharacteristics")
            _sub(sc, "depth", 16)
            _sub(sc, "samplerate", f.get("sample_rate") or 48000)
            _sub(a, "channelcount", f.get("channels") or 2)

    def clipitem(self, track_el, it, mediatype, trackindex, links):
        self.n_items += 1
        cid = f"clipitem-{self.n_items}"
        it["_id"] = cid
        f = self.m["files"][it["file"]]
        el = _sub(track_el, "clipitem", id=cid)
        _sub(el, "name", it["name"])
        _sub(el, "enabled", "TRUE")
        if f.get("duration") is not None:
            src_fps = f["timebase"] * (1000 / 1001 if f["ntsc"] else 1)
            _sub(el, "duration", round(f["duration"] / src_fps * float(self.m["fps"])))
        _rate(el, self.m["timebase"], self.m["ntsc"])
        _sub(el, "start", it["start"])
        _sub(el, "end", it["end"])
        _sub(el, "in", it["in"])
        _sub(el, "out", it["out"])
        self.file(el, f)
        st = _sub(el, "sourcetrack")
        _sub(st, "mediatype", mediatype)
        _sub(st, "trackindex", 1)
        if mediatype == "video":
            if it.get("scale") is not None and abs(it["scale"] - 100) > 1e-6:
                _effect(el, "Basic Motion", "basic", "motion", "video",
                        [("scale", "Scale", 0, 1000, f'{it["scale"]:.4g}', [])])
            if it.get("opacity"):
                keys = [(it["in"] + w, f"{v:g}") for w, v in it["opacity"]]
                _effect(el, "Opacity", "opacity", "motion", "video", [("opacity", "Opacity", 0, 100, "100", keys)])
        else:
            if it.get("volume"):
                keys = [(it["in"] + w, f"{_gain(db):.5f}") for w, db in it["volume"]]
                _effect(el, "Audio Levels", "audiolevels", "audiolevels", "audio",
                        [("level", "Level", 0, MAX_GAIN, keys[0][1], keys)])
            elif it.get("level_db") is not None:
                _effect(el, "Audio Levels", "audiolevels", "audiolevels", "audio",
                        [("level", "Level", 0, MAX_GAIN, f'{_gain(it["level_db"]):.5f}', [])])
        links.append((it, el, mediatype, trackindex))
        return el

    def write(self):
        m = self.m
        root = ET.Element("xmeml", version="4")
        seq = _sub(root, "sequence", id="sequence-1")
        _sub(seq, "name", m["name"])
        _sub(seq, "duration", m["duration"])
        _rate(seq, m["timebase"], m["ntsc"])
        _timecode(seq, None, m["timebase"], m["ntsc"])
        media = _sub(seq, "media")
        video = _sub(media, "video")
        fmt = _sub(video, "format")
        sc = _sub(fmt, "samplecharacteristics")
        _rate(sc, m["timebase"], m["ntsc"])
        _sub(sc, "width", m["width"])
        _sub(sc, "height", m["height"])
        _sub(sc, "anamorphic", "FALSE")
        _sub(sc, "pixelaspectratio", "square")
        _sub(sc, "fielddominance", "none")
        links = []
        for k, tr in enumerate(m["video"], 1):
            t = _sub(video, "track")
            for it in tr["items"]:
                self.clipitem(t, it, "video", k, links)
            _sub(t, "enabled", "TRUE")
            _sub(t, "locked", "FALSE")
        audio = _sub(media, "audio")
        _sub(audio, "numOutputChannels", 2)
        fmt = _sub(audio, "format")
        sc = _sub(fmt, "samplecharacteristics")
        _sub(sc, "depth", 16)
        _sub(sc, "samplerate", 48000)
        for k, tr in enumerate(m["audio"], 1):
            t = _sub(audio, "track")
            for it in tr["items"]:
                self.clipitem(t, it, "audio", k, links)
            _sub(t, "enabled", "TRUE")
            _sub(t, "locked", "FALSE")
        self.link(links)
        for mk in m["markers"]:
            el = _sub(seq, "marker")
            _sub(el, "name", mk["name"])
            _sub(el, "comment", mk.get("comment", ""))
            _sub(el, "in", mk["start"])
            _sub(el, "out", mk["start"] + mk["duration"] if mk.get("duration") else -1)
        xml = minidom.parseString(ET.tostring(root, encoding="UTF-8")).toprettyxml(indent="  ", encoding="UTF-8")
        return xml.replace(b'<?xml version="1.0" encoding="UTF-8"?>',
                           b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>', 1)

    @staticmethod
    def link(links):
        """Picture and sound of the same shot (V1 and A1) move together in the editor."""
        by_shot = {}
        for it, el, mediatype, trackindex in links:
            nr = (it.get("meta") or {}).get("nr")
            if nr is not None and trackindex == 1 and "cutaway_of" not in (it.get("meta") or {}):
                by_shot.setdefault(nr, []).append((it, el, mediatype, trackindex))
        index = {}
        for it, el, mediatype, trackindex in links:
            index.setdefault((mediatype, trackindex), []).append(it["_id"])
        for group in by_shot.values():
            if len(group) < 2:
                continue
            for _, el, _, _ in group:
                for it, _, mediatype, trackindex in group:
                    ln = _sub(el, "link")
                    _sub(ln, "linkclipref", it["_id"])
                    _sub(ln, "mediatype", mediatype)
                    _sub(ln, "trackindex", trackindex)
                    _sub(ln, "clipindex", index[(mediatype, trackindex)].index(it["_id"]) + 1)


def write(model):
    return Writer(model).write()
