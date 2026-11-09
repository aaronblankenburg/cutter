"""FilmCraft headless on the demo: build the project of a cut with the real filmcraft-cli and render frames from it.

Runs where filmcraft-cli is available: in the Docker image, or natively with FILMCRAFT_CLI=/path/to/filmcraft-cli.
Needs ffmpeg for the demo footage. Never touches a running FilmCraft (no --bridge).
"""
import json
import os
import shutil
import subprocess
import unittest

from helpers import FFMPEG, TOOLS, TempDir
import golden_demo

CLI = os.environ.get("FILMCRAFT_CLI") or shutil.which("filmcraft-cli")


@unittest.skipUnless(CLI and os.access(CLI, os.X_OK), "filmcraft-cli not available (set FILMCRAFT_CLI)")
@unittest.skipUnless(TOOLS and FFMPEG, "needs numpy, OpenTimelineIO and ffmpeg")
class FilmCraftHeadless(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cutter import demo
        cls.tmp = TempDir()
        d = cls.tmp.__enter__()
        demo.media(d / "footage")
        cls.film = golden_demo.setup_project(d, media_root=str(d / "footage"))
        golden_demo.cli("retime", "cut1", "--project", cls.film)
        golden_demo.cli("timeline", "cut1", "--project", cls.film)
        os.environ["FILMCRAFT_CLI"] = CLI
        cls.build = golden_demo.cli("filmcraft", "build", "cut1", "--project", cls.film)
        cls.project = cls.film / "cuts" / "cut1" / "cut1.fcproj"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def fc(self, *args):
        return subprocess.run([CLI, "--compact", "--data-dir", str(self.film / ".cutter" / "filmcraft-data"), "--project",
                               str(self.project), *map(str, args)], capture_output=True, text=True,
                              env={k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")})

    def test_build_ran_headless_without_failures(self):
        self.assertEqual(self.build["mode"], "headless")
        self.assertTrue(self.project.exists())
        log = "\n".join(self.build["log"])
        self.assertIn("offline: []", log)
        self.assertRegex(log, r"Batch: \d+ commands, \d+ ok, 0 failed")
        self.assertIn("Title and credits", log)

    def test_project_has_captions_and_the_whole_film(self):
        r = self.fc("inspect", "sequence")
        self.assertEqual(r.returncode, 0, r.stderr)
        seq = json.loads(r.stdout)
        self.assertAlmostEqual(seq["duration"] / 254016000000, 29.0, delta=0.05)
        self.assertGreaterEqual(len(seq["video"]), 3, "picture, cutaways, graphics")
        r = self.fc("exec", "captions.setTrack", "track=C1")       # fails with "no such caption track" if missing
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.fc("exec", "captions.setTrack", "track=C2")
        self.assertNotEqual(r.returncode, 0, "exactly one caption track")

    def test_frames_render_without_offline_media(self):
        for t in (2.0, 11.5, 27.5):
            with self.subTest(seconds=t):
                out = self.film / f"frame_{t}.png"
                r = self.fc("render", "--seconds", t, "--out", out)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertGreater(out.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
