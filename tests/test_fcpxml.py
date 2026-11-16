"""FCPXML 1.10 for Final Cut Pro and DaVinci Resolve (fcpxml.py) from the export model.

The DTD is Apple's and not part of this repository. To validate against it, save the DTD of FCPXML 1.10 (from
Apple's "Document Type Definition" page of the FCPXML reference) and point CUTTER_FCPXML_DTD at it; lxml is needed.
"""
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from fractions import Fraction

from helpers import TOOLS
import golden_demo

MAC = ["--target-os", "macos", "--target-root", "/Volumes/Footage/Demo"]
DTD = os.environ.get("CUTTER_FCPXML_DTD")


def sec(value):
    return Fraction(value[:-1])


class Times(unittest.TestCase):
    def test_rational_seconds(self):
        from cutter.fcpxml import frame_duration, t
        self.assertEqual(t(Fraction(5)), "5s")
        self.assertEqual(t(frame_duration(30, True)), "1001/30000s")
        self.assertEqual(t(3 * frame_duration(25, False)), "3/25s")

    def test_file_urls_as_final_cut_writes_them(self):
        from cutter.fcpxml import file_url
        self.assertEqual(file_url("file://localhost/Volumes/A%20B/x.mov"), "file:///Volumes/A%20B/x.mov")
        self.assertEqual(file_url("file://localhost/D:/V/x.mov"), "file:///D:/V/x.mov")
        self.assertEqual(file_url("file://server/share/x.mov"), "file://server/share/x.mov")


