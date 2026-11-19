"""Picture formats (frame.py) and reframing (reframe.py): 9:16 for Reels, 4:5, 1:1 … through timeline, titles,
FilmCraft batch and the review render."""
import json
import pathlib
import subprocess
import unittest

from helpers import FFMPEG, TOOLS, TempDir
import golden_demo
from cutter import frame, reframe, review_data, titles
from cutter.cli import export_scale
from cutter.util import CutterError


class Formats(unittest.TestCase):
    def test_names_sizes_and_custom(self):
        self.assertEqual((frame.get(None).w, frame.get(None).h), (1920, 1080))
        f = frame.get("9:16")
        self.assertEqual((f.name, f.w, f.h, f.tall), ("9:16", 1080, 1920, True))
        self.assertEqual(frame.get("1080x1350").name, "4:5")
        self.assertEqual(frame.get([3840, 2160]).name, "3840x2160")
        self.assertEqual(frame.get({"width": 1080, "height": 1080}).name, "1:1")
        self.assertEqual(frame.of({"frame": "21:9"}, "9:16").name, "21:9")
        self.assertEqual(frame.of({}, "9:16").name, "9:16")
        for bad in ("5:7", "1081x1920", "50x50", "wide"):
            with self.assertRaises(CutterError, msg=bad):
                frame.get(bad)

    def test_layout_moves_into_the_frame_and_out_of_the_covered_parts(self):
        hd, tall, sq = frame.get("16:9"), frame.get("9:16"), frame.get("1:1")
        self.assertEqual(hd.point([960, 1000]), [960, 1000])                   # laid out for 16:9: unchanged
        self.assertEqual(sq.point([960, 540]), [540.0, 540.0])
        self.assertEqual(tall.point([960, 540]), [540.0, 960.0])
        self.assertEqual(tall.point([960, 1050])[1], 1920 - round(0.22 * 1920))  # not under the app's caption
        self.assertEqual(tall.point([960, 20])[1], round(0.14 * 1920))
        self.assertEqual(tall.review_size(), (720, 1280))
        self.assertEqual(frame.get("21:9").review_size(360), (854, 360))

    def test_review_and_export_sizes_follow_the_shorter_side(self):
        tall = frame.get("9:16")
        self.assertEqual(review_data.frame_size(None, tall), (720, 1280))
        self.assertEqual(review_data.frame_size(360, tall), (360, 640))
        self.assertEqual(review_data.frame_size(360), (640, 360))
        self.assertEqual(export_scale(360, tall), ["--scale", "0.333333"])
        self.assertEqual(export_scale(1080, tall), [])


class Reframing(unittest.TestCase):
    TALL = frame.get("9:16")

    def test_shown_share(self):
        sx, sy = reframe.shown(self.TALL, 1920, 1080)
        self.assertAlmostEqual(sx, 0.3164, places=3)
        self.assertEqual(reframe.shown(self.TALL, 1080, 1920), (1.0, 1.0))
        self.assertEqual(reframe.shown(self.TALL, 1920, 1080, rotation=90), (1.0, 1.0))   # upright: fits

    def test_the_window_goes_where_the_subject_is(self):
        frames = [[t, [["face", 0.2, 0.4, 0.05, 0.1, 0.9], ["person", 0.22, 0.6, 0.1, 0.5, 0.8]]] for t in (0.5, 1.5, 2.5)]
        focus, why = reframe.suggest(frames, 0, 3, 0.316, 1.0)
        self.assertEqual(why, "faces")
        self.assertAlmostEqual(focus[0], 0.2, delta=0.03)
        self.assertEqual(focus[1], 0.5)                                         # height not cut: middle
        # outside the shot's in/out: not counted
        self.assertEqual(reframe.suggest(frames, 10, 12, 0.316, 1.0), ([0.5, 0.5], "centre"))
        # two people too far apart for one window: the window holds the larger one
        two = [[0.5, [["person", 0.1, 0.5, 0.05, 0.2, 0.9], ["person", 0.85, 0.5, 0.15, 0.6, 0.9]]]]
        self.assertAlmostEqual(reframe.suggest(two, 0, 1, 0.316, 1.0)[0][0], 0.842, delta=0.01)
        # the edge of the picture: no further than the picture reaches
        edge = [[0.5, [["face", 0.02, 0.5, 0.03, 0.06, 0.9]]]]
        self.assertAlmostEqual(reframe.suggest(edge, 0, 1, 0.316, 1.0)[0][0], 0.158, delta=0.001)

    def test_offset_keeps_the_frame_filled(self):
        self.assertEqual(reframe.offset(self.TALL, 1920, 1080, 0, [0.5, 0.5]), [0.0, 0.0])
        dx = reframe.offset(self.TALL, 1920, 1080, 0, [0.2, 0.5])[0]
        self.assertAlmostEqual(dx, 0.3 * 3413.3, delta=1)                         # picture 3413 px wide at 177.8 %
        self.assertAlmostEqual(reframe.offset(self.TALL, 1920, 1080, 0, [0.0, 0.5])[0], (3413.3 - 1080) / 2, delta=1)

    def test_plan_own_focus_and_centred_shots(self):
        clips = {"A": {"width": 1920, "height": 1080}, "B": {"width": 1080, "height": 1920}}
        edit = {"sections": [{"name": "x", "shots": [
            {"nr": 1, "clip": "A", "in": 0, "out": 3, "focus": 0.25},
            {"nr": 2, "clip": "B", "in": 0, "out": 3},                          # portrait in 9:16: nothing cut off
            {"nr": 3, "clip": "A", "in": 0, "out": 3},                          # nothing found: stays centred
            {"nr": 4, "clip": "B", "in": 0, "out": 3, "cutaways": [{"clip": "A", "in": 1, "out": 2, "focus": [0.7, 0.5]}]}]}]}
        p = reframe.plan(edit, clips, {}, "9:16")
        self.assertEqual(sorted(p["shots"]), [1, 4])
        self.assertEqual(p["shots"][1]["why"], "edit")
        self.assertGreater(p["shots"][1]["offset"][0], 0)
        self.assertEqual(p["shots"][4]["offset"], [0.0, 0.0])
        self.assertLess(p["shots"][4]["cutaways"][0]["offset"][0], 0)
        self.assertEqual(reframe.plan(edit, clips, {}, "16:9")["shots"].keys() - {2, 4}, set())


