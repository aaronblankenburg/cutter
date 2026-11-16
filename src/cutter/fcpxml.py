"""FCPXML 1.10 for Final Cut Pro (10.6 and later) and DaVinci Resolve, from the model of export.build_model.

Written against Apple's DTD for FCPXML 1.10 (tests/test_fcpxml.py validates with it when the DTD is available).

How the model maps onto FCPXML:
- the first video track is the primary storyline (spine); holes in it become gaps;
- every other item is a connected clip on a lane of the spine item it starts in (video tracks 2… on lanes 1…,
  audio that does not belong to a V1 clip on lanes -1…, captions above the video);
- a shot's picture and sound (V1 and A1 with the same file and frames) are one asset-clip, as in Final Cut;
- picture size: the footage fills the frame (adjust-conform fill, what Basic Motion scale does for Premiere);
- dialogue level and music ducking: adjust-volume (static or keyframes, in dB);
- titles, credits, end text: the package's transparent PNGs with opacity keyframes (adjust-blend);
- stabilisation (adjust-stabilization) and noise reduction (adjust-noiseReduction) are applied, the rest of the
  audio cleanup and the review decisions stay markers (for Resolve the stabilisation markers too);
- subtitles become captions (ITT role in the project's language); the package has the SRT as well.

Times are rational seconds. The `start` of a footage clip counts from the asset's start, which is the original's
start timecode, so the editor shows the camera's timecode and relinks by it.
"""
import xml.etree.ElementTree as ET
from fractions import Fraction
from xml.dom import minidom

from .export import timecode_frames

VERSION = "1.10"


def t(x):
    """Rational seconds as FCPXML writes them: "5s", "1001/30000s"."""
    x = Fraction(x)
    return f"{x.numerator}s" if x.denominator == 1 else f"{x.numerator}/{x.denominator}s"


def frame_duration(timebase, ntsc):
    return Fraction(1001, timebase * 1000) if ntsc else Fraction(1, timebase)


def file_url(url):
    """file://localhost/… as Final Cut writes it: file:///…"""
    return "file:///" + url[len("file://localhost/"):] if url.startswith("file://localhost/") else url


def _db(db):
    return f"{max(-96.0, min(12.0, db)):.2f}dB"


def _sub(parent, tag, **attrs):
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items() if v is not None})


def _keyframes(parent, name, keys):
    p = _sub(parent, "param", name=name)
    ka = _sub(p, "keyframeAnimation")
    for time, value in keys:
        _sub(ka, "keyframe", time=t(time), value=value)
    return p


