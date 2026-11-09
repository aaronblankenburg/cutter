"""The command line: help for every command, JSON results and errors, new, plan, doctor and dry runs."""
import json
import unittest

from helpers import TempDir, run_cutter
from cutter import cli, demo


def commands():
    ap = cli.parser()
    sub = next(a for a in ap._actions if a.dest == "command")
    for name, p in sub.choices.items():
        nested = [a for a in p._actions if a.__class__.__name__ == "_SubParsersAction"]
        if nested:
            for child in nested[0].choices:
                yield [name, child]
        else:
            yield [name]


class Help(unittest.TestCase):
    def test_every_command_has_help(self):
        for c in commands():
            with self.subTest(command=" ".join(c)):
                with self.assertRaises(SystemExit) as ex:
                    import contextlib, io
                    with contextlib.redirect_stdout(io.StringIO()) as out:
                        cli.main(c + ["--help"])
                self.assertEqual(ex.exception.code, 0)
                self.assertIn("usage: cutter", out.getvalue())

    def test_readme_commands_exist(self):
        names = {" ".join(c) for c in commands()}
        for c in ("new", "doctor", "analyze", "music analyze", "music license", "retime", "timeline", "filmcraft build",
                  "filmcraft export", "filmcraft gui", "map overview", "map route", "review-data", "review password", "serve",
                  "feedback fetch", "feedback reply", "deploy", "agent setup", "migrate"):
            self.assertIn(c, names)

    def test_version(self):
        r = run_cutter("version", "--json")
        self.assertRegex(json.loads(r.stdout)["version"], r"^\d+\.\d+\.\d+$")


class Results(unittest.TestCase):
    def test_missing_input_is_exit_3_with_json(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            r = run_cutter("retime", "cut9", "--json", cwd=d, check=False)
            self.assertEqual(r.returncode, 3)
            err = json.loads(r.stdout)
            self.assertEqual(err["code"], 3)
            self.assertIn("edit.json", err["error"])

    def test_no_project(self):
        with TempDir() as d:
            r = run_cutter("timeline", "cut1", cwd=d, check=False)
            self.assertEqual(r.returncode, 3)
            self.assertIn("cutter new", r.stderr)

    def test_new_project(self):
        with TempDir() as d:
            footage = d / "Footage"
            footage.mkdir()
            r = run_cutter("new", d / "film", "--media", footage, "--title", "My film", "--language", "de", "--dry-run", "--json")
            self.assertTrue(json.loads(r.stdout)["dry_run"])
            self.assertFalse((d / "film").exists())
            run_cutter("new", d / "film", "--media", footage, "--title", "My film", "--language", "de")
            toml = (d / "film" / "cutter.toml").read_text()
            self.assertIn(f'footage = "{footage}"', toml)
            self.assertIn('language = "de"', toml)
            self.assertIn('allowed_licenses = ["CC0", "CC BY"]', toml)
            self.assertEqual((d / "film" / "CLAUDE.md").read_text(), "@AGENTS.md\n")
            agents = (d / "film" / "AGENTS.md").read_text()
            self.assertIn("My film", agents)
            self.assertIn(str(footage), agents)
            for sub in ("analysis", "music", "cuts", "exports", ".cutter/review"):
                self.assertTrue((d / "film" / sub).is_dir(), sub)
            self.assertIn(".cutter/", (d / "film" / ".gitignore").read_text())
            r = run_cutter("new", d / "film", "--media", footage, check=False)
            self.assertEqual(r.returncode, 3, "never overwrite a project")
            r = run_cutter("new", footage / "inside", "--media", footage, check=False)
            self.assertEqual(r.returncode, 3, "never inside the footage")

    def test_plan_applies_changes_as_code(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            (d / "cuts" / "cut1").mkdir(parents=True)
            (d / "cuts" / "cut1" / "edit.json").write_text(json.dumps(demo.edit_list("cut1")), encoding="utf-8")
            run_cutter("plan", "cut2", "--new", cwd=d)
            script = d / "cuts" / "cut2" / "plan.py"
            text = script.read_text()
            self.assertIn("cut1", text)
            script.write_text(text.replace("    # if s0[\"nr\"] == 7:\n    #     continue",
                                           "    if s0[\"nr\"] == 7:\n        continue"), encoding="utf-8")
            run_cutter("plan", "cut2", cwd=d)
            e = json.loads((d / "cuts" / "cut2" / "edit.json").read_text())
            shots = [s for sec in e["sections"] for s in sec["shots"]]
            self.assertEqual(len(shots), 7)
            self.assertNotIn(7, [s["nr_previous"] for s in shots])
            self.assertEqual([s["nr"] for s in shots], list(range(1, 8)))
            self.assertEqual(e["title"], "Demo (cut2)")

    def test_doctor_json(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text(f'[media]\nfootage = "{d}/nowhere"\n', encoding="utf-8")
            r = run_cutter("doctor", "--json", cwd=d, check=False)
            self.assertIn(r.returncode, (0, 4))
            out = json.loads(r.stdout)
            names = {c["name"]: c for c in out["checks"]}
            self.assertFalse(names["Footage 'footage'"]["ok"])
            self.assertIn("fix", names["Footage 'footage'"])
            self.assertEqual(r.returncode, 4, "unreadable footage is a required check")

    def test_export_always_burns_subtitles(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            r = run_cutter("filmcraft", "export", "cut1", "--dry-run", "--json", cwd=d)
            cmd = json.loads(r.stdout)["command"]
            self.assertEqual(json.loads(cmd[cmd.index("--settings") + 1])["burnCaptions"], True)
            self.assertIn(str(d / "exports" / "cut1.mp4"), cmd)

    def test_bridge_port_9876_is_refused_in_tests(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            r = run_cutter("filmcraft", "build", "cut1", "--bridge", "9876", cwd=d, check=False)
            self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
