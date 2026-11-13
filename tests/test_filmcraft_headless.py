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


@unittest.skipUnless(CLI and os.access(CLI, os.X_OK), "filmcraft-cli not available (set FILMCRAFT_CLI)")
@unittest.skipUnless(TOOLS and FFMPEG, "needs numpy, OpenTimelineIO and ffmpeg")
class FilmCraftDraft(unittest.TestCase):
    """A draft from proxies: FilmCraft builds it like the film from the footage and shows the same picture."""

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
        proxies.make(list(clips.values()), p.path("proxies"), height=360)
        os.environ["FILMCRAFT_CLI"] = CLI
        golden_demo.cli("retime", "cut1", "--project", cls.film)
        cls.project = cls.film / "cuts" / "cut1" / "cut1.fcproj"
        cls.frames, cls.builds = {}, {}
        for kind, option in (("footage", []), ("proxies", ["--proxies"])):
            golden_demo.cli("timeline", "cut1", *option, "--project", cls.film)
            cls.builds[kind] = golden_demo.cli("filmcraft", "build", "cut1", "--overwrite", "--project", cls.film)
            for t in (2.0, 11.5, 20.0):
                out = cls.film / f"{kind}_{t}.png"
                r = subprocess.run([CLI, "--compact", "--data-dir", str(cls.film / ".cutter" / "filmcraft-data"), "--project",
                                    str(cls.project), "render", "--seconds", str(t), "--out", str(out)], capture_output=True,
                                   text=True, env={k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")})
                assert r.returncode == 0, r.stdout + r.stderr
                cls.frames[kind, t] = out

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def test_the_draft_builds_like_the_film(self):
        a, b = self.builds["footage"], self.builds["proxies"]
        self.assertEqual((a["media"], b["media"]), ("footage", "proxies"))
        self.assertEqual((b["commands"], b["dialogue"]), (a["commands"], a["dialogue"]))
        self.assertIn("offline: []", " ".join(b["log"]))

    def test_the_draft_shows_the_same_picture(self):
        for t in (2.0, 11.5, 20.0):
            with self.subTest(seconds=t):
                r = subprocess.run(["ffmpeg", "-nostdin", "-v", "info", "-i", str(self.frames["footage", t]), "-i",
                                    str(self.frames["proxies", t]), "-lavfi", "[0:v]scale=320:180[a];[1:v]scale=320:180[b];[a][b]ssim",
                                    "-f", "null", "-"],
                                   capture_output=True, text=True)
                ssim = float(r.stderr.rsplit("All:", 1)[1].split()[0])     # small: the proxies have 360 lines
                self.assertGreater(ssim, 0.85, f"draft and film differ at {t} s")


if __name__ == "__main__":
    unittest.main()
