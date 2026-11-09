"""Music: license names and verdicts, the license check on file tags, and beat analysis of the demo track."""
import json
import subprocess
import unittest

from helpers import AUDIO, FFMPEG, TempDir, run_cutter
from cutter import music


class Licenses(unittest.TestCase):
    def test_canonical_names(self):
        cases = {"CC BY 4.0 https://creativecommons.org/licenses/by/4.0/": "CC BY",
                 "https://creativecommons.org/licenses/by-nc-sa/4.0/": "CC BY-NC-SA",
                 "Attribution-NonCommercial 3.0": "CC BY-NC",
                 "CC0 1.0": "CC0", "cc-by-nd": "CC BY-ND", "All rights reserved": None, "": None}
        for text, name in cases.items():
            with self.subTest(text=text):
                self.assertEqual(music.canonical(text), name)

    def test_defaults_allow_only_cc0_and_cc_by(self):
        allowed = ["CC0", "CC BY"]
        self.assertEqual(music.verdict("CC0", allowed)[0], "allowed")
        self.assertEqual(music.verdict("CC BY", allowed)[0], "allowed")
        self.assertEqual(music.verdict("CC BY-NC", allowed)[0], "not_allowed")
        self.assertEqual(music.verdict(None, allowed)[0], "unknown")

    def test_nc_is_opt_in(self):
        self.assertEqual(music.verdict("CC BY-NC-SA", ["CC0", "CC BY", "CC BY-NC-SA"])[0], "allowed")

    def test_traps_stay_forbidden_even_when_listed(self):
        for lic in ("CC BY-ND", "CC BY-NC-ND", "CC BY-SA"):
            with self.subTest(lic=lic):
                self.assertEqual(music.verdict(lic, ["CC0", "CC BY", lic])[0], "forbidden")


def tone(path, copyright=None):
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    if copyright:
        cmd += ["-metadata", f"copyright={copyright}"]
    subprocess.run(cmd + [str(path)], check=True)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class LicenseCheck(unittest.TestCase):
    def test_check_reads_tags_and_exits_with_4_on_problems(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            (d / "music").mkdir()
            tone(d / "music" / "ok.wav", "CC BY 4.0 https://creativecommons.org/licenses/by/4.0/")
            r = run_cutter("music", "license", "--json", cwd=d)
            self.assertEqual(json.loads(r.stdout)["items"][0]["verdict"], "allowed")
            tone(d / "music" / "nd.wav", "CC BY-ND 4.0")
            tone(d / "music" / "none.wav")
            r = run_cutter("music", "license", "--json", cwd=d, check=False)
            self.assertEqual(r.returncode, 4)
            verdicts = {i["source"].rsplit("/", 1)[-1]: i["verdict"] for i in json.loads(r.stdout)["items"]}
            self.assertEqual(verdicts, {"nd.wav": "forbidden", "none.wav": "unknown", "ok.wav": "allowed"})


@unittest.skipUnless(AUDIO and FFMPEG, "needs librosa and ffmpeg")
class BeatAnalysis(unittest.TestCase):
    def test_demo_track_tempo_and_downbeat(self):
        from cutter import demo
        with TempDir() as d:
            info = demo.click_track(d / "click.wav")
            r = music.analyze_file(d / "click.wav")
        self.assertAlmostEqual(r["bpm"], 120.0, delta=0.5)
        self.assertEqual(r["phase"], 0, "the first beat of the demo track is a downbeat")
        self.assertAlmostEqual(r["downbeats"][0], info["first_beat"], delta=0.06)
        gaps = [b - a for a, b in zip(r["beats"], r["beats"][1:])]
        self.assertAlmostEqual(sum(gaps) / len(gaps), 0.5, delta=0.01)
        self.assertGreater(len(r["beats"]), 70)
        self.assertLess(r["lufs"], -10)


if __name__ == "__main__":
    unittest.main()