class Titles(unittest.TestCase):
    def test_titles_and_end_cards_move_into_a_tall_frame(self):
        fc = {"titles": [{"text": "A title", "font": "DejaVu Sans", "size": 90, "position": [960, 540], "align": "center",
                          "time": 0, "duration": 3}],
              "credits": {"styles": {"s": {"font": "DejaVu Sans"}}, "layers": [
                  {"style": "s", "text": "Music:", "size": 60, "position": [300, 500], "align": "left"},
                  {"style": "s", "text": "A long line of credits for the music", "size": 40, "position": [800, 500],
                   "align": "left"}]},
              "captions": {"style": {"size": 48, "margin": 0.06}}}
        edit = {"end_text": {"style": {"center_y": 950, "size": 50, "font": "DejaVu Sans", "band": {"height": {"1": 90}}},
                             "cards": [{"text": "The end"}]}}
        tall = frame.get("9:16")
        fc2, edit2 = titles.for_frame(fc, edit, tall)
        self.assertEqual(fc["titles"][0]["position"], [960, 540], "the originals stay as they are")
        self.assertEqual(fc2["titles"][0]["position"], [540.0, 960.0])
        self.assertEqual(edit2["end_text"]["style"]["center_y"], 1920 - round(0.22 * 1920))
        self.assertEqual(fc2["captions"]["style"]["margin"], 0.22)
        xs = [L["position"][0] for L in fc2["credits"]["layers"]]
        self.assertTrue(all(0 < x < 1080 for x in xs), xs)
        self.assertLess(fc2["credits"]["layers"][1]["size"], 40, "the credits as a block, smaller to fit")
        self.assertIs(titles.for_frame(fc, edit, frame.get("16:9"))[0], fc)


@unittest.skipUnless(TOOLS and FFMPEG, "needs numpy, OpenTimelineIO and ffmpeg")
class Pipeline(unittest.TestCase):
    def test_a_reel_from_the_demo(self):
        """edit.json "frame": "9:16": the timeline, the FilmCraft batch (stand-in) and the review render are 1080x1920
        (720x1280); a shot's own focus moves its picture in the batch and the render."""
        from cutter import demo
        with TempDir() as d:
            demo.media(d / "footage")
            film = golden_demo.setup_project(d, media_root=str(d / "footage"))
            golden_demo.cli("retime", "cut1", "--project", film)
            ep = film / "cuts" / "cut1" / "edit.json"
            edit = json.loads(ep.read_text(encoding="utf-8"))
            edit["frame"] = "9:16"
            first = next(s for sec in edit["sections"] for s in sec["shots"] if s.get("clip") != "TXT")
            first["focus"] = 0.2
            ep.write_text(json.dumps(edit), encoding="utf-8")
            r = golden_demo.cli("timeline", "cut1", "--project", film)
            self.assertEqual(r["frame"], {"name": "9:16", "width": 1080, "height": 1920})
            self.assertEqual(r["reframed"], 1)
            otio = json.loads((film / "cuts" / "cut1" / "cut1.otio").read_text(encoding="utf-8"))
            sc = otio["metadata"]["fcp_xml"]["media"]["video"]["format"]["samplecharacteristics"]
            self.assertEqual((sc["width"], sc["height"]), (1080, 1920))
            xml = (film / "cuts" / "cut1" / "cut1.fcp7.xml").read_text(encoding="utf-8")
            self.assertIn("<width>1080</width>", xml)

            b = golden_demo.cli("filmcraft", "build", "cut1", "--project", film, "--dry-run")
            batch = [json.loads(x) for x in pathlib.Path(b["batch"]).read_text(encoding="utf-8").splitlines()]
            pos = [x["params"]["value"] for x in batch if x["params"].get("param") == "position"
                   and x["params"].get("effect") == "motion"]
            self.assertTrue(pos and pos[0][0] > 540 and pos[0][1] == 960, pos)

            golden_demo.cli("review-data", "cut1", "--project", film, "--rough", "--no-numbers")
            folder = film / ".cutter" / "review" / "data" / "cuts" / "cut1"
            tl = json.loads((folder / "timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(tl["frame"]["name"], "9:16")
            self.assertEqual((tl["video"]["width"], tl["video"]["height"]), (720, 1280))
            self.assertEqual((tl["thumbnails"]["width"], tl["thumbnails"]["height"]), (108, 192))
            # shot 1 shows the left part of the test picture: the frame counter in its top left corner is in view
            png = d / "shot1.png"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", "0.5", "-i", str(folder / "review.mp4"),
                            "-frames:v", "1", "-vf", "crop=200:60:0:0,format=gray", "-f", "rawvideo", str(png)], check=True)
            dark = sum(1 for v in png.read_bytes() if v < 60) / (200 * 60)
            self.assertGreater(dark, 0.2, "the counter's dark box")


if __name__ == "__main__":
    unittest.main()
