"""What the image and sound analysis derives from its measurements: camera moves, usable ranges, wind, telemetry,
the quality bar – and the whole analysis on a generated clip with a known pan and a known blur (needs OpenCV).

Everything is generated at run time; nothing is downloaded (CUTTER_TEST=1 keeps the detection models away).
"""
import json
import math
import random
import struct
import subprocess
import unittest

from helpers import FFMPEG, TempDir, have_modules

from cutter import quality, telemetry

NUMPY = have_modules("numpy")
OPENCV = NUMPY and have_modules("cv2") and FFMPEG


def samples(parts, sharp=None):
    """parts: [(seconds, gx, gy, zoom, jitter)] at five samples a second."""
    out, t, rnd = [], 0.0, random.Random(3)
    for sec, gx, gy, zoom, jitter in parts:
        for _ in range(round(sec * 5)):
            out.append({"t": round(t, 2), "gx": gx + rnd.choice((-1, 1)) * jitter, "gy": gy + rnd.choice((-1, 1)) * jitter,
                        "zoom": zoom, "sharp": (sharp(t) if sharp else 100.0), "low": 0.0, "high": 0.0})
            t += 0.2
    return out


class CameraAndUsable(unittest.TestCase):
    def test_moves_become_segments(self):
        s = samples([(4, 0, 0, 0, 0), (3, -12, 0, 0, 0), (3, 0, 0, 0, 0), (3, 0, 0, 0, 5), (3, 0, 6, 0, 0), (3, 0, 0, 8, 0)])
        values, seg = quality.camera(s, 19.0)
        self.assertEqual([x["move"] for x in seg], ["static", "pan_right", "static", "shaky", "tilt_up", "push_in"])
        self.assertEqual(seg[0]["start"], 0.0)
        self.assertEqual(seg[-1]["end"], 19.0)
        self.assertAlmostEqual(seg[1]["start"], 4.0, delta=0.61)        # the pan is found to about half a second
        self.assertAlmostEqual(seg[1]["end"], 7.0, delta=0.61)
        self.assertAlmostEqual(seg[1]["speed"], 12.0, delta=2.5)
        for a, b in zip(seg, seg[1:]):
            self.assertEqual(a["end"], b["start"])
        self.assertEqual(len(values), len(s))

    def test_a_jolt_is_shake_not_a_pan(self):
        s = samples([(4, 0, 0, 0, 0), (0.4, -12, 0, 0, 0), (4, 0, 0, 0, 0)])
        self.assertLessEqual({x["move"] for x in quality.camera(s)[1]}, {"static", "shaky"})
        s = samples([(4, 0, 0, 0, 0), (0.4, -2.6, 0, 0, 0), (4, 0, 0, 0, 0)])          # too short and slow to be a move
        self.assertEqual([x["move"] for x in quality.camera(s)[1]], ["static"])

    def test_usable_ranges_and_flaws(self):
        s = samples([(5, 0, 0, 0, 0), (3, 0, 0, 0, 6), (5, -8, 0, 0, 0)], sharp=lambda t: 20.0 if 0.6 <= t < 2.4 else 100.0)
        values, _ = quality.camera(s, 13.0)
        good, flaws = quality.usable(s, values, 13.0)
        self.assertEqual([(g["camera"], g["steady"]) for g in good], [("static", True), ("pan_right", False)])
        self.assertAlmostEqual(good[0]["start"], 2.4, delta=0.21)
        self.assertEqual(good[-1]["end"], 13.0)
        why = {w for f in flaws for w in f["why"]}
        self.assertLessEqual({"soft", "shaky"}, why)
        self.assertEqual(flaws[0]["start"], 0.0)                        # the moment before the blur is too short to use
        covered = sorted((r["start"], r["end"]) for r in good + flaws)
        self.assertEqual(covered[0][0], 0.0)
        for a, b in zip(covered, covered[1:]):
            self.assertAlmostEqual(a[1], b[0], places=2)

    def test_one_soft_frame_does_not_split_a_range(self):
        s = samples([(6, 0, 0, 0, 0)], sharp=lambda t: 10.0 if abs(t - 3.0) < 0.05 else 100.0)
        good, _ = quality.usable(s, quality.camera(s, 6.0)[0], 6.0)
        self.assertEqual([(g["start"], g["end"]) for g in good], [(0.0, 6.0)])

    def test_ranges(self):
        r = quality.ranges(range(10), [0, 1, 1, 0, 1, 0, 0, 1, 1, 1], min_len=2.0)
        self.assertEqual(r, [{"start": 1, "end": 3.0}, {"start": 7, "end": 10.0}])
        r = quality.ranges(range(10), [0, 1, 1, 0, 1, 0, 0, 1, 1, 1], min_len=2.0, close_gap=1.0, end=9.5)
        self.assertEqual(r, [{"start": 1, "end": 5.0}, {"start": 7, "end": 9.5}])


