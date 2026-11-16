"""Stand-ins with sidecars: analysis reads a small copy, clips.json states the original's metadata."""
import json
import pathlib
import subprocess
import unittest

from helpers import FFMPEG, TempDir
from test_footage_quality import gpmf

FORMAT = "cutlery.sidecar/1"


def make(path, size, rate="25", seconds=2, extra=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(seconds), "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "64k", "-shortest", *extra,
                    str(path)], check=True)


def sidecar(name, rel_path, **video):
    v = {"codec": "hevc", "width": 3840, "height": 2160, "rotation": 0, "r_frame_rate": "30000/1001",
         "avg_frame_rate": "30000/1001", "nb_frames": 60, "vfr": False, "bit_depth": 10}
    v.update(video)
    return {"format": FORMAT, "file": name, "rel_path": rel_path, "size": 1_234_567_890, "mtime": "2024-06-01T10:15:00Z",
            "container": "QuickTime", "duration": 2.002, "creation_time": "2024-06-01T08:15:00Z",
            "timecode": "10:00:00;02", "gps": "+48.1374+011.5755+519.000/", "video": v,
            "audio": {"codec": "aac", "channels": 2, "sample_rate": 48000},
            "tags": {"com.apple.quicktime.model": "iPhone", "creation_time": "2024-06-01T08:15:00Z"},
            "telemetry": {"gpmd": False, "srt": None},
            "fingerprint": {"size": 1_234_567_890, "head_sha256": "a" * 64, "tail_sha256": "b" * 64}}


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class StandIns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TempDir()
        cls.d = d = cls.tmp.__enter__()
        cls.media, cls.sidecars = d / "media", d / "sidecars"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def add(self, rel, sc):
        make(self.media / (rel + ".mp4"), "640x360", rate="30000/1001")
        target = self.sidecars / (rel + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(sc), encoding="utf-8")
        return self.media / (rel + ".mp4")

    def test_sidecar_files_of_a_stand_in(self):
        from cutter import standin
        f = standin.sidecar_files(self.media / "Day 1" / "IMG 1.MOV.mp4", self.media, self.sidecars)
        self.assertEqual({k: v.relative_to(self.sidecars).as_posix() for k, v in f.items()},
                         {"json": "Day 1/IMG 1.MOV.json", "gpmd": "Day 1/IMG 1.MOV.gpmd", "srt": "Day 1/IMG 1.MOV.srt"})

    def test_metadata_is_the_originals(self):
        from cutter import analyze
        p = self.add("Day 1/2024-06-01 10-15-00 IMG_1.MOV", sidecar("2024-06-01 10-15-00 IMG_1.MOV", "Day 1/2024-06-01 10-15-00 IMG_1.MOV"))
        c = analyze.probe_standin(p, self.media, self.sidecars)
        self.assertEqual(c["file"], "2024-06-01 10-15-00 IMG_1.MOV")
        self.assertEqual(c["rel_path"], "Day 1/2024-06-01 10-15-00 IMG_1.MOV")
        self.assertEqual((c["codec"], c["width"], c["height"], c["rotation"]), ("hevc", 3840, 2160, 0))
        self.assertEqual((c["fps"], c["fps_raw"], c["vfr"], c["duration"]), (29.97, "30000/1001", False, 2.002))
        self.assertEqual(c["timecode"], "10:00:00;02")
        self.assertEqual(c["created"], "2024-06-01 10:15:00")                     # from the name, as for the original
        self.assertEqual(c["created_source"], "filename")
        self.assertEqual(c["device"], "iPhone")
        self.assertEqual(c["gps"], {"lat": 48.1374, "lon": 11.5755, "alt": 519.0})
        self.assertEqual((c["audio_rate"], c["audio_channels"]), (48000, 2))
        self.assertEqual(c["size_mb"], 1234.6)
        self.assertEqual(c["path"], str(p.resolve()))                              # what is read is the stand-in
        self.assertEqual(c["standin"], {"width": 640, "height": 360})
        self.assertEqual(c["original"]["fingerprint"]["head_sha256"], "a" * 64)
        self.assertEqual(c["original"]["bit_depth"], 10)

    def test_created_from_the_tags_without_a_dated_name(self):
        from cutter import analyze
        p = self.add("tags/IMG_2.MOV", sidecar("IMG_2.MOV", "tags/IMG_2.MOV"))
        c = analyze.probe_standin(p, self.media, self.sidecars)
        self.assertEqual((c["created"], c["created_source"]), ("2024-06-01 08:15:00", "metadata"))

    def test_portrait_rotation_keeps_the_ffprobe_convention(self):
        """The sidecar says 90° clockwise, clips.json says what ffprobe says (270), and width/height are upright."""
        from cutter import analyze
        p = self.add("rot/IMG_3.MOV", sidecar("IMG_3.MOV", "rot/IMG_3.MOV", rotation=90, width=3840, height=2160))
        c = analyze.probe_standin(p, self.media, self.sidecars)
        self.assertEqual((c["rotation"], c["width"], c["height"]), (270, 2160, 3840))

    def test_vfr_and_h264_names(self):
        from cutter import analyze
        p = self.add("vfr/IMG_4.MOV", sidecar("IMG_4.MOV", "vfr/IMG_4.MOV", codec="avc", r_frame_rate="30/1",
                                              avg_frame_rate="450/19", vfr=True))
        c = analyze.probe_standin(p, self.media, self.sidecars)
        self.assertEqual((c["codec"], c["vfr"], c["fps_avg"]), ("h264", True, 23.684))

    def test_missing_or_foreign_sidecar_is_an_error_not_a_guess(self):
        from cutter import analyze
        make(self.media / "none/IMG_5.MOV.mp4", "640x360")
        with self.assertRaisesRegex(RuntimeError, "no sidecar"):
            analyze.probe_standin(self.media / "none/IMG_5.MOV.mp4", self.media, self.sidecars)
        (self.sidecars / "none").mkdir(parents=True, exist_ok=True)
        (self.sidecars / "none/IMG_5.MOV.json").write_text('{"format": "other/1"}')
        with self.assertRaisesRegex(RuntimeError, "not a sidecar"):
            analyze.probe_standin(self.media / "none/IMG_5.MOV.mp4", self.media, self.sidecars)

    def test_unsupported_original_is_refused(self):
        from cutter import analyze
        sc = sidecar("IMG_6.MOV", "un/IMG_6.MOV")
        sc["unsupported"] = {"reason": "video_codec"}
        p = self.add("un/IMG_6.MOV", sc)
        with self.assertRaisesRegex(RuntimeError, "could not be read"):
            analyze.probe_standin(p, self.media, self.sidecars)

    def test_ids_follow_the_fingerprint_not_the_name(self):
        from cutter import analyze
        out = self.d / "analysis"
        out.mkdir(exist_ok=True)
        a = self.add("ids/A.MOV", sidecar("A.MOV", "ids/A.MOV"))
        clips = [analyze.probe_standin(a, self.media, self.sidecars)]
        analyze.assign_ids(clips, out)
        analyze.save_clips(clips, out)
        first = clips[0]["id"]
        # renamed on the user's computer, uploaded again under the new name: same original, same ID
        sc = sidecar("B.MOV", "ids/B.MOV")
        b = self.add("ids/B.MOV", sc)
        again = [analyze.probe_standin(b, self.media, self.sidecars)]
        analyze.assign_ids(again, out)
        self.assertEqual(again[0]["id"], first)
        # a different original gets a new ID
        sc2 = sidecar("C.MOV", "ids/C.MOV")
        sc2["fingerprint"]["head_sha256"] = "c" * 64
        c = self.add("ids/C.MOV", sc2)
        other = [analyze.probe_standin(c, self.media, self.sidecars)]
        analyze.assign_ids(other, out)
        self.assertNotEqual(other[0]["id"], first)

    def test_length_problem_is_reported(self):
        from cutter import standin
        clip = {"duration": 10.0, "fps": 25.0}
        self.assertIsNone(standin.length_problem(clip, {"format": {"duration": "10.05"}}))
        self.assertIn("stand-in is 9.000", standin.length_problem(clip, {"format": {"duration": "9.0"}}))

    def test_stand_ins_never_need_a_proxy(self):
        from cutter import proxies
        original = {"codec": "hevc", "width": 3840, "height": 2160, "rotation": 0, "has_video": True}
        self.assertTrue(proxies.needed(original))
        self.assertFalse(proxies.needed({**original, "standin": {"width": 640, "height": 360}}))

    def test_low_detail_compares_clips_of_the_same_analysed_size(self):
        from cutter import image_analysis
        a = {"width": 3840, "height": 2160, "standin": {"width": 640, "height": 360}}
        b = {"width": 1920, "height": 1080, "standin": {"width": 640, "height": 360}}
        c = {"width": 1920, "height": 1080}
        self.assertEqual(image_analysis.analysed_size(a), image_analysis.analysed_size(b))
        self.assertEqual(image_analysis.analysed_size(c), (1920, 1080))

    def test_telemetry_from_the_raw_track_of_the_original(self):
        from cutter import telemetry
        make(self.media / "gp/GOPR1.MP4.mp4", "640x360")
        raw = self.d / "GOPR1.gpmd"
        raw.write_bytes(gpmf(4))
        r = telemetry.read(self.media / "gp/GOPR1.MP4.mp4", 4.0, files={"gpmd": str(raw)})
        self.assertEqual(r["source"], "gopro")
        self.assertTrue(r["track"])
        srt = self.d / "DJI.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n[latitude: 52.1] [longitude: 13.2] [rel_alt: 10.0 abs_alt: 90.0]\n\n")
        r = telemetry.read(self.media / "gp/GOPR1.MP4.mp4", 1.0, files={"srt": str(srt)})
        self.assertEqual(r["source"], "dji")

    def test_telemetry_files_are_found_next_to_the_sidecar(self):
        from cutter import analyze
        p = self.add("tel/GOPR2.MP4", sidecar("GOPR2.MP4", "tel/GOPR2.MP4"))
        (self.sidecars / "tel/GOPR2.MP4.gpmd").write_bytes(gpmf(2))
        c = analyze.probe_standin(p, self.media, self.sidecars)
        self.assertEqual(list(c["telemetry_files"]), ["gpmd"])
        self.assertTrue(pathlib.Path(c["telemetry_files"]["gpmd"]).is_file())


if __name__ == "__main__":
    unittest.main()
