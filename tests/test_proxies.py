"""Proxies: made for the clips that need one, with the same frames; a draft timeline plays them, the final one the
footage; the rough render of the review data follows the timeline."""
import json
import os
import pathlib
import subprocess
import unittest

from helpers import FFMPEG, TOOLS, TempDir
import golden_demo


def make_clip(path, size, rate="25", seconds=2, codec=("-c:v", "libx264", "-preset", "ultrafast")):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(seconds), "-pix_fmt", "yuv420p",
                    *codec, "-c:a", "aac", "-b:a", "64k", "-shortest", str(path)], check=True)


def frames(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                        "stream=nb_read_frames,width,height,r_frame_rate", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    s = json.loads(r.stdout)["streams"][0]
    return int(s["nb_read_frames"]), s["width"], s["height"], s["r_frame_rate"]


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class Proxies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cutter import analyze
        cls.tmp = TempDir()
        cls.d = d = cls.tmp.__enter__()
        (d / "footage").mkdir()
        make_clip(d / "footage" / "big.mp4", "640x480")
        make_clip(d / "footage" / "ntsc.mp4", "640x360", rate="30000/1001")
        make_clip(d / "footage" / "small.mp4", "320x240")
        make_clip(d / "footage" / "mpeg4.mp4", "320x240", codec=("-c:v", "mpeg4"))
        cls.clips = []
        for k, name in enumerate(("big", "ntsc", "small", "mpeg4"), 1):
            cls.clips.append(dict(analyze.probe_clip(d / "footage" / f"{name}.mp4", d / "footage"), id=f"C00{k}"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def test_proxies_have_the_same_frames_and_only_clips_that_need_one_get_one(self):
        from cutter import proxies
        from cutter.util import CutterError
        folder = self.d / "proxies"
        dry = proxies.make(self.clips, folder, height=240, dry_run=True)
        self.assertEqual((dry["to_make"], dry["as_is"]), (["C001", "C002", "C004"], 1))
        self.assertFalse(folder.exists())
        with self.assertRaises(CutterError):
            proxies.make(self.clips, folder, height=20)
        with self.assertRaises(CutterError):
            proxies.table(self.clips, folder)                    # none made yet
        r = proxies.make(self.clips, folder, height=240)
        self.assertEqual((r["made"], r["kept"], r["as_is"], r["failed"]), (3, 0, 1, []))
        for cid, name, size in (("C001", "big", (320, 240)), ("C002", "ntsc", (426, 240)), ("C004", "mpeg4", (320, 240))):
            with self.subTest(clip=name):
                source, proxy = frames(self.d / "footage" / f"{name}.mp4"), frames(folder / f"{cid}.mp4")
                self.assertEqual((proxy[0], proxy[3]), (source[0], source[3]))       # frame for frame, same rate
                self.assertEqual(proxy[1:3], size)
        self.assertFalse((folder / "C003.mp4").exists())         # small H.264: used as it is
        table, missing = proxies.table(self.clips, folder)
        self.assertEqual((sorted(table), missing), (["C001", "C002", "C004"], []))
        self.assertEqual((table["C002"]["width"], table["C002"]["height"]), (426, 240))
        again = proxies.make(self.clips, folder, height=240)
        self.assertEqual((again["made"], again["kept"]), (0, 3))
        # other footage under the same name: its proxy no longer stands in for it
        changed = [dict(c) for c in self.clips]
        make_clip(self.d / "other.mp4", "640x480", seconds=3)
        changed[0]["path"] = str(self.d / "other.mp4")
        table, missing = proxies.table(changed, folder)
        self.assertEqual((sorted(table), missing), (["C002", "C004"], ["C001"]))
        # a clip that is gone takes its proxy with it
        proxies.make(self.clips[:3], folder, height=240)
        self.assertFalse((folder / "C004.mp4").exists())

    def test_a_proxy_that_is_not_as_long_as_its_clip_is_not_used(self):
        from cutter import proxies
        folder = self.d / "proxies_short"
        wrong = dict(self.clips[0], duration=5.0)                # the clip list says 5 s, the file has 2
        r = proxies.make([wrong], folder, height=240)
        self.assertEqual((r["made"], [f["clip"] for f in r["failed"]]), (0, ["C001"]))
        self.assertEqual(list(folder.glob("*.mp4")), [])
        self.assertEqual(proxies.table([wrong], folder), ({}, ["C001"]))


@unittest.skipUnless(TOOLS and FFMPEG, "needs numpy, OpenTimelineIO and ffmpeg")
class Drafts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cutter import demo, proxies, timeline
        from cutter.config import Project
        cls.tmp = TempDir()
        d = cls.tmp.__enter__()
        demo.media(d / "footage")
        cls.film = golden_demo.setup_project(d, media_root=str(d / "footage"))
        with open(cls.film / "cutter.toml", "a", encoding="utf-8") as f:
            f.write("\n[proxies]\nheight = 360\n")
        p = Project.load(cls.film)
        clips = timeline.resolve_clips({c["id"]: c for c in json.loads((cls.film / "analysis/clips.json").read_text())},
                                       p.media_roots())
        cls.made = proxies.make(list(clips.values()), p.path("proxies"), height=p["proxies"]["height"])
        golden_demo.cli("retime", "cut1", "--project", cls.film)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def media(self):
        """{clip name on the timeline: (file, scale in percent)} of the picture track."""
        otio = json.loads((self.film / "cuts/cut1/cut1.otio").read_text())
        v1 = next(t for t in otio["tracks"]["children"] if t["name"].startswith("V1"))
        out = {}
        for it in v1["children"]:
            if it["OTIO_SCHEMA"].startswith("Clip"):
                ref = it["media_references"][it["active_media_reference_key"]]
                out[it["name"]] = (pathlib.Path(ref["target_url"].replace("file://", "")).name,
                                   it["metadata"]["cutter"]["scale_percent"])
        return otio, out

    def test_a_draft_plays_the_proxies_and_the_final_timeline_the_footage(self):
        self.assertEqual((self.made["made"], self.made["failed"]), (5, []))
        final = golden_demo.cli("timeline", "cut1", "--project", self.film)
        otio, footage = self.media()
        self.assertEqual(final["media"], "footage")
        self.assertNotIn("cutter", otio.get("metadata", {}))
        draft = golden_demo.cli("timeline", "cut1", "--proxies", "--project", self.film)
        otio, proxy = self.media()
        self.assertEqual((draft["media"], otio["metadata"]["cutter"]["media"]), ("proxies", "proxies"))
        self.assertEqual(draft["timeline"]["duration"], final["timeline"]["duration"])
        self.assertEqual(list(proxy), list(footage))             # the same shots under the same names
        for name, (file, scale) in proxy.items():
            with self.subTest(clip=name):
                self.assertRegex(file, r"^C\d+\.mp4$")
                self.assertIn("clip0", name)                     # the name still says which footage it is
                self.assertAlmostEqual(scale, 300.0, delta=0.5)  # 360 lines fill the 1080 of the film
        xml = (self.film / "cuts/cut1/cut1.fcp7.xml").read_text()
        self.assertIn("analysis/proxies/C001.mp4", xml)
        self.assertNotIn("footage/camera", xml)
        # the edit list is the same whichever was built: levels are measured on the footage
        edit = (self.film / "cuts/cut1/edit.json").read_text()
        golden_demo.cli("timeline", "cut1", "--project", self.film)
        self.assertEqual((self.film / "cuts/cut1/edit.json").read_text(), edit)
        self.assertNotIn("analysis/proxies", (self.film / "cuts/cut1/cut1.fcp7.xml").read_text())

    def test_the_rough_render_follows_the_timeline(self):
        golden_demo.cli("timeline", "cut1", "--proxies", "--project", self.film)
        r = golden_demo.cli("review-data", "cut1", "--rough", "--project", self.film)
        self.assertEqual(r["media"], "proxies")
        video = self.film / ".cutter/review/data/cuts/cut1/review.mp4"
        self.assertGreater(video.stat().st_size, 50_000)
        n, w, h, _ = frames(video)
        self.assertAlmostEqual(n / 25, json.loads((video.parent / "timeline.json").read_text())["duration"], delta=0.2)

    def test_without_proxies_a_draft_says_what_to_do(self):
        from cutter.util import CutterError
        with TempDir() as d:
            film = golden_demo.setup_project(d)
            with self.assertRaises(CutterError) as ex:
                from cutter import cli
                cli.c_timeline(cli.parser().parse_args(["timeline", "cut1", "--proxies", "--project", str(film)]), None)
            self.assertIn("analyze --proxies", ex.exception.hint)


if __name__ == "__main__":
    unittest.main()