def item(key, typ, size, values):
    """One GPMF item; values: bytes for nested data, else a list of packed samples."""
    payload = values if isinstance(values, bytes) else b"".join(values)
    repeat = len(payload) // size if size else 0
    return key.encode() + bytes([ord(typ) if typ else 0, size]) + struct.pack(">H", repeat) + payload + b"\0" * (-len(payload) % 4)


def gpmf(seconds, gps9=False):
    """A gpmd track as GoPro writes it: one DEVC per second with a gyroscope stream and a GPS stream."""
    out = b""
    for k in range(seconds):
        gyro = [struct.pack(">hhh", 100 * (-1) ** n, 0, 400 if k >= 2 else 0) for n in range(200)]      # /1000 rad/s
        s1 = item("SCAL", "s", 2, [struct.pack(">h", 1000)]) + item("GYRO", "s", 6, gyro)
        lat, lon = 36.8 + k * 0.0001, 10.18
        if gps9:
            row = struct.pack(">lllllllSS".replace("S", "H"), round(lat * 1e7), round(lon * 1e7), 12000, 5000, 5000, 8000, 1000, 150, 3)
            s2 = (item("TYPE", "c", 9, b"lllllllSS") + item("SCAL", "l", 4, [struct.pack(">l", v) for v in
                  (10000000, 10000000, 1000, 1000, 100, 1, 1000, 100, 1)]) + item("GPS9", "?", 32, [row] * 10))
        else:
            row = struct.pack(">lllll", round(lat * 1e7), round(lon * 1e7), 12000, 5000, 500)
            s2 = (item("GPSF", "L", 4, [struct.pack(">L", 3)]) + item("GPSP", "S", 2, [struct.pack(">H", 150)])
                  + item("SCAL", "l", 4, [struct.pack(">l", v) for v in (10000000, 10000000, 1000, 1000, 100)])
                  + item("GPS5", "l", 20, [row] * 18))
        out += item("DEVC", None, 1, item("DVNM", "c", 6, b"Camera") + item("STRM", None, 1, s1) + item("STRM", None, 1, s2))
    return out


