"""Colour correction: measuring pictures, the suggestion per shot, switching it off, the preview model.

The measurement and the preview need numpy (and OpenCV for video files); the rules work on plain numbers.
"""
import unittest

from helpers import have_modules

from cutter import color

NUMPY = have_modules("numpy")


def frame(t, p02=0.05, p50=0.42, p98=0.9, rgb=(0.5, 0.5, 0.5), neutral=0.2):
    return [t, p02, p50, p98, *rgb, neutral]


class Suggestion(unittest.TestCase):
    def test_a_good_picture_is_left_alone(self):
        self.assertEqual(color.suggest([frame(0), frame(1)]), {})

    def test_dark_picture_is_brightened_without_clipping_its_highlights(self):
        s = color.suggest([frame(0, p50=0.18, p98=0.6)])
        self.assertGreater(s["exposure"], 0.4)
        self.assertLessEqual(s["exposure"], 0.6)
        # bright parts in the picture (sky at dusk, a lamp): not brightened
        self.assertNotIn("exposure", color.suggest([frame(0, p50=0.18, p98=0.93)]))

    def test_night_stays_dark(self):
        self.assertNotIn("exposure", color.suggest([frame(0, p02=0.0, p50=0.05, p98=0.5)]))

    def test_bright_picture_is_darkened(self):
        self.assertLess(color.suggest([frame(0, p50=0.75, p98=1.0)])["exposure"], -0.3)

    def test_blue_cast_is_warmed_and_green_cast_moved_to_magenta(self):
        s = color.suggest([frame(0, rgb=(0.42, 0.5, 0.6))])
        self.assertGreater(s["temperature"], 20)
        s = color.suggest([frame(0, rgb=(0.48, 0.56, 0.48))])
        self.assertGreaterEqual(s["tint"], 10)

    def test_warm_light_is_cooled_only_half(self):
        warm = color.suggest([frame(0, rgb=(0.54, 0.5, 0.46))])["temperature"]
        cold = color.suggest([frame(0, rgb=(0.46, 0.5, 0.54))])["temperature"]
        self.assertLess(warm, 0)
        self.assertLess(abs(warm), abs(cold) * 0.6)

    def test_low_light_keeps_its_colour(self):
        # camp fire, dusk: the colour of the light is the mood
        self.assertNotIn("temperature", color.suggest([frame(0, p50=0.15, p98=0.6, rgb=(0.6, 0.45, 0.3), neutral=0.3)]))

    def test_no_white_balance_without_neutral_area(self):
        # red dunes: almost nothing neutral in the picture
        self.assertNotIn("temperature", color.suggest([frame(0, rgb=(0.7, 0.4, 0.3), neutral=0.01)]))

    def test_hazy_picture_gets_deeper_blacks(self):
        self.assertLessEqual(color.suggest([frame(0, p02=0.2)])["blacks"], -15)

    def test_limits(self):
        s = color.suggest([frame(0, p50=0.02, p98=0.05, rgb=(0.2, 0.5, 0.9))])
        for k, v in s.items():
            lo, hi = color.LIMITS[k]
            self.assertTrue(lo <= v <= hi, (k, v))

    def test_only_the_frames_of_the_shot_count(self):
        frames = [frame(t, p50=0.15 if t >= 10 else 0.42, p98=0.5 if t >= 10 else 0.9) for t in range(20)]
        self.assertEqual(color.suggest(frames, 0, 8), {})
        self.assertGreater(color.suggest(frames, 11, 18)["exposure"], 0.5)
        self.assertEqual(color.suggest([], 0, 3), {})


EDIT = {"sections": [
    {"name": "Morning", "shots": [
        {"nr": 1, "clip": "C001", "in": 0, "out": 4},
        {"nr": 2, "clip": "C002", "in": 0, "out": 4, "cutaways": [{"clip": "C003", "in": 0, "out": 2, "at": 1}]},
        {"nr": 3, "clip": "C004", "in": 0, "out": 4, "color": False},
        {"nr": 4, "clip": "C001", "in": 0, "out": 4, "color": {"exposure": 0.3}}]}]}
CLIPS = {f"C00{i}": {"id": f"C00{i}", "width": 1920, "height": 1080, "codec": "h264", "file": f"DSC_000{i}.MOV"}
         for i in range(1, 5)}