@unittest.skipUnless(TOOLS, "needs the tool packages (Docker image)")
class FinalCut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="cutter_fcpxml_")
        film = golden_demo.setup_project(cls.tmp.name)
        clips = json.loads((film / "analysis/clips.json").read_text())
        for c in clips:
            if c["id"] == "C001":
                c.update(timecode="10:00:00:00", width=3840, height=2160, rel_path="Tag 1/Straße am Fluss.MOV")
            if c["id"] == "C004":
                c.update(fps=29.97, fps_raw="30000/1001", timecode="01:02:03;04")
        (film / "analysis/clips.json").write_text(json.dumps(clips))
        edit = json.loads((film / "cuts/cut1/edit.json").read_text())
        edit["sections"][0]["shots"][1]["stabilize"] = True          # shot 2
        (film / "cuts/cut1/edit.json").write_text(json.dumps(edit))
        golden_demo.run_pipeline(film)
        cls.film = film
        cls.result = golden_demo.cli("export", "cut1", "--project", film, "--to", "fcp", *MAC, "--out", film / "pkg")
        cls.path = film / "pkg" / cls.result["timeline"]
        cls.xml = ET.parse(cls.path).getroot()
        cls.assets = {a.get("id"): a for a in cls.xml.iter("asset")}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def asset(self, name):
        return next(a for a in self.assets.values() if a.get("name") == name)

    def test_version_and_package(self):
        self.assertEqual(self.xml.get("version"), "1.10")
        self.assertTrue(self.result["timeline"].endswith(".fcpxml"))
        self.assertIn("titles/title_01.png", self.result["files"])

    @unittest.skipUnless(DTD, "set CUTTER_FCPXML_DTD to Apple's DTD of FCPXML 1.10")
    def test_valid_against_the_dtd(self):
        from lxml import etree
        dtd = etree.DTD(DTD)
        doc = etree.parse(str(self.path))
        self.assertTrue(dtd.validate(doc), dtd.error_log.filter_from_errors())

    def test_media_points_to_the_originals(self):
        a = self.asset("Straße am Fluss.MOV")
        self.assertEqual(a.find("media-rep").get("src"), "file:///Volumes/Footage/Demo/Tag%201/Stra%C3%9Fe%20am%20Fluss.MOV")
        for x in self.xml.iter("media-rep"):
            self.assertTrue(x.get("src").startswith("file:///Volumes/Footage/Demo/"), x.get("src"))

    def test_original_timecode_rate_and_size(self):
        a = self.asset("Straße am Fluss.MOV")
        self.assertEqual(a.get("start"), "36000s")
        fmt = next(f for f in self.xml.iter("format") if f.get("id") == a.get("format"))
        self.assertEqual((fmt.get("width"), fmt.get("height")), ("3840", "2160"))
        ntsc = next(f for f in self.xml.iter("format") if f.get("frameDuration") == "1001/30000s")
        b = next(x for x in self.assets.values() if x.get("format") == ntsc.get("id"))
        frames = Fraction(b.get("start")[:-1]) / Fraction(1001, 30000)
        self.assertEqual(frames, 111582)                  # 01:02:03;04 drop-frame
        clip = next(c for c in self.xml.iter("asset-clip") if c.get("ref") == b.get("id"))
        self.assertEqual(clip.get("tcFormat"), "DF")
        self.assertGreaterEqual(sec(clip.get("start")), sec(b.get("start")))

    def test_spine_covers_the_sequence(self):
        seq = next(self.xml.iter("sequence"))
        spine = seq.find("spine")
        pos = Fraction(0)
        for el in spine:
            self.assertEqual(sec(el.get("offset")), pos, el.get("name"))
            pos += sec(el.get("duration"))
        self.assertEqual(pos, sec(seq.get("duration")))
        self.assertAlmostEqual(float(pos), self.result["duration"], places=2)

    def test_footage_fills_the_frame(self):
        for c in self.xml.find(".//spine").iter("asset-clip"):
            if c.get("srcEnable") != "audio" and c.get("lane") is None:
                self.assertEqual(c.find("adjust-conform").get("type"), "fill")

    def test_music_ducks_under_dialogue(self):
        music = [c for c in self.xml.iter("asset-clip") if c.get("audioRole") == "music"]
        self.assertTrue(music)
        keys = music[0].findall("adjust-volume/param/keyframeAnimation/keyframe")
        self.assertGreater(len(keys), 2)
        values = {float(k.get("value")[:-2]) for k in keys}
        self.assertGreater(max(values) - min(values), 3)
        start, end = sec(music[0].get("start")), sec(music[0].get("start")) + sec(music[0].get("duration"))
        self.assertTrue(all(start <= sec(k.get("time")) <= end for k in keys))

    def test_titles_fade_in_and_out(self):
        stills = [v for v in self.xml.iter("video") if v.get("name", "").startswith("title_")]
        self.assertTrue(stills)
        amounts = [float(k.get("value")) for k in stills[0].findall("adjust-blend/param/keyframeAnimation/keyframe")]
        self.assertIn(1.0, amounts)
        self.assertTrue(all(v.get("lane") for v in stills))

    def test_stabilisation_is_applied(self):
        clip = next(c for c in self.xml.iter("asset-clip") if c.get("name", "").startswith("02 "))
        self.assertEqual(clip.find("adjust-stabilization").get("type"), "automatic")

    def test_cutaways_are_picture_only(self):
        for c in self.xml.iter("asset-clip"):
            if c.get("lane") and int(c.get("lane")) > 0:
                self.assertEqual(c.get("srcEnable"), "video")
                self.assertIsNone(c.find("adjust-volume"))

    def test_noise_reduction_and_markers(self):
        nr = list(self.xml.iter("adjust-noiseReduction"))
        self.assertTrue(nr)
        names = [m.get("value") for m in self.xml.iter("marker")]
        self.assertTrue(any(n.startswith("Audio cleanup") for n in names))
        self.assertTrue(any(n.startswith("Decision") for n in names))
        self.assertFalse(any(n.startswith("Stabilise") for n in names))     # applied, not a note

    def test_captions_for_every_subtitle(self):
        caps = list(self.xml.iter("caption"))
        srt = (self.film / "pkg" / self.result["timeline"].replace(".fcpxml", ".srt")).read_text(encoding="utf-8")
        self.assertEqual(len(caps), srt.count(" --> "))
        self.assertTrue(all(c.get("role").startswith("iTT?captionFormat=ITT.") for c in caps))

    def test_resolve_gets_the_same_timeline_with_stabilisation_markers(self):
        r = golden_demo.cli("export", "cut1", "--project", self.film, "--to", "resolve", *MAC, "--out", self.film / "res")
        res = ET.parse(self.film / "res" / r["timeline"]).getroot()
        names = [m.get("value") for m in res.iter("marker")]
        self.assertIn("Stabilise shot 2", names)
        self.assertEqual(len(names), len(list(self.xml.iter("marker"))) + 1)
        self.assertEqual(len(list(res.iter("asset-clip"))), len(list(self.xml.iter("asset-clip"))))