class Telemetry(unittest.TestCase):
    def test_place_in_the_container_tags(self):
        self.assertEqual(telemetry.gps_from_tags({"com.apple.quicktime.location.iso6709": "+48.1374+011.5755+519.000/"}),
                         {"lat": 48.1374, "lon": 11.5755, "alt": 519.0})
        self.assertEqual(telemetry.gps_from_tags({"location": "-33.8568+151.2153/"}), {"lat": -33.8568, "lon": 151.2153})
        self.assertIsNone(telemetry.gps_from_tags({"location": "+00.0000+000.0000/"}))
        self.assertIsNone(telemetry.gps_from_tags({"title": "+48"}))

    def test_gopro_gyroscope_and_track(self):
        for gps9 in (False, True):
            with self.subTest(gps9=gps9):
                r = telemetry.gopro(gpmf(4, gps9), 4.0)
                self.assertEqual(r["source"], "gopro")
                self.assertEqual([g["t"] for g in r["gyro"]], [0.0, 1.0, 2.0, 3.0])
                self.assertAlmostEqual(r["gyro"][0]["shake"], math.degrees(0.1), delta=0.2)     # ±0.1 rad/s around zero
                self.assertAlmostEqual(r["gyro"][0]["turn"], 0.0, delta=0.2)
                self.assertAlmostEqual(r["gyro"][3]["turn"], math.degrees(0.4), delta=0.2)      # a steady 0.4 rad/s turn
                self.assertEqual(r["gps"], {"lat": 36.8, "lon": 10.18, "alt": 12.0})
                self.assertEqual(len(r["track"]), 4)
                self.assertAlmostEqual(r["distance_m"], 33, delta=2)                            # 3 x 0.0001° of latitude
                self.assertEqual(r["speed_kmh"]["mean"], 18.0)

    def test_gopro_without_a_fix_has_no_track(self):
        data = gpmf(2).replace(struct.pack(">L", 3) + b"GPSP", struct.pack(">L", 0) + b"GPSP")
        r = telemetry.gopro(data, 2.0)
        self.assertNotIn("track", r)
        self.assertIn("gyro", r)
        self.assertIsNone(telemetry.gopro(b"not gpmf at all", 2.0))

    def test_dji_flight_log(self):
        new = ("1\n00:00:00,000 --> 00:00:00,033\n<font size=\"28\">FrameCnt: 1 [iso: 100] [latitude: 47.3728] "
               "[longitude: 8.6562] [rel_alt: 19.0 abs_alt: 512.3]</font>\n\n"
               "2\n00:00:00,033 --> 00:00:00,066\n[latitude: 47.3729] [longitude: 8.6562] [rel_alt: 19.0 abs_alt: 512.3]\n\n"
               "3\n00:00:02,000 --> 00:00:02,033\n[latitude: 47.3738] [longtitude: 8.6562] [rel_alt: 20.0 abs_alt: 513.0]\n")
        r = telemetry.dji_srt(new)
        self.assertEqual(r["gps"], {"lat": 47.3728, "lon": 8.6562, "alt": 512.3})
        self.assertEqual([p[0] for p in r["track"]], [0.0, 2.0])                                # at most one point a second
        self.assertAlmostEqual(r["distance_m"], 111, delta=3)
        old = "1\n00:00:01,000 --> 00:00:02,000\nHOME(8.6500,47.3700) 2020.01.01 10:00:00\nGPS(8.6562,47.3728,19) BAROMETER:20.1\n"
        self.assertEqual(telemetry.dji_srt(old)["gps"], {"lat": 47.3728, "lon": 8.6562, "alt": 19.0})
        self.assertIsNone(telemetry.dji_srt("1\n00:00:01,000 --> 00:00:02,000\nHello, this is a subtitle.\n"))


