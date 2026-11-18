"""Review renders keep the size of a small export ([filmcraft] export_height, 360p stand-ins) instead of scaling up."""
import pathlib
import subprocess
import tempfile
import unittest

from helpers import FFMPEG
from cutter import review_data
from cutter.cli import export_scale


def clip(path, size, fps, seconds=2):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps}:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)


class Sizes(unittest.TestCase):
    def test_frame_size(self):
        self.assertEqual(review_data.frame_size(None), (1280, 720))
        self.assertEqual(review_data.frame_size(1080), (1280, 720))
        self.assertEqual(review_data.frame_size(360), (640, 360))
        self.assertEqual(review_data.frame_size(240), (426, 240))

    def test_export_scale(self):
        self.assertEqual(export_scale(0), [])
        self.assertEqual(export_scale(None), [])
        self.assertEqual(export_scale(1080), [])
        self.assertEqual(export_scale(360), ["--scale", "0.333333"])
        self.assertEqual(export_scale("720"), ["--scale", "0.666667"])


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class ExportVideo(unittest.TestCase):
    def take_over(self, size, fps):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            src, out = d / "export.mp4", d / "review.mp4"
            clip(src, size, fps)
            review_data.export_video(src, {"duration": 2.0, "shots": []}, out, d, False)
            p = review_data.probe(out)
            return p["width"], p["height"], round(p["fps"])

    def test_small_export_is_remuxed_at_its_size(self):
        self.assertEqual(self.take_over("640x360", 25), (640, 360, 25))

    def test_small_export_at_another_rate_keeps_its_size(self):
        self.assertEqual(self.take_over("640x360", 30), (640, 360, 25))

    def test_large_export_becomes_720p(self):
        self.assertEqual(self.take_over("1920x1080", 25), (1280, 720, 25))


if __name__ == "__main__":
    unittest.main()
