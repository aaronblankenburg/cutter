"""alternatives.json: places where the agent weighed other clips or ranges; cutter review-data renders a preview of
every option and puts the places into timeline.json."""
import json
import pathlib
import tempfile
import unittest

from helpers import FFMPEG
from cutter import review_data
from cutter.util import CutterError

from test_review_sizes import clip

TL = {"duration": 20.0, "shots": [{"nr": 1, "start": 0.0, "end": 8.0, "clip": "C001", "source_in": 2.0, "source_out": 10.0},
                                  {"nr": 2, "start": 8.0, "end": 20.0, "clip": "C002", "source_in": 0.0, "source_out": 12.0}]}


def write(d, places):
    p = pathlib.Path(d) / "alternatives.json"
    p.write_text(json.dumps({"places": places}), encoding="utf-8")
    return p


class Places(unittest.TestCase):
    def test_place_gets_the_time_of_its_shot(self):
        with tempfile.TemporaryDirectory() as d:
            p = write(d, [{"id": "a2", "nr": 2, "reason": "the arrival",
                           "options": [{"clip": "C007", "in": 3, "out": 9.5, "why": "the car is in the picture"}]}])
            [place] = review_data.alternatives(p, TL, {"C007": "/x/C007.mp4"})
            self.assertEqual((place["t"], place["t_to"], place["current"]["clip"]), (8.0, 20.0, "C002"))
            self.assertEqual(place["options"][0]["n"], 1)
            self.assertEqual(place["options"][0]["_path"], "/x/C007.mp4")
            self.assertNotIn("_path", json.dumps(review_data.without_internal(place)))

    def test_the_clip_finds_the_shot_when_numbers_moved(self):
        tl = {"duration": 30.0, "shots": [
            {"nr": 1, "start": 0.0, "end": 8.0, "clip": "C001", "source_in": 2.0, "source_out": 10.0},
            {"nr": 2, "start": 8.0, "end": 20.0, "clip": "C002", "source_in": 0.0, "source_out": 12.0},
            {"nr": 3, "start": 20.0, "end": 25.0, "clip": "C003", "source_in": 0.0, "source_out": 5.0},
            {"nr": 4, "start": 25.0, "end": 30.0, "clip": "C001", "source_in": 20.0, "source_out": 25.0}]}
        opt = [{"clip": "C007", "in": 0, "out": 1}]
        with tempfile.TemporaryDirectory() as d:
            a, b, c = review_data.alternatives(write(d, [
                {"id": "a", "nr": 2, "clip": "C003", "options": opt},     # moved by one: the nearest shot of C003
                {"id": "b", "clip": "C002", "options": opt},              # the clip alone, in one shot
                {"id": "c", "nr": 4, "clip": "C001", "options": opt}]), tl, {})
            self.assertEqual((a["nr"], a["t"], a["current"]["clip"]), (3, 20.0, "C003"))
            self.assertEqual((b["nr"], b["t"], b["t_to"]), (2, 8.0, 20.0))
            self.assertEqual((c["nr"], c["current"]["in"]), (4, 20.0))
            for bad in ({"id": "x", "clip": "C001", "options": opt},       # in two shots: nr needed
                        {"id": "x", "nr": 1, "clip": "C009", "options": opt}):
                with self.assertRaises(CutterError, msg=bad):
                    review_data.alternatives(write(d, [bad]), tl, {})

    def test_mistakes_are_named(self):
        bad = [[{"id": "a", "nr": 9, "options": [{"clip": "C1", "in": 0, "out": 1}]}],
               [{"id": "a", "nr": 1, "options": []}],
               [{"id": "a", "nr": 1, "options": [{"clip": "C1", "in": 0, "out": 1}] * 4}],
               [{"id": "a b", "nr": 1, "options": [{"clip": "C1", "in": 0, "out": 1}]}],
               [{"id": "a", "nr": 1, "options": [{"clip": "C1", "in": 2, "out": 1}]}]]
        with tempfile.TemporaryDirectory() as d:
            for places in bad:
                with self.assertRaises(CutterError, msg=places):
                    review_data.alternatives(write(d, places), TL, {})


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class Previews(unittest.TestCase):
    def test_previews_are_rendered_kept_and_removed(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            clip(d / "C007.mp4", "1280x720", 25, seconds=4)
            places = [{"id": "a1", "nr": 1, "options": [{"clip": "C007", "in": 1, "out": 3}, {"clip": "C404", "in": 0, "out": 1}]}]
            alts = review_data.alternatives(write(d, places), TL, {"C007": str(d / "C007.mp4")})
            out = d / "cut1"
            out.mkdir()
            self.assertEqual(review_data.alternative_previews(alts, out), 1)
            o1, o2 = alts[0]["options"]
            self.assertEqual((o1["file"], o1["poster"], o2["file"]), ("alt-a1-1.mp4", "alt-a1-1.jpg", None))
            p = review_data.probe(out / "alt-a1-1.mp4")
            self.assertEqual(p["height"], 360)
            self.assertAlmostEqual(p["duration"], 2.0, delta=0.15)
            self.assertTrue(p["audio"])
            mtime = (out / "alt-a1-1.mp4").stat().st_mtime_ns
            again = review_data.alternatives(write(d, places), TL, {"C007": str(d / "C007.mp4")})
            review_data.alternative_previews(again, out, review_data.without_internal(alts))
            self.assertEqual((out / "alt-a1-1.mp4").stat().st_mtime_ns, mtime, "an unchanged option is not rendered again")
            review_data.alternative_previews([], out)
            self.assertEqual(list(out.glob("alt-*")), [])


if __name__ == "__main__":
    unittest.main()