DATA = {"clips": {
    "C001": {"frames": [frame(t, p50=0.2, p98=0.6) for t in range(5)]},
    "C002": {"frames": [frame(t, p50=0.2, p98=0.6) for t in range(5)]},
    "C003": {"frames": [frame(t, rgb=(0.4, 0.5, 0.62)) for t in range(5)]},
    "C004": {"frames": [frame(t, p50=0.2, p98=0.6) for t in range(5)]}}}


class Plan(unittest.TestCase):
    def test_shots_cutaways_and_the_edit_lists_own_values(self):
        pl = color.plan(EDIT, CLIPS, DATA)
        self.assertTrue(pl["on"])
        self.assertGreater(pl["shots"][1]["values"]["exposure"], 0.3)
        self.assertGreater(pl["shots"][2]["cutaways"][0]["temperature"], 15)
        self.assertEqual(pl["shots"][3]["values"], {})               # "color": false
        self.assertFalse(pl["shots"][3]["on"])
        self.assertEqual(pl["shots"][4]["values"], {"exposure": 0.3})  # own values replace the suggestion

    def test_switched_off(self):
        self.assertFalse(any(s["values"] for s in color.plan(EDIT, CLIPS, DATA, enabled=False)["shots"].values()))
        review = color.choices([{"color": "all/off", "created": "1"}])
        pl = color.plan(EDIT, CLIPS, DATA, review=review)
        self.assertFalse(pl["on"])
        self.assertTrue(all(not s["values"] and not any(s["cutaways"]) for s in pl["shots"].values()))
        self.assertTrue(pl["shots"][1]["suggested"], "the suggestion stays visible")
        review = color.choices([{"color": "1/off", "created": "1"}, {"color": "all/off", "created": "2"},
                                {"color": "all/on", "created": "3"}])
        pl = color.plan(EDIT, CLIPS, DATA, review=review)
        self.assertEqual(pl["shots"][1]["values"], {})
        self.assertTrue(pl["shots"][2]["values"])

    def test_one_camera_in_a_section_is_evened_out(self):
        data = {"clips": {"C001": {"frames": [frame(0, p50=0.12, p98=0.4)]},
                          "C002": {"frames": [frame(0, p50=0.28, p98=0.7)]}}}
        edit = {"sections": [{"name": "A", "shots": [{"nr": 1, "clip": "C001", "in": 0, "out": 1},
                                                    {"nr": 2, "clip": "C002", "in": 0, "out": 1}]}]}
        own = {nr: color.suggest(data["clips"][c]["frames"]).get("exposure", 0) for nr, c in ((1, "C001"), (2, "C002"))}
        pl = color.plan(edit, CLIPS, data)
        got = {nr: pl["shots"][nr]["values"].get("exposure", 0) for nr in (1, 2)}
        self.assertLess(abs(got[1] - got[2]), abs(own[1] - own[2]))

    def test_describe(self):
        self.assertEqual(color.describe({"exposure": 0.4, "temperature": -12}), "exposure +0.4, temperature -12")


@unittest.skipUnless(NUMPY, "needs numpy")
class Pictures(unittest.TestCase):
    def picture(self, cast=(1.0, 1.0, 1.0), gain=1.0):
        import numpy as np
        rng = np.random.default_rng(1)
        grey = rng.uniform(0.2, 0.8, (90, 160, 1)).repeat(3, axis=2)
        return np.clip(grey * np.array(cast) * gain, 0, 1).astype(np.float32)

    def test_measured_cast_is_removed_by_the_preview_model(self):
        import numpy as np
        img = self.picture(cast=(0.85, 1.0, 1.15))
        st = color.frame_stats(img)
        s = color.suggest([[0] + st])
        self.assertGreater(s["temperature"], 15)
        after = color.apply_preview(img, s)
        before_rb = img[..., 2].mean() / img[..., 0].mean()
        after_rb = after[..., 2].mean() / after[..., 0].mean()
        self.assertLess(abs(np.log(after_rb)), abs(np.log(before_rb)) * 0.85)       # partly: STRENGTH and LIMITS
        self.assertGreater(after_rb, 1.0, "not overcorrected")

    def test_dark_picture_measured_and_brightened(self):
        img = self.picture(gain=0.35)
        s = color.suggest([[0] + color.frame_stats(img)])
        self.assertGreater(s["exposure"], 0.4)
        self.assertGreater(color.apply_preview(img, s).mean(), img.mean() * 1.15)


if __name__ == "__main__":
    unittest.main()
