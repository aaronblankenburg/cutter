"""Public golden tests: the synthetic demo through retime, timeline, review data and the FilmCraft batch."""
import json
import pathlib
import unittest

from helpers import FFMPEG, GOLDEN, TOOLS, TempDir
import golden_demo

EXPECTED = GOLDEN / "demo" / "expected"


@unittest.skipUnless(TOOLS and FFMPEG, "needs numpy, OpenTimelineIO and ffmpeg")
class GoldenDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TempDir()
        cls.film = golden_demo.setup_project(cls.tmp.__enter__())
        cls.results = golden_demo.run_pipeline(cls.film)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def test_outputs_match_the_expected_files(self):
        for name in golden_demo.OUTPUTS:
            with self.subTest(file=name):
                expected = (EXPECTED / name).read_text(encoding="utf-8")
                self.assertEqual(golden_demo.normalized(self.film, name), expected,
                                 f"{name} differs; after an intended change: python tests/golden_demo.py --update")

    def test_retime_puts_section_ends_on_downbeats(self):
        r = self.results["retime"]
        self.assertEqual(r["without_beat"], 0)
        log = json.loads((self.film / "cuts/cut1/work/retime_log.json").read_text())
        required = [L for L in log if L.get("required")]
        self.assertTrue(required, "the demo has required downbeats")
        self.assertTrue(all(L["downbeat"] for L in required))
        # 120 BPM, the music starts at the film start with its first beat at 0.5 s: cuts land on multiples of 0.5 s
        for L in log:
            self.assertAlmostEqual(L["cut"] * 2, round(L["cut"] * 2), delta=0.1, msg=L)

    def test_timeline_has_subtitles_music_and_cutaway(self):
        srt = (self.film / "cuts/cut1/cut1.srt").read_text()
        self.assertIn("Subtitles are burned in on export.", srt)
        otio = json.loads((self.film / "cuts/cut1/cut1.otio").read_text())
        names = [t["name"] for t in otio["tracks"]["children"]]
        self.assertIn("V4 Cutaways", names)
        self.assertIn("A2 Music", names)

    def test_review_timeline(self):
        tl = json.loads((self.film / ".cutter/review/data/cuts/cut1/timeline.json").read_text())
        self.assertEqual(tl["format"], 2)
        self.assertEqual(len(tl["shots"]), 8)
        self.assertEqual(len(tl["subtitles"]), 2)
        self.assertEqual([e["id"] for e in tl["decisions"]["entries"]], ["overview", "talk"])
        self.assertAlmostEqual(tl["duration"], 29.0, places=2)

    def test_filmcraft_batch_has_titles_credits_and_subtitles(self):
        lines = [json.loads(x) for x in (self.film / "cuts/cut1/work/filmcraft_batch.jsonl").read_text().splitlines() if x.strip()]
        ids = [x["id"] for x in lines]
        self.assertIn("captions.import", ids)
        self.assertEqual(ids[-1], "file.save")
        self.assertIn("captions.setTrack", ids, "the spoken language (cutter.toml → language) names the subtitle track")
        self.assertEqual(self.results["filmcraft"]["mode"], "dry-run")

    def test_media_paths_follow_the_media_root(self):
        """Portability: the same project with its footage elsewhere – only cutter.toml changes."""
        with TempDir() as tmp:
            film = golden_demo.setup_project(tmp, media_root="/moved/elsewhere")
            (film / "cuts/cut1/edit.json").write_text((EXPECTED / "edit.json").read_text())
            golden_demo.cli("timeline", "cut1", "--build-only", "--project", film)
            otio = (film / "cuts/cut1/cut1.otio").read_text()
            xml = (film / "cuts/cut1/cut1.fcp7.xml").read_text()
        self.assertIn("file:///moved/elsewhere/camera/", otio)
        self.assertIn("file:///moved/elsewhere/camera/", xml)
        self.assertNotIn("/demo/footage", otio + xml)


class GoldenFiles(unittest.TestCase):
    def test_expected_files_are_small_and_free_of_local_paths(self):
        for p in sorted(EXPECTED.iterdir()) + sorted((GOLDEN / "demo" / "input").iterdir()):
            with self.subTest(file=p.name):
                self.assertLess(p.stat().st_size, 200_000)
                text = p.read_text(encoding="utf-8")
                self.assertNotIn("/home/", text)
                self.assertNotIn("/tmp/", text)


if __name__ == "__main__":
    unittest.main()
