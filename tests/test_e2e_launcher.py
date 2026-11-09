"""End to end through the launcher and the Docker image, from an empty folder: demo footage, new, doctor, analyze,
demo cut, music, retime, timeline, FilmCraft, review data, password, serve (401 without, 200 with login).

Opt-in, because it takes minutes:  CUTTER_E2E=1 python -m unittest tests/test_e2e_launcher.py -v
Needs Docker and the image (bin/cutter image pull|build). The Whisper model comes from CUTTER_MODEL_CACHE (an
existing Hugging Face hub cache, mounted read-only); without it, analyze runs with --no-transcripts so that nothing
is downloaded. CUTTER_E2E_EXPORT=1 also exports the film with FilmCraft. CUTTER_DEV=1 runs this checkout's code.
"""
import base64
import json
import os
import secrets
import shutil
import subprocess
import time
import unittest
import urllib.error
import urllib.request

from helpers import LAUNCHER, TempDir, free_port, wait_for_port

E2E = os.environ.get("CUTTER_E2E") == "1"
SH = shutil.which("dash") or "/bin/sh"


@unittest.skipUnless(E2E, "set CUTTER_E2E=1 to run the end-to-end test through Docker")
class EndToEnd(unittest.TestCase):
    def test_from_an_empty_folder_to_the_review_app(self):
        timings = {}

        with TempDir() as d:
            def run(*args, cwd, stdin=None, name=None):
                t0 = time.time()
                r = subprocess.run([SH, str(LAUNCHER), *map(str, args)], cwd=cwd, input=stdin, capture_output=True, text=True,
                                   env=dict(os.environ, CUTTER_YES="0"))
                timings[name or " ".join(map(str, args[:2]))] = round(time.time() - t0, 1)
                self.assertEqual(r.returncode, 0, f"cutter {' '.join(map(str, args))}\n{r.stdout}\n{r.stderr}")
                return r

            run("demo", "media", d / "footage", cwd=d)
            run("new", "film", "--media", "footage", "--title", "E2E", cwd=d)
            film = d / "film"
            r = run("doctor", "--json", cwd=film)
            self.assertTrue(json.loads(r.stdout)["ok"])
            analyze = ["analyze"] if os.environ.get("CUTTER_MODEL_CACHE") else ["analyze", "--no-transcripts"]
            run(*analyze, cwd=film, name="analyze")
            self.assertEqual(len(json.loads((film / "analysis" / "clips.json").read_text())), 5)
            run("demo", "cut", cwd=film)
            run("music", "license", cwd=film)
            run("music", "analyze", cwd=film)
            run("retime", "cut1", cwd=film)
            run("timeline", "cut1", cwd=film)
            otio = (film / "cuts" / "cut1" / "cut1.otio").read_text()
            self.assertIn(f"file://{d / 'footage'}".replace(" ", "%20"), otio, "host paths in the timeline")
            run("filmcraft", "build", "cut1", cwd=film)
            self.assertTrue((film / "cuts" / "cut1" / "cut1.fcproj").exists())
            if os.environ.get("CUTTER_E2E_EXPORT") == "1":
                run("filmcraft", "export", "cut1", cwd=film)
            run("review-data", "cut1", cwd=film)
            password = secrets.token_urlsafe(16)
            run("review", "password", "tester", cwd=film, stdin=f"{password}\n{password}\n")
            port = free_port()
            t0 = time.time()
            proc = subprocess.Popen([SH, str(LAUNCHER), "serve", "--bind", "127.0.0.1", "--port", str(port)], cwd=film,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            try:
                self.assertTrue(wait_for_port(port, 60))
                code = None
                for _ in range(100):                       # the port answers before the app inside is ready
                    code = status(f"http://127.0.0.1:{port}/")
                    if code != 0:
                        break
                    time.sleep(0.2)
                timings["serve (ready)"] = round(time.time() - t0, 1)
                self.assertEqual(code, 401)
                auth = "Basic " + base64.b64encode(f"tester:{password}".encode()).decode()
                self.assertEqual(status(f"http://127.0.0.1:{port}/", auth), 200)
                self.assertEqual(status(f"http://127.0.0.1:{port}/media/cut1/review.mp4", auth, "bytes=0-99"), 206)
            finally:
                subprocess.run(["docker", "stop", f"cutter-review-{port}"], capture_output=True)
                proc.wait(timeout=30)
                proc.stdout.close()
        print("\nTimings (s):", json.dumps(timings))


def status(url, auth=None, rng=None):
    req = urllib.request.Request(url)
    if auth:
        req.add_header("Authorization", auth)
    if rng:
        req.add_header("Range", rng)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, ConnectionError, OSError):
        return 0


if __name__ == "__main__":
    unittest.main()