class Writer:
    def __init__(self, model, language="en", applied=("stabilize",)):
        self.m = model
        self.applied = set(applied)
        self.language = language or "en"
        self.fd = Fraction(1) / Fraction(model["fps"])
        self.ids = 0
        self.formats = {}
        self.assets = {}
        self.styles = 0

    def next_id(self):
        self.ids += 1
        return f"r{self.ids}"

    # ------------------------------------------------------------ resources

    def format(self, width, height, fd=None):
        key = (width, height, fd)
        if key not in self.formats:
            el = _sub(self.resources, "format", id=self.next_id(), frameDuration=t(fd) if fd else None,
                      width=width, height=height, name=None if fd else "FFVideoFormatRateUndefined")
            self.formats[key] = el.get("id")
        return self.formats[key]

    def asset(self, f):
        """{"id", "start", "fd", "still", "tcFormat"} of a file of the model; the asset is written once."""
        if f["id"] in self.assets:
            return self.assets[f["id"]]
        still = f["kind"] == "still"
        fd = frame_duration(f["timebase"], f["ntsc"])
        tc = f.get("timecode") if f["kind"] == "footage" else None
        drop = bool(tc) and ";" in tc and f["ntsc"]
        start = timecode_frames(tc, f["timebase"], drop) * fd if tc else Fraction(0)
        attrs = {"id": self.next_id(), "name": f["name"], "start": t(start)}
        if still:
            attrs["duration"] = "0s"
        elif f.get("duration") is not None:
            attrs["duration"] = t(f["duration"] * fd)
        if f.get("has_video"):
            attrs.update(hasVideo=1, videoSources=1,
                         format=self.format(f.get("width") or self.m["width"], f.get("height") or self.m["height"],
                                            None if still else fd))
        if f.get("has_audio"):
            attrs.update(hasAudio=1, audioSources=1, audioChannels=f.get("channels") or 2,
                         audioRate=f.get("sample_rate") or 48000)
        el = _sub(self.resources, "asset", **attrs)
        _sub(el, "media-rep", kind="original-media", src=file_url(f["url"]))
        a = {"id": attrs["id"], "start": start, "still": still, "tcFormat": ("DF" if drop else "NDF") if tc else None}
        self.assets[f["id"]] = a
        return a

    # ------------------------------------------------------------ story elements

    def clip(self, parent, it, offset, lane=None, audio=None, only=None, role=None):
        """An asset-clip (footage, music) or a video element (still) for an item of the model.
        audio: the A1 item that belongs to this picture (its level goes onto this clip)."""
        f = self.m["files"][it["file"]]
        a = self.asset(f)
        dur = (it["end"] - it["start"]) * self.fd
        start = a["start"] + it["in"] * self.fd
        if a["still"]:
            el = _sub(parent, "video", ref=a["id"], lane=lane, offset=t(offset), name=it["name"], start=t(start),
                      duration=t(dur))
        else:
            el = _sub(parent, "asset-clip", ref=a["id"], lane=lane, offset=t(offset), name=it["name"], start=t(start),
                      duration=t(dur), srcEnable=only if f.get("has_audio") and f.get("has_video") else None,
                      tcFormat=a["tcFormat"], audioRole=None if only == "video" or not f.get("has_audio") else
                      role or ("music" if f["kind"] == "audio" else "dialogue"))
        el.set("_start", str(start))                     # the clip's local time at its first frame (removed later)
        el.set("_offset", str(offset))
        if f["kind"] == "footage" and f.get("has_video") and only != "audio":
            _sub(el, "adjust-conform", type="fill")
        if it.get("opacity"):
            b = _sub(el, "adjust-blend")
            _keyframes(b, "amount", [(start + w * self.fd, f"{v / 100:g}") for w, v in it["opacity"]])
        if it.get("stabilize") and only != "audio":
            _sub(el, "adjust-stabilization", type="automatic")
        sound = audio if audio is not None else it
        if not a["still"] and f.get("has_audio") and only != "video":
            if sound.get("volume"):
                v = _sub(el, "adjust-volume")
                _keyframes(v, "amount", [(start + w * self.fd, _db(db)) for w, db in sound["volume"]])
            elif sound.get("level_db") is not None:
                _sub(el, "adjust-volume", amount=_db(sound["level_db"]))
        return el

    def finish(self, el, f, it):
        """What has to come after anchored items and markers (DTD order): channel-level audio enhancements."""
        if el.tag == "asset-clip" and it.get("noise_reduction") and f.get("has_audio") and el.get("srcEnable") != "video":
            n = f.get("channels") or 2
            src = _sub(el, "audio-channel-source", srcCh=", ".join(str(i) for i in range(1, n + 1)), role="dialogue")
            _sub(src, "adjust-noiseReduction", amount=f'{float(it["noise_reduction"]):g}')

    def write(self):
        m = self.m
        root = ET.Element("fcpxml", version=VERSION)
        self.resources = _sub(root, "resources")
        seq_format = self.format(m["width"], m["height"], self.fd)
        library = _sub(root, "library")
        event = _sub(library, "event", name=m["name"])
        project = _sub(event, "project", name=m["name"])
        total = m["duration"] * self.fd
        seq = _sub(project, "sequence", format=seq_format, duration=t(total), tcStart="0s", tcFormat="NDF",
                   audioLayout="stereo", audioRate="48k")
        spine = _sub(seq, "spine")

        video = m["video"]
        audio = m["audio"]
        v1 = sorted(video[0]["items"], key=lambda i: i["start"]) if video else []
        a1 = audio[0]["items"] if audio else []
        pair = {}                                         # V1 item index -> the A1 item with the same file and frames
        used = set()
        for k, it in enumerate(v1):
            for j, au in enumerate(a1):
                if j not in used and (au["file"], au["start"], au["end"], au["in"]) == (it["file"], it["start"], it["end"], it["in"]):
                    pair[k] = au
                    used.add(j)
                    break

        # the primary storyline: V1 with gaps
        main, pos = [], 0
        for k, it in enumerate(v1):
            if it["start"] > pos:
                main.append(self.gap(spine, pos, it["start"]))
            if it["start"] < pos:                         # overlap on V1 should not happen; keep the later clip whole
                continue
            only = None if k in pair else "video"
            main.append((self.clip(spine, it, it["start"] * self.fd, audio=pair.get(k), only=only), it))
            pos = it["end"]
        if pos < m["duration"] or not main:
            main.append(self.gap(spine, pos, max(m["duration"], pos + 1)))

        def parent_of(frame):
            for el, _ in main:
                o, d = Fraction(el.get("_offset")), _dur(el)
                if o <= frame * self.fd < o + d:
                    return el
            return main[-1][0] if frame * self.fd >= Fraction(main[-1][0].get("_offset")) else main[0][0]

        def local(el, frame):
            return Fraction(el.get("_start")) + frame * self.fd - Fraction(el.get("_offset"))

        anchored = {id(el): [] for el, _ in main}
        for lane, tr in enumerate(video[1:], 1):          # pictures only: their sound, if any, is on an audio track
            for it in tr["items"]:
                anchored[id(parent_of(it["start"]))].append(("clip", it, lane, "video"))
        for j, au in enumerate(a1):
            if j not in used:
                anchored[id(parent_of(au["start"]))].append(("clip", au, -1, "audio"))
        for n, tr in enumerate(audio[1:], 2):
            for it in tr["items"]:
                anchored[id(parent_of(it["start"]))].append(("clip", it, -n, None))
        caption_lane = max(1, len(video))
        for a, b, text in m.get("subtitles") or []:
            fa, fb = round(Fraction(a) / self.fd), round(Fraction(b) / self.fd)
            anchored[id(parent_of(fa))].append(("caption", (fa, fb, text), caption_lane, None))

        markers = {id(el): [] for el, _ in main}         # markers of what is applied would only repeat it
        for mk in m["markers"]:
            if mk.get("kind") in self.applied:
                continue
            markers[id(parent_of(mk["start"]))].append(mk)

        for el, it in main:
            for kind, x, lane, only in sorted(anchored[id(el)], key=lambda e: _first(e)):
                if kind == "clip":
                    c = self.clip(el, x, local(el, x["start"]), lane=lane, only=only)
                    self.finish(c, m["files"][x["file"]], x)
                else:
                    self.caption(el, local(el, x[0]), (x[1] - x[0]) * self.fd, x[2], lane)
            for mk in markers[id(el)]:
                _sub(el, "marker", start=t(local(el, mk["start"])), duration=t(max(1, mk.get("duration") or 0) * self.fd),
                     value=mk["name"], note=mk.get("comment") or None)
            if it is not None:
                self.finish(el, m["files"][it["file"]], it)

        for el in root.iter():
            for k in ("_start", "_offset"):
                el.attrib.pop(k, None)
        xml = minidom.parseString(ET.tostring(root, encoding="UTF-8")).toprettyxml(indent="  ", encoding="UTF-8")
        return xml.replace(b'<?xml version="1.0" encoding="UTF-8"?>',
                           b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>', 1)

    def gap(self, spine, a, b):
        start = Fraction(3600)                            # Final Cut's convention for the local time of gaps
        el = _sub(spine, "gap", name="Gap", offset=t(a * self.fd), start=t(start), duration=t((b - a) * self.fd))
        el.set("_start", str(start))
        el.set("_offset", str(a * self.fd))
        return el, None

    def caption(self, parent, offset, duration, text, lane):
        self.styles += 1
        sid = f"ts{self.styles}"
        el = _sub(parent, "caption", lane=lane, offset=t(offset), name=text.replace("\n", " ")[:60], start=t(offset),
                  duration=t(duration), role=f"iTT?captionFormat=ITT.{self.language}")
        tx = _sub(el, "text", placement="bottom")
        st = _sub(tx, "text-style", ref=sid)
        st.text = text
        d = _sub(el, "text-style-def", id=sid)
        _sub(d, "text-style", font=".AppleSystemUIFont", fontSize="13", fontFace="Regular", fontColor="1 1 1 1",
             backgroundColor="0 0 0 1")
        return el


def _dur(el):
    s = el.get("duration")[:-1]
    return Fraction(s)


def _first(entry):
    kind, x = entry[0], entry[1]
    return x["start"] if kind == "clip" else x[0]


def write(model, language="en", applied=("stabilize",)):
    """applied: marker kinds left out because the receiving application applies them (Final Cut: stabilisation;
    Resolve gets them all, it may not take adjust-stabilization from FCPXML)."""
    return Writer(model, language, applied).write()
