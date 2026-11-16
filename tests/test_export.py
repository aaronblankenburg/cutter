"""Packages for other editors (export.py, fcp7.py, titles.py): media references, original metadata, keyframes."""
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET

from helpers import TOOLS
import golden_demo

WIN = ["--target-os", "windows", "--target-root", "D:\\Videos\\Demo"]


class Rates(unittest.TestCase):
    def test_rates(self):
        from cutter.export import rate
        self.assertEqual(rate("30000/1001")[1:], (30, True))
        self.assertEqual(rate(29.97)[1:], (30, True))
        self.assertEqual(rate("24000/1001")[1:], (24, True))
        self.assertEqual(rate("60000/1001")[1:], (60, True))
        self.assertEqual(rate(25)[1:], (25, False))
        self.assertEqual(rate("50/1")[1:], (50, False))

    def test_timecode_frames(self):
        from cutter.export import timecode_frames
        self.assertEqual(timecode_frames("10:00:00:00", 25, False), 900000)
        self.assertEqual(timecode_frames("00:01:00;02", 30, True), 1800)          # 00:01:00;00/;01 do not exist
        self.assertEqual(timecode_frames("01:00:00;00", 30, True), 107892)
        self.assertEqual(timecode_frames("00:10:00;00", 30, True), 17982)
        self.assertEqual(timecode_frames(None, 25, False), 0)


@unittest.skipUnless(TOOLS, "needs the tool packages (Docker image)")
class Premiere(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="cutter_export_")
        film = golden_demo.setup_project(cls.tmp.name)
        # what a stand-in project knows about the originals: start timecode, NTSC, 4K, a name with spaces
        clips = json.loads((film / "analysis/clips.json").read_text())
        for c in clips:
            if c["id"] == "C001":
                c.update(timecode="10:00:00:00", width=3840, height=2160, rel_path="Tag 1/Straße am Fluss.MOV")
            if c["id"] == "C004":
                c.update(fps=29.97, fps_raw="30000/1001", timecode="01:02:03;04")
        (film / "analysis/clips.json").write_text(json.dumps(clips))
        golden_demo.run_pipeline(film)
        cls.film = film
        cls.result = golden_demo.cli("export", "cut1", "--project", film, *WIN, "--out", film / "pkg")
        cls.xml = ET.parse(film / "pkg" / cls.result["timeline"]).getroot()
        cls.files = {f.get("id"): f for f in cls.xml.iter("file") if f.find("pathurl") is not None}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def file(self, name):
        return next(f for f in self.files.values() if f.findtext("name") == name)

    def test_package_contents(self):
        files = set(self.result["files"])
        self.assertTrue({"Demo_cut1.xml", "Demo_cut1.srt", "music/click_120bpm.wav", "music/LICENSES.txt",
                         "titles/title_01.png", "titles/title_02.png"} <= files, files)
        self.assertIn("CC BY 4.0", (self.film / "pkg/music/LICENSES.txt").read_text())

    def test_media_references_point_to_the_originals_on_the_editing_computer(self):
        f = self.file("Straße am Fluss.MOV")
        self.assertEqual(f.findtext("pathurl"), "file://localhost/D:/Videos/Demo/Tag%201/Stra%C3%9Fe%20am%20Fluss.MOV")
        music = self.file("click_120bpm.wav")
        self.assertEqual(music.findtext("pathurl"), "file://localhost/D:/Videos/Demo/_cutlery/music/click_120bpm.wav")
        for f in self.files.values():
            self.assertNotIn("%", f.findtext("name"))
            self.assertNotIn("/demo", f.findtext("pathurl"))

    def test_original_timecode_rate_size_and_duration(self):
        f = self.file("Straße am Fluss.MOV")
        self.assertEqual(f.findtext("timecode/string"), "10:00:00:00")
        self.assertEqual(f.findtext("timecode/frame"), "900000")
        self.assertEqual(f.findtext("media/video/samplecharacteristics/width"), "3840")
        self.assertEqual(f.findtext("duration"), "400")                                # 16 s at 25 fps
        ntsc = self.file("2024-05-01 09-15-00 clip04.mp4")
        self.assertEqual((ntsc.findtext("rate/timebase"), ntsc.findtext("rate/ntsc")), ("30", "TRUE"))
        self.assertEqual(ntsc.findtext("timecode/displayformat"), "DF")
        self.assertEqual(ntsc.findtext("timecode/frame"), str(((3600 + 2 * 60 + 3) * 30 + 4) - 2 * (62 - 6)))

    def test_4k_is_scaled_to_half(self):
        item = next(c for c in self.xml.iter("clipitem") if "C001" in c.findtext("name")
                    and c.findtext("sourcetrack/mediatype") == "video")
        self.assertEqual(item.findtext("filter/effect/parameter/value"), "50")

    def test_every_file_is_defined_once(self):
        defined = [f.get("id") for f in self.xml.iter("file") if f.find("pathurl") is not None]
        self.assertEqual(len(defined), len(set(defined)))
        refs = {f.get("id") for f in self.xml.iter("file")}
        self.assertEqual(refs, set(defined))

    def test_music_ducks_under_dialogue(self):
        music = [c for c in self.xml.iter("clipitem") if c.findtext("file/name") == "click_120bpm.wav"
                 or self.files.get(c.find("file").get("id"), ET.Element("x")).findtext("name") == "click_120bpm.wav"]
        keys = [(int(k.findtext("when")), float(k.findtext("value"))) for c in music for k in c.iter("keyframe")]
        self.assertGreater(len(keys), 4)
        values = [v for _, v in keys]
        self.assertLess(min(values), 0.2)                      # lowered under the talk
        self.assertGreater(max(values), 1.0)                   # base level

    def test_titles_with_opacity_and_markers(self):
        titles = [c for c in self.xml.iter("clipitem") if c.findtext("name").startswith("title_")]
        self.assertEqual(len(titles), 2)
        self.assertTrue(all(c.find(".//keyframe") is not None for c in titles))
        names = [m.findtext("name") for m in self.xml.iter("marker")]
        self.assertIn("Talk starts", names)
        self.assertTrue(any(n.startswith("Decision: ") for n in names))
        self.assertTrue(any(n.startswith("Audio cleanup") for n in names))

    def test_picture_and_sound_of_a_shot_are_linked(self):
        linked = [c for c in self.xml.iter("clipitem") if c.find("link") is not None]
        self.assertTrue(linked)
        ids = {c.get("id") for c in self.xml.iter("clipitem")}
        for c in linked:
            self.assertTrue({l.findtext("linkclipref") for l in c.iter("link")} <= ids)

    def test_relink_renamed_originals(self):
        relink = self.film / "relink.json"
        relink.write_text(json.dumps({"Tag 1/Straße am Fluss.MOV": "Tag 1/neu/Fluss.MOV"}))
        r = golden_demo.cli("export", "cut1", "--project", self.film, *WIN, "--out", self.film / "pkg3", "--relink", relink)
        xml = (self.film / "pkg3" / r["timeline"]).read_text()
        self.assertIn("file://localhost/D:/Videos/Demo/Tag%201/neu/Fluss.MOV", xml)
        self.assertNotIn("Stra%C3%9Fe", xml)

    def test_bad_target_is_a_usage_error(self):
        from cutter.util import CutterError
        with self.assertRaises((CutterError, SystemExit)):
            golden_demo.cli("export", "cut1", "--project", self.film, "--target-os", "windows", "--target-root", "/x",
                            "--out", self.film / "pkg2")


if __name__ == "__main__":
    unittest.main()
