"""feedback fetch/reply against a local review database, and deploy as a dry run (nothing is sent anywhere)."""
import http.client
import json
import unittest

from helpers import TempDir, basic_auth, free_port, make_htpasswd, run_cutter
from test_review_server import make_cut, start


def post_comment(port, password, **entry):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/api/entries", body=json.dumps(entry).encode(),
              headers={"Authorization": basic_auth("tester", password), "Content-Type": "application/json"})
    r = c.getresponse()
    data = json.loads(r.read())
    c.close()
    return data["entry"]


class Feedback(unittest.TestCase):
    def test_fetch_and_reply_in_a_project(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text('title = "Fb"\n', encoding="utf-8")
            data = d / ".cutter" / "review" / "data"
            make_cut(data, "cut1")
            make_cut(data, "cut2")
            password, htpasswd = make_htpasswd(d / ".cutter" / "review", "tester")
            port = free_port()
            proc = start(data, htpasswd, port)
            try:
                e1 = post_comment(port, password, cut="cut2", t=2.0, action="like", text="Nice start", person="Reviewer A")
                e2 = post_comment(port, password, cut="cut2", t=12.0, t_to=14.0, action="question", text="Who speaks?",
                                  person="Reviewer B")
                post_comment(port, password, cut="cut1", t=1.0, text="old cut", person="Reviewer A")
            finally:
                proc.terminate()
                proc.wait(timeout=10)
                proc.stdout.close()
            r = run_cutter("feedback", "fetch", "--json", cwd=d)
            out = json.loads(r.stdout)
            self.assertEqual(out["cut"], "cut2", "the newest cut by default")
            self.assertEqual(out["source"], "local")
            f = d / ".cutter" / "review" / "feedback-cut2.json"
            self.assertEqual(out["file"], str(f))
            x = json.loads(f.read_text())
            self.assertEqual(x["counts"]["total"], 2)
            self.assertEqual({p["name"] for p in x["people"]}, {"Reviewer A", "Reviewer B"})
            replies = d / "replies.json"
            replies.write_text(json.dumps([{"id": e1["id"], "status": "done", "reply": "Thanks!"},
                                           {"id": e2["id"], "status": "done", "reply": "The guide."}]), encoding="utf-8")
            r = run_cutter("feedback", "reply", "cut2", "--file", replies, "--json", cwd=d)
            self.assertEqual(json.loads(r.stdout)["changed"], 2)
            self.assertTrue(list((data / "backups").glob("feedback_before_replies_*.sqlite")))
            x = json.loads(run_cutter("feedback", "fetch", "cut2", "--status", "done", "--json", "--output", d / "done.json",
                                      cwd=d).stdout)
            self.assertEqual(x["counts"]["total"], 2)
            bad = d / "bad.json"
            bad.write_text(json.dumps([{"id": e1["id"], "status": "maybe"}]), encoding="utf-8")
            r = run_cutter("feedback", "reply", "--file", bad, cwd=d, check=False)
            self.assertEqual(r.returncode, 3)

    def test_reply_rejects_entries_of_another_cut(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            data = d / ".cutter" / "review" / "data"
            make_cut(data, "cut1")
            make_cut(data, "cut2")
            password, htpasswd = make_htpasswd(d / ".cutter" / "review", "tester")
            port = free_port()
            proc = start(data, htpasswd, port)
            try:
                e = post_comment(port, password, cut="cut1", t=1.0, text="x", person="Reviewer A")
            finally:
                proc.terminate()
                proc.wait(timeout=10)
                proc.stdout.close()
            f = d / "r.json"
            f.write_text(json.dumps([{"id": e["id"], "status": "done"}]), encoding="utf-8")
            r = run_cutter("feedback", "reply", "cut2", "--file", f, cwd=d, check=False)
            self.assertEqual(r.returncode, 3)
            self.assertIn("from another cut", r.stderr)


class NoServerInTests(unittest.TestCase):
    """Even with a deploy.env that names a server, tests never run ssh or rsync: CUTTER_TEST makes cutter refuse."""

    def test_feedback_and_deploy_refuse_to_connect(self):
        import os
        with TempDir() as d:
            bin_ = d / "bin"
            bin_.mkdir()
            log = d / "calls.txt"
            for prog in ("ssh", "rsync"):
                (bin_ / prog).write_text(f"#!/bin/sh\necho {prog} \"$@\" >> {log}\nexit 0\n", encoding="utf-8")
                (bin_ / prog).chmod(0o755)
            (d / "cutter.toml").write_text("", encoding="utf-8")
            (d / "deploy.env").write_text("TARGET_HOST=tester@server.invalid\nTARGET_DIR=/srv/cutter\n", encoding="utf-8")
            env = {"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"}
            for args in (("feedback", "fetch"), ("feedback", "reply", "--file", d / "r.json"), ("deploy", "app")):
                if args[1] == "reply":
                    (d / "r.json").write_text('[{"id": 1, "status": "done"}]', encoding="utf-8")
                with self.subTest(command=" ".join(map(str, args[:2]))):
                    r = run_cutter(*args, cwd=d, env=env, check=False)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn("Tests never", r.stderr)
            self.assertFalse(log.exists(), "ssh or rsync was called")
            r = run_cutter("deploy", "app", "--dry-run", "--json", cwd=d, env=env)
            self.assertTrue(json.loads(r.stdout)["dry_run"], "dry runs still work")
            self.assertFalse(log.exists())


class Deploy(unittest.TestCase):
    def test_dry_run_lists_the_commands(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text('title = "Dep"\n', encoding="utf-8")
            (d / "deploy.env").write_text("TARGET_HOST=user@review.example.org\nTARGET_DIR=/srv/cutter\nDOMAIN=review.example.org\n",
                                          encoding="utf-8")
            r = run_cutter("deploy", "app", "server", "--dry-run", "--json", cwd=d)
            out = json.loads(r.stdout)
            self.assertTrue(out["dry_run"])
            flat = [" ".join(map(str, c)) for c in out["commands"]]
            self.assertTrue(any(c.startswith("rsync") and "user@review.example.org:/srv/cutter/app/" in c for c in flat))
            self.assertTrue(any("user@review.example.org:/srv/cutter/server/" in c for c in flat))
            r = run_cutter("deploy", "cut1", "--dry-run", cwd=d, check=False)
            self.assertEqual(r.returncode, 3, "no review data for cut1 yet")

    def test_server_templates(self):
        from cutter import deploy
        with TempDir() as d:
            env = {"TARGET_DIR": "/srv/cutter/", "PORT": "8089", "DOMAIN": "review.example.org", "SERVICE": "cutter-review",
                   "SERVICE_USER": "user"}
            names = deploy.fill_templates(env, "My film", d)
            self.assertEqual(set(names), {"install.sh", "nginx-site.conf", "review.service"})
            for n in names:
                text = (d / n).read_text()
                self.assertNotIn("{{", text, n)
            self.assertIn("127.0.0.1:8089", (d / "nginx-site.conf").read_text())

    def test_missing_deploy_env(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            r = run_cutter("deploy", "app", "--dry-run", "--env", d / "nope.env", cwd=d, check=False)
            self.assertEqual(r.returncode, 3)


if __name__ == "__main__":
    unittest.main()
