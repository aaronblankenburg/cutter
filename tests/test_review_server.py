"""The review app server: Basic Auth is mandatory, the API stores comments, videos are served with HTTP ranges.

The server runs on 127.0.0.1 (ports 8290-8299) with a password and an htpasswd file created at run time.
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import GOLDEN, SRC, TempDir, basic_auth, free_port, make_htpasswd, run_cutter, wait_for_port

SERVER = SRC / "cutter" / "review_app" / "server.py"


def start(data, htpasswd, port):
    proc = subprocess.Popen([sys.executable, str(SERVER), "--host", "127.0.0.1", "--port", str(port), "--data", str(data),
                             "--htpasswd", str(htpasswd)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if not wait_for_port(port):
        proc.kill()
        raise RuntimeError("review server did not start: " + proc.stdout.read().decode(errors="replace"))
    return proc


def make_cut(data, cut="cut1", size=300_000):
    folder = data / "cuts" / cut
    folder.mkdir(parents=True, exist_ok=True)
    tl = json.loads((GOLDEN / "demo" / "expected" / "timeline.json").read_text())
    tl["version"] = cut
    (folder / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
    (folder / "review.mp4").write_bytes(bytes(range(256)) * (size // 256))
    return folder


class ReviewServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TempDir()
        d = cls.tmp.__enter__()
        cls.data = d / "data"
        make_cut(cls.data)
        alt = make_cut(cls.data, "cut2")
        tl = json.loads((alt / "timeline.json").read_text())
        tl["alternatives"] = [{"id": "a4", "nr": 4, "t": 10.0, "t_to": 18.0, "reason": "two takes of the view",
                               "options": [{"clip": "C002", "in": 1.0, "out": 9.0, "why": "steadier",
                                            "file": "alt-a4-1.mp4", "poster": "alt-a4-1.jpg"}]}]
        (alt / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
        cls.password, cls.htpasswd = make_htpasswd(d, "tester")
        cls.port = free_port()
        cls.proc = start(cls.data, cls.htpasswd, cls.port)
        cls.auth = {"Authorization": basic_auth("tester", cls.password)}

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.proc.stdout.close()
        cls.tmp.__exit__(None, None, None)

    def req(self, method, path, body=None, headers=None, auth=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(self.auth if auth else {}, **(headers or {}))
        if body is not None:
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_login_is_required(self):
        r, _ = self.req("GET", "/", auth=False)
        self.assertEqual(r.status, 401)
        self.assertIn("Basic", r.getheader("WWW-Authenticate"))
        r, _ = self.req("GET", "/api/cuts", headers={"Authorization": basic_auth("tester", "wrong-password")}, auth=False)
        self.assertEqual(r.status, 401)
        r, _ = self.req("GET", "/media/cut1/review.mp4", auth=False)
        self.assertEqual(r.status, 401)

    def test_page_and_security_headers(self):
        r, body = self.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"i18n.js", body)
        self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")

    def test_range_requests(self):
        r, body = self.req("GET", "/media/cut1/review.mp4", headers={"Range": "bytes=1000-1999"})
        self.assertEqual(r.status, 206)
        self.assertEqual(len(body), 1000)
        self.assertTrue(r.getheader("Content-Range").startswith("bytes 1000-1999/"))
        self.assertEqual(body[:4], bytes([1000 % 256, 1001 % 256, 1002 % 256, 1003 % 256]))
        r, _ = self.req("GET", "/media/cut1/review.mp4", headers={"Range": "bytes=99999999-"})
        self.assertEqual(r.status, 416)
        r, body = self.req("GET", "/media/cut1/review.mp4")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Accept-Ranges"), "bytes")

    def test_no_path_traversal(self):
        for path in ("/media/cut1/../../htpasswd", "/media/..%2f..%2fhtpasswd/x", "/static/../server.py"):
            r, _ = self.req("GET", path)
            self.assertIn(r.status, (400, 403, 404), path)

    def test_comment_reply_and_export(self):
        r, body = self.req("GET", "/api/cuts")
        cuts = json.loads(body)
        self.assertEqual(cuts["cuts"][0]["id"], "cut1")
        self.assertIn("shorter", cuts["actions"])
        r, body = self.req("POST", "/api/entries", {"cut": "cut1", "t": 11.0, "action": "shorter", "text": "Too long here",
                                                     "person": "Reviewer A"})
        self.assertEqual(r.status, 201, body)
        e = json.loads(body)["entry"]
        self.assertEqual(e["nr"], 4, "11 s is inside shot 4 of the demo")
        self.assertEqual(e["status"], "open")
        r, _ = self.req("PATCH", f"/api/entries/{e['id']}", {"text": "changed", "person": "Reviewer B"})
        self.assertEqual(r.status, 403, "only the author may change an entry")
        r, body = self.req("POST", "/api/status", {"ids": [e["id"]], "status": "done", "reply": "Shortened by 1 s."})
        self.assertEqual(json.loads(body)["changed"], 1)
        r, body = self.req("GET", "/api/export?cut=cut1")
        x = json.loads(body)
        self.assertEqual(x["counts"]["done"], 1)
        self.assertEqual(x["entries"][0]["reply"], "Shortened by 1 s.")
        r, body = self.req("GET", "/api/export?cut=cut1&format=md")
        self.assertIn("Too long here", body.decode())

    def test_sending_the_feedback_off_makes_one_request(self):
        r, body = self.req("GET", "/api/entries?cut=cut1")
        self.assertIsNone(json.loads(body)["request"])
        r, body = self.req("POST", "/api/submit", {"cut": "cut9", "person": "Reviewer A"})
        self.assertEqual(r.status, 404, "only for a cut that exists")
        r, body = self.req("POST", "/api/submit", {"cut": "cut1"})
        self.assertEqual(r.status, 400, "a name is needed")
        r, body = self.req("POST", "/api/submit", {"cut": "cut1", "person": "Reviewer A"})
        self.assertEqual(r.status, 201, body)
        first = json.loads(body)["request"]
        self.assertEqual((first["cut"], first["person"], first["taken"]), ("cut1", "Reviewer A", None))
        r, body = self.req("POST", "/api/submit", {"cut": "cut1", "person": "Reviewer B"})
        self.assertEqual(r.status, 200, "a request that still waits is not doubled")
        self.assertEqual(json.loads(body)["request"]["id"], first["id"])
        r, body = self.req("GET", "/api/entries?cut=cut1")
        d = json.loads(body)
        self.assertEqual(d["request"]["id"], first["id"])
        r, body = self.req("GET", f"/api/entries?cut=cut1&rev={d['rev']}")
        d = json.loads(body)
        self.assertTrue(d["unchanged"])
        self.assertEqual(d["request"]["id"], first["id"], "the request is reported even when no comment changed")


    def test_general_comment_has_no_moment_and_comes_first(self):
        r, body = self.req("POST", "/api/entries", {"cut": "cut2", "t": 3.0, "text": "here", "person": "Reviewer G"})
        self.assertEqual(r.status, 201, body)
        r, body = self.req("POST", "/api/entries", {"cut": "cut2", "t": None, "t_to": 5.0, "text": "Much too long overall",
                                                     "person": "Reviewer G"})
        self.assertEqual(r.status, 201, body)
        e = json.loads(body)["entry"]
        self.assertEqual((e["t"], e["t_to"], e["nr"], e["clip"]), (None, None, None, None))
        r, body = self.req("GET", "/api/entries?cut=cut2")
        self.assertIsNone(json.loads(body)["entries"][0]["t"], "general entries come first")
        r, body = self.req("PATCH", f"/api/entries/{e['id']}", {"t": 11.0, "person": "Reviewer G"})
        self.assertEqual(json.loads(body)["entry"]["nr"], 4, "a general entry can be given a moment")
        r, body = self.req("PATCH", f"/api/entries/{e['id']}", {"t": None, "person": "Reviewer G"})
        self.assertIsNone(json.loads(body)["entry"]["nr"])
        r, body = self.req("GET", "/api/export?cut=cut2&format=md")
        md = body.decode()
        self.assertIn("## Whole film", md)
        self.assertLess(md.index("Much too long overall"), md.index("here"))

    def test_choosing_an_alternative_once_per_place(self):
        base = {"cut": "cut2", "t": 10.0, "t_to": 18.0, "person": "Reviewer H"}
        r, body = self.req("POST", "/api/entries", dict(base, alternative="a4/2", text="x"))
        self.assertEqual(r.status, 400, "there is only one option")
        r, body = self.req("POST", "/api/entries", dict(base, alternative="zz/1", text="x"))
        self.assertEqual(r.status, 400, "unknown place")
        r, body = self.req("POST", "/api/entries", dict(base, alternative="a4/1", action="other_shot", text="Take C002"))
        self.assertEqual(r.status, 201, body)
        first = json.loads(body)["entry"]
        self.assertEqual(first["alternative"], "a4/1")
        r, body = self.req("POST", "/api/entries", dict(base, alternative="a4/keep", text="Keep it"))
        self.assertEqual(r.status, 200, "choosing again changes the earlier choice")
        e = json.loads(body)["entry"]
        self.assertEqual((e["id"], e["alternative"], e["text"], e["action"]), (first["id"], "a4/keep", "Keep it", None), "the new choice replaces the old one")
        r, body = self.req("POST", "/api/entries", dict(base, person="Reviewer I", alternative="a4/1", text="C002"))
        self.assertEqual(r.status, 201, "another reviewer chooses on their own")
        r, body = self.req("GET", "/api/export?cut=cut2")
        self.assertIn("a4/keep", [x["alternative"] for x in json.loads(body)["entries"]])
        r, body = self.req("GET", "/api/export?cut=cut2&format=md")
        self.assertIn("alternative a4/keep", body.decode())

    def test_app_config_for_an_embedding_app(self):
        project = self.data / "project.json"
        project.write_text(json.dumps({"title": "Demo", "person": "  Host   User ", "submit": False, "embedded": True}),
                           encoding="utf-8")
        try:
            r, body = self.req("GET", "/api/cuts")
            app = json.loads(body)["app"]
            self.assertEqual((app["person"], app["submit"], app["embedded"]), ("Host User", False, True))
            r, _ = self.req("GET", "/")
            self.assertEqual(r.getheader("X-Frame-Options"), "SAMEORIGIN", "embedded: frames of the same origin")
            self.assertIn("frame-ancestors 'self'", r.getheader("Content-Security-Policy"))
        finally:
            project.unlink()
        r, body = self.req("GET", "/api/cuts")
        self.assertNotIn("embedded", json.loads(body)["app"])


class Migration(unittest.TestCase):
    def test_older_database_gets_general_entries_and_alternatives(self):
        import sqlite3
        sys.path.insert(0, str(SERVER.parent))
        import server
        with TempDir() as d:
            db = sqlite3.connect(d / "feedback.sqlite")
            old = server.SCHEMA.replace("    t REAL,", "    t REAL NOT NULL,").replace(",\n    alternative TEXT", "")
            db.executescript(old)
            db.execute("INSERT INTO entries (cut, t, text, person, created) VALUES ('cut1', 2.5, 'old', 'A', 'now')")
            db.commit()
            db.close()
            store = server.Store(str(d / "feedback.sqlite"))
            store.new({"cut": "cut1", "t": None, "text": "general", "person": "A", "alternative": None})
            self.assertEqual([(e["t"], e["text"]) for e in store.list("cut1")], [(None, "general"), (2.5, "old")])
            store.db.close()


class PasswordRequired(unittest.TestCase):
    def test_server_without_users_answers_503(self):
        with TempDir() as d:
            make_cut(d / "data")
            (d / "htpasswd").write_text("", encoding="utf-8")
            port = free_port()
            proc = start(d / "data", d / "htpasswd", port)
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                c.request("GET", "/")
                r = c.getresponse()
                self.assertEqual(r.status, 503)
                self.assertIn(b"cutter review password", r.read())
                c.close()
            finally:
                proc.terminate()
                proc.wait(timeout=10)
                proc.stdout.close()

    def test_cutter_serve_refuses_to_start_without_a_password(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            r = run_cutter("serve", "--bind", "127.0.0.1", "--port", str(free_port()), cwd=d, check=False)
            self.assertEqual(r.returncode, 3)
            self.assertIn("cutter review password", r.stderr)

    def test_review_password_stores_only_a_hash(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            secret = "correct horse battery"
            r = run_cutter("review", "password", "alice", cwd=d, input=f"{secret}\n{secret}\n")
            text = (d / ".cutter" / "review" / "htpasswd").read_text()
            self.assertTrue(text.startswith("alice:scrypt$"))
            self.assertNotIn(secret, text)
            self.assertEqual(oct((d / ".cutter" / "review" / "htpasswd").stat().st_mode & 0o777), "0o600")
            r = run_cutter("serve", "--dry-run", "--json", cwd=d)
            self.assertEqual(json.loads(r.stdout)["bind"], "0.0.0.0")

    def test_review_password_generate_needs_no_terminal(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            f = d / ".cutter" / "review" / "htpasswd"
            r = run_cutter("review", "password", "alice", "--generate", "--dry-run", "--json", cwd=d, input="")
            self.assertFalse(json.loads(r.stdout)["exists"])
            self.assertFalse(f.exists())
            r = run_cutter("review", "password", "alice", "--generate", "--json", cwd=d, input="")
            first = json.loads(r.stdout)
            self.assertRegex(first["password"], r"^[a-z2-9]{4}(-[a-z2-9]{4}){3}$")
            self.assertFalse(first["replaced"])
            self.assertNotIn(first["password"], f.read_text())
            self.assertNotIn(first["password"], r.stderr)
            self.assertEqual(oct(f.stat().st_mode & 0o777), "0o600")
            sys.path.insert(0, str(SERVER.parent))
            import server
            stored = dict(z.split(":", 1) for z in f.read_text().splitlines())
            self.assertTrue(server.password_ok(first["password"], stored["alice"]))
            r = run_cutter("review", "password", "alice", "--generate", cwd=d, input="")      # for people: shown once
            self.assertIn("password: ", r.stdout)
            self.assertEqual(f.read_text().count("alice:"), 1)
            run_cutter("review", "password", "bob", "--generate", "--json", cwd=d, input="")
            r = run_cutter("review", "password", "alice", "--delete", "--json", cwd=d, input="")
            self.assertTrue(json.loads(r.stdout)["deleted"])
            self.assertTrue(f.read_text().startswith("bob:scrypt$"))

    def test_review_password_without_a_terminal_says_what_to_do(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            r = run_cutter("review", "password", "alice", "--json", cwd=d, input="", check=False)
            self.assertEqual(r.returncode, 2)
            self.assertIn("--generate", json.loads(r.stdout)["hint"])
            self.assertNotIn("Traceback", r.stderr)
            self.assertFalse((d / ".cutter" / "review" / "htpasswd").exists())
            for typed in ("too short\ntoo short\n", "correct horse battery\ncorrect horse staple\n"):
                r = run_cutter("review", "password", "alice", cwd=d, input=typed, check=False)
                self.assertEqual(r.returncode, 2)
                self.assertNotIn("Traceback", r.stderr)
            r = run_cutter("review", "password", "a b", "--generate", cwd=d, input="", check=False)
            self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