@unittest.skipUnless(NUMPY, "needs numpy")
class Wind(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from cutter import audio_analysis
        self.np, self.A = np, audio_analysis
        rnd = np.random.default_rng(5)
        n = audio_analysis.SR

        def low_noise():
            spec = np.fft.rfft(rnd.standard_normal(n))
            spec[np.fft.rfftfreq(n, 1 / n) > 90] = 0
            y = np.fft.irfft(spec, n)
            return y / np.abs(y).max() * 0.5
        self.low_a, self.low_b = low_noise(), low_noise()
        t = np.arange(n) / n
        self.voice = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.2 * np.sin(2 * np.pi * 900 * t)

    def test_low_noise_that_the_channels_do_not_share_is_wind(self):
        np, A = self.np, self.A
        self.assertGreaterEqual(A.wind_score(np.stack((self.low_a, self.low_b), 1)), 0.8)
        self.assertGreaterEqual(A.wind_score(np.stack((self.low_a + 0.02 * self.voice, self.low_b), 1)), 0.8)

    def test_an_engine_reaches_both_microphones_alike(self):
        np, A = self.np, self.A
        self.assertEqual(A.wind_score(np.stack((self.low_a, self.low_a * 0.8), 1)), 0.0)

    def test_speech_and_tones_are_not_wind(self):
        np, A = self.np, self.A
        self.assertEqual(A.wind_score(np.stack((self.voice, self.voice), 1)), 0.0)
        self.assertEqual(A.wind_score(self.voice[:, None]), 0.0)

    def test_one_microphone_reports_rumble(self):
        np, A = self.np, self.A
        self.assertGreaterEqual(A.wind_score(self.low_a[:, None]), 0.8)
        self.assertGreaterEqual(A.wind_score(np.stack((self.low_a, self.low_a), 1), stereo=False), 0.8)


@unittest.skipUnless(NUMPY, "needs numpy")
class Takes(unittest.TestCase):
    def test_clips_that_look_alike_and_were_recorded_together_are_takes(self):
        import numpy as np
        from cutter import image_analysis
        rnd = np.random.default_rng(11)
        scene_a, scene_b = rnd.integers(0, 255, (6, 132)), rnd.integers(0, 255, (6, 132))

        def near(scene):
            return np.clip(scene + rnd.normal(0, 6, scene.shape), 0, 255).round().astype(int).tolist()
        clips = [{"id": "C001", "created": "2024-05-01 09:00:00", "width": 1920, "height": 1080},
                 {"id": "C002", "created": "2024-05-01 09:04:00", "width": 1920, "height": 1080},     # the same shot again
                 {"id": "C003", "created": "2024-05-01 09:06:00", "width": 1920, "height": 1080},     # something else
                 {"id": "C004", "created": "2024-05-01 15:00:00", "width": 1920, "height": 1080}]     # the same view, hours later
        with TempDir() as d:
            index = {}
            for c, scene in zip(clips, (scene_a, scene_a, scene_b, scene_a)):
                data = {"id": c["id"], "signature": near(scene), "summary": {"sharpness_mean": 100.0}}
                (d / f'{c["id"]}.json').write_text(json.dumps(data))
                index[c["id"]] = data["summary"]
            (d / "_index.json").write_text(json.dumps(index))
            self.assertEqual(image_analysis.find_takes(clips, d), [["C001", "C002"]])
            self.assertEqual(json.loads((d / "C002.json").read_text())["summary"]["similar"], ["C001"])
            index = json.loads((d / "_index.json").read_text())
            self.assertEqual(index["C001"]["similar"], ["C002"])
            self.assertNotIn("similar", index["C003"])
            self.assertNotIn("similar", index["C004"])
            (d / "C002.json").write_text(json.dumps({"id": "C002", "signature": near(scene_b), "summary": {}}))
            self.assertEqual(image_analysis.find_takes(clips, d), [["C002", "C003"]])           # a new run corrects it
            self.assertNotIn("similar", json.loads((d / "_index.json").read_text())["C001"])


@unittest.skipUnless(have_modules("PIL"), "needs Pillow")
class QualityBar(unittest.TestCase):
    def test_the_bar_is_added_once(self):
        from PIL import Image
        from cutter import analyze
        with TempDir() as d:
            (d / "sheets").mkdir()
            (d / "image").mkdir()
            sheet = d / "sheets" / "C001.jpg"
            Image.new("RGB", (600, 200), (24, 24, 24)).save(sheet)
            data = {"duration": 10.0, "usable": [{"start": 0, "end": 5, "camera": "static", "steady": True}],
                    "flaws": [{"start": 5, "end": 10, "why": ["shaky"]}], "audio": {"speech": [{"start": 1, "end": 3}]}}
            (d / "image" / "C001.json").write_text(json.dumps(data))
            self.assertEqual(analyze.add_quality_bars(d, d / "image"), 1)
            with Image.open(sheet) as im:
                height = im.height
                self.assertGreater(height, 200)
                r, g, b = im.convert("RGB").getpixel((150, 208))                 # inside the usable, steady range
                self.assertGreater(g, r + 40)
                r, g, b = im.convert("RGB").getpixel((450, 208))                 # inside the flaw
                self.assertGreater(r, g + 40)
            self.assertEqual(analyze.add_quality_bars(d, d / "image"), 0)        # up to date: not saved again
            data["usable"] = []
            (d / "image" / "C001.json").write_text(json.dumps(data))
            import os
            os.utime(d / "image" / "C001.json", (sheet.stat().st_mtime + 5,) * 2)
            self.assertEqual(analyze.add_quality_bars(d, d / "image"), 1)        # replaced, not stacked
            with Image.open(sheet) as im:
                self.assertEqual(im.height, height)


def face_vectors(seed, n, spread=0.25, size=128):
    """n vectors around one direction, as the faces of one person are."""
    import numpy as np
    rnd = np.random.default_rng(seed)
    centre = rnd.normal(size=size)
    centre /= np.linalg.norm(centre)
    out = centre + spread * rnd.normal(size=(n, size)) / math.sqrt(size) * 3
    return out / np.linalg.norm(out, axis=1, keepdims=True)


@unittest.skipUnless(NUMPY, "needs numpy")
class People(unittest.TestCase):
    def clip_file(self, d, cid, persons):
        """persons: [(seed, seconds)] -> the "faces" of a clip file as image_analysis writes them."""
        from cutter import people
        found = [{"t": t, "v": v, "size": 0.2, "score": 0.9}
                 for seed, secs in persons for t, v in zip(secs, face_vectors(seed, len(secs)))]
        faces = people.clip_faces(found)
        for g in faces:
            g.pop("best")
        (d / "image").mkdir(exist_ok=True)
        (d / "image" / f"{cid}.json").write_text(json.dumps({"id": cid, "faces": faces}))
        return faces

    def test_faces_of_a_clip_are_grouped_by_person(self):
        with TempDir() as d:
            faces = self.clip_file(d, "C001", [(1, range(0, 8)), (2, range(3, 6))])
            self.assertEqual([g["faces"] for g in faces], [8, 3])
            self.assertEqual(faces[1]["seconds"], [3, 4, 5])
            self.assertEqual(len(faces[0]["vector"]), 128)

    def test_two_people_whose_faces_have_much_in_common_stay_two(self):
        # Faces share a lot (they are all faces, seen by the same camera). The centres of two people's faces are
        # then more alike than any two of their faces; grouping by the centres would make one person of them.
        import numpy as np
        from cutter import people
        rnd = np.random.default_rng(5)
        common, a, b = (v / np.linalg.norm(v) for v in rnd.normal(size=(3, 128)))
        noise = rnd.normal(size=(40, 128))
        noise /= np.linalg.norm(noise, axis=1, keepdims=True)
        faces = [0.55 * common + 0.5 * (a if k < 20 else b) + 0.67 * noise[k] for k in range(40)]
        centres = people.unit([np.sum(faces[:20], axis=0), np.sum(faces[20:], axis=0)])
        self.assertGreater(float(centres[0] @ centres[1]), people.SAME)
        order = rnd.permutation(40)
        labels = people.group([faces[k] for k in order])
        self.assertEqual(len(set(labels)), 2)
        self.assertEqual(len({(labels[n], k < 20) for n, k in enumerate(order)}), 2)

    def test_people_are_found_across_clips_and_keep_id_and_name(self):
        from cutter import people
        from cutter.util import CutterError
        with TempDir() as d:
            clips = [{"id": f"C00{k}", "duration": 20.0} for k in (1, 2, 3)]
            self.clip_file(d, "C001", [(1, range(0, 8)), (2, range(3, 6))])
            self.clip_file(d, "C002", [(2, range(0, 10))])
            self.clip_file(d, "C003", [(3, [4])])                         # seen once: no ID
            file = d / "people.json"
            data = people.update(clips, d / "image", file)
            self.assertEqual([(p["id"], sorted(p["clips"]), p["seconds"]) for p in data["people"]],
                             [("P1", ["C001", "C002"], 13), ("P2", ["C001"], 8)])
            self.assertEqual(data["people"][0]["clips"]["C001"], [{"start": 3, "end": 6}])
            self.assertEqual(data["unassigned_faces"], 1)
            people.rename(file, "p2", "Ada")
            with self.assertRaises(CutterError):
                people.rename(file, "P1", "ada")                          # the name is taken
            with self.assertRaises(CutterError):
                people.rename(file, "P9", "Bo")
            # a new analysis: another clip with the second person, and someone new who is seen more than anyone
            clips.append({"id": "C004", "duration": 60.0})
            self.clip_file(d, "C004", [(4, range(0, 40)), (1, range(40, 44))])
            again = people.update(clips, d / "image", file)["people"]
            self.assertEqual([(p["id"], p["name"], sorted(p["clips"])) for p in again],
                             [("P1", "", ["C001", "C002"]), ("P2", "Ada", ["C001", "C004"]), ("P3", "", ["C004"])])

    def test_merged_people_stay_one(self):
        from cutter import people
        with TempDir() as d:
            clips = [{"id": "C001", "duration": 20.0}, {"id": "C002", "duration": 20.0}]
            self.clip_file(d, "C001", [(1, range(0, 8))])
            self.clip_file(d, "C002", [(2, range(0, 5))])                 # the same person with a helmet, say
            file = d / "people.json"
            self.assertEqual(len(people.update(clips, d / "image", file)["people"]), 2)
            people.rename(file, "P2", "Ada")
            self.assertEqual(people.merge(file, ["P1", "P2"], dry_run=True)["name"], "Ada")
            self.assertEqual(len(people.load(file)["people"]), 2)         # the dry run wrote nothing
            people.merge(file, ["P1", "P2"])
            one = people.update(clips, d / "image", file)["people"]
            self.assertEqual([(p["id"], p["name"], sorted(p["clips"]), len(p["vectors"])) for p in one],
                             [("P1", "Ada", ["C001", "C002"], 2)])

    def test_a_mixed_person_can_be_split(self):
        import numpy as np
        from cutter import people
        from cutter.util import CutterError
        with TempDir() as d:
            clips = [{"id": f"C00{k}", "duration": 20.0} for k in (1, 2, 3, 4)]
            for cid, seed, secs in (("C001", 1, range(0, 9)), ("C002", 1, range(0, 6)), ("C003", 2, range(0, 7)),
                                    ("C004", 3, range(0, 5))):
                for g in self.clip_file(d, cid, [(seed, secs)]):
                    g["thumb"] = f"faces/{cid}_0.jpg"
                    (d / "image" / f"{cid}.json").write_text(json.dumps({"id": cid, "faces": [g]}))
            file = d / "people.json"
            people.update(clips, d / "image", file)
            people.merge(file, ["P1", "P2"])                              # the mistake: two people under one ID
            mixed = people.update(clips, d / "image", file)["people"]
            self.assertEqual([(p["id"], sorted(p["clips"])) for p in mixed],
                             [("P1", ["C001", "C002", "C003"]), ("P3", ["C004"])])
            for which, to in ((["C009"], None), (["C001", "C002", "C003"], None), (["C003"], "P1"), (["C003"], "P8")):
                with self.assertRaises(CutterError):
                    people.split(file, "P1", which, clips, d / "image", to=to)
            old, new, moved = people.split(file, "p1", ["c003_0"], clips, d / "image", dry_run=True)
            self.assertEqual((old["id"], new["id"], moved), ("P1", "P4", ["C003_0"]))
            self.assertEqual(len(people.load(file)["people"]), 2)         # the dry run wrote nothing
            people.split(file, "P1", ["C003"], clips, d / "image")
            for _ in range(2):                                            # it stays that way in every later analysis
                apart = people.update(clips, d / "image", file)["people"]
                self.assertEqual([(p["id"], sorted(p["clips"]), p["seconds"]) for p in apart],
                                 [("P1", ["C001", "C002"], 15), ("P3", ["C004"], 5), ("P4", ["C003"], 7)])
            # a face much closer to the others of P1 than to its own vector still stays where the split put it
            data = people.load(file)
            near = people.unit(np.sum(data["people"][0]["vectors"], axis=0))
            faces = json.loads((d / "image" / "C003.json").read_text())["faces"]
            faces[0]["vector"] = [round(float(x), 4) for x in near]
            (d / "image" / "C003.json").write_text(json.dumps({"id": "C003", "faces": faces}))
            data["people"][2]["vectors"] = [faces[0]["vector"]]
            data["people"][0]["vectors"] = data["people"][0]["vectors"][:2]
            file.write_text(json.dumps(data))
            self.assertEqual(sorted(people.update(clips, d / "image", file)["people"][2]["clips"]), ["C003"])
            # --to: the faces are someone who has an ID already
            people.split(file, "P1", ["C002"], clips, d / "image", to="P3")
            moved = people.update(clips, d / "image", file)["people"]
            self.assertEqual([(p["id"], sorted(p["clips"])) for p in moved],
                             [("P1", ["C001"]), ("P3", ["C002", "C004"]), ("P4", ["C003"])])

    def test_people_command(self):
        from helpers import run_cutter
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            (d / "analysis").mkdir()
            a = d / "analysis"
            self.clip_file(a, "C001", [(1, range(0, 8)), (2, range(3, 6))])
            (a / "clips.json").write_text(json.dumps([{"id": "C001", "duration": 20.0}]))
            self.assertEqual(json.loads(run_cutter("people", "list", "--json", cwd=d).stdout)["people"], [])
            from cutter import people
            people.update([{"id": "C001", "duration": 20.0}], a / "image", a / "people.json")
            r = json.loads(run_cutter("people", "name", "P1", "Ada", "--dry-run", "--json", cwd=d).stdout)
            self.assertTrue(r["dry_run"])
            self.assertEqual(people.load(a / "people.json")["people"][0]["name"], "")
            run_cutter("people", "name", "P1", "Ada", cwd=d)
            r = json.loads(run_cutter("people", "merge", "P1", "P2", "--json", cwd=d).stdout)
            self.assertEqual((r["id"], r["name"]), ("P1", "Ada"))
            rows = json.loads(run_cutter("people", "list", "--json", cwd=d).stdout)["people"]
            self.assertEqual([(p["id"], p["name"], p["seconds"], p["clips"]) for p in rows], [("P1", "Ada", 8, ["C001"])])
            self.assertEqual(rows[0]["pictures"], [])
            r = run_cutter("people", "split", "P1", "C001", "--json", cwd=d, check=False)
            self.assertEqual((r.returncode, json.loads(r.stdout)["code"]), (2, 2))    # nothing would be left
            r = run_cutter("people", "name", "P7", "Bo", "--json", cwd=d, check=False)
            self.assertEqual((r.returncode, json.loads(r.stdout)["code"]), (2, 2))

    def test_quality_text_says_who(self):
        from cutter import image_analysis
        with TempDir() as d:
            (d / "image").mkdir()
            clip = {"id": "C001", "duration": 10.0, "created": "2024-05-01 09:00:00", "rel_path": "a.mp4"}
            data = {"usable": [{"start": 0, "end": 10, "camera": "static", "steady": True}], "flaws": [],
                    "camera": [{"start": 0, "end": 10, "move": "static", "speed": 0, "shake": 0}], "per_second": [],
                    "summary": {"usable_s": 10.0}}
            (d / "image" / "C001.json").write_text(json.dumps(data))
            known = [{"id": "P1", "name": "Ada", "seconds": 4, "clips": {"C001": [{"start": 2, "end": 6}]}},
                     {"id": "P2", "name": "", "seconds": 9, "clips": {"C007": [{"start": 0, "end": 9}]}}]
            text = image_analysis.readable([clip], d / "image", known)
            self.assertIn("who      P1 Ada 0:02.0-0:06.0", text)
            self.assertIn("P2 (9 s in 1 clips)", text)


@unittest.skipUnless(OPENCV, "needs OpenCV, numpy and ffmpeg (they are in the Docker image)")
class WholeAnalysis(unittest.TestCase):
    """A clip made from one still picture: 3 s static, a 4 s pan to the right, static again, blurred from 9 to 11 s;
    the sound is a tone on two identical channels."""

    @classmethod
    def setUpClass(cls):
        from cutter import image_analysis
        cls.tmp = TempDir()
        d = cls.tmp.__enter__()
        still, clip = d / "still.png", d / "2024-05-01 09-00-00 pan.mp4"
        ff = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
        # a picture with texture at every scale, as real pictures have: stripes and flat fields hide motion along
        # them, and fine noise alone cannot be followed from one sample to the next
        import cv2
        import numpy as np
        rnd = np.random.default_rng(2)
        picture = sum(cv2.resize(rnd.random((h, w)).astype(np.float32), (1600, 360), interpolation=cv2.INTER_CUBIC) * weight
                      for (h, w), weight in (((5, 20), 0.5), ((18, 80), 0.3), ((90, 400), 0.2)))
        cv2.imwrite(str(still), np.clip(picture * 255, 0, 255).astype(np.uint8))
        subprocess.run(ff + ["-loop", "1", "-framerate", "25", "-i", str(still), "-f", "lavfi", "-i",
                             "sine=frequency=440:sample_rate=48000", "-t", "13", "-vf",
                             "crop=640:360:x='if(lt(t,3),0,min((t-3)*60,240))':y=0,boxblur=luma_radius=8:enable='between(t,9,11)'",
                             "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-g", "25",
                             "-c:a", "aac", "-ac", "2", "-shortest", str(clip)], check=True)
        clips = [{"id": "C001", "file": clip.name, "rel_path": clip.name, "path": str(clip), "duration": 13.0, "fps": 25.0,
                  "width": 640, "height": 360, "has_video": True, "has_audio": True, "audio_channels": 2,
                  "created": "2024-05-01 09:00:00"}]
        (d / "clips.json").write_text(json.dumps(clips))
        cls.d = d
        cls.result = image_analysis.run(d / "clips.json", d / "image", text_path=d / "quality.txt")
        cls.data = json.loads((d / "image" / "C001.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def test_the_pan_is_found(self):
        moves = [(c["move"], c["start"], c["end"]) for c in self.data["camera"]]
        self.assertEqual([m[0] for m in moves], ["static", "pan_right", "static"])
        self.assertAlmostEqual(moves[1][1], 3.0, delta=0.7)
        self.assertAlmostEqual(moves[1][2], 7.0, delta=0.7)
        self.assertAlmostEqual(self.data["camera"][1]["speed"], 60 / 640 * 100, delta=2.5)      # 60 px/s of 640 px
        per = self.data["per_second"]
        self.assertLess(per[4]["pan_x"], -7)                               # the picture moves to the left
        self.assertLess(per[1]["pan"], 1.0)

    def test_the_blur_is_a_flaw_and_the_rest_is_usable(self):
        soft = [f for f in self.data["flaws"] if "soft" in f["why"] and f["end"] > 9.5]
        self.assertEqual(len(soft), 1)
        self.assertAlmostEqual(soft[0]["start"], 9.0, delta=0.5)
        self.assertAlmostEqual(soft[0]["end"], 11.0, delta=0.5)
        first, last = self.data["usable"][0], self.data["usable"][-1]
        # the first range holds 5 s without a move and the pan of 4 s, found to half a second: either may prevail
        self.assertEqual(first["start"], 0.0)
        self.assertIn((first["camera"], first["steady"]), (("static", True), ("pan_right", False)))
        self.assertEqual((last["camera"], last["steady"]), ("static", True))
        self.assertGreaterEqual(self.data["summary"]["usable_s"], 9.0)
        self.assertLessEqual(self.data["summary"]["usable_s"], 11.2)

    def test_sound_and_file_format(self):
        d = self.data
        self.assertEqual(d["version"], 3)
        self.assertEqual(d["audio"]["channels"], 1)                        # the two channels are identical
        self.assertEqual(d["audio"]["rumble"], [])
        self.assertAlmostEqual(d["per_second"][2]["level"], -21, delta=6)
        self.assertEqual(len(d["signature"]), 13)
        self.assertEqual(self.result["analyzed"], 1)
        self.assertFalse(self.result["faces"])                             # no model download in tests
        text = (self.d / "quality.txt").read_text()
        self.assertIn("=== C001", text)
        self.assertIn("pan right", text)
        self.assertIn("soft", text)

    def test_a_second_run_measures_nothing_again(self):
        from cutter import image_analysis
        self.assertEqual(image_analysis.run(self.d / "clips.json", self.d / "image")["analyzed"], 0)
        old = dict(self.data, version=2)
        (self.d / "image" / "C001.json").write_text(json.dumps(old))
        self.assertEqual(image_analysis.run(self.d / "clips.json", self.d / "image")["analyzed"], 1)   # an old file is redone


if __name__ == "__main__":
    unittest.main()
