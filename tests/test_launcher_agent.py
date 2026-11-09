"""The launcher bin/cutter (POSIX sh, run with dash) and `agent setup`.

Docker is replaced by a stub that records its arguments, so these tests check exactly which folders the launcher
mounts and how; uname, ip, hostname, ipconfig and scutil are stubbed to run the Linux and the macOS branches.
`agent setup` only ever runs with a temporary HOME.
"""
import json
import os
import pathlib
import shutil
import subprocess
import unittest

from helpers import LAUNCHER, REPO, TempDir, run_cutter

DASH = shutil.which("dash")
SKILL = REPO / "skills" / "video-editing"

DOCKER_STUB = r"""#!/bin/sh
case "$1" in
  info) exit 0 ;;
  version) echo 29.0.0; exit 0 ;;
  image) [ "$2" = inspect ] && { [ -n "$STUB_NO_IMAGE" ] && exit 1; echo 1800000000; exit 0; } ;;
  run) shift; for a in "$@"; do printf '%s\n' "$a"; done > "$STUB_LOG"; exit 0 ;;
esac
exit 0
"""


def stub(folder, name, text):
    p = pathlib.Path(folder) / name
    p.write_text(text, encoding="utf-8")
    p.chmod(0o755)


@unittest.skipUnless(DASH and LAUNCHER.exists(), "needs dash and bin/cutter (not part of the Docker image)")
class Launcher(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.d = self.tmp.__enter__()
        self.bin = self.d / "bin"
        self.bin.mkdir()
        stub(self.bin, "docker", DOCKER_STUB)
        self.log = self.d / "docker-args.txt"
        self.home = self.d / "home"
        self.home.mkdir()
        self.footage = self.d / "Footage with spaces"
        (self.footage / "day1").mkdir(parents=True)
        self.film = self.d / "film"
        (self.film / "cuts" / "cut1").mkdir(parents=True)
        (self.film / "cutter.toml").write_text(
            f'title = "T"\n[media]\ncam = "{self.footage}"\nmissing = "/does/not/exist"\n[music]\nallowed_licenses = ["CC0"]\n',
            encoding="utf-8")

    def tearDown(self):
        self.tmp.__exit__(None, None, None)

    def run_launcher(self, *args, cwd=None, os_name="Linux", extra_env=None):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "STUB_LOG": str(self.log),
               "CUTTER_IMAGE": "cutter-test:stub", "TZ": "Europe/Berlin"}
        if os_name == "Darwin":
            stub(self.bin, "uname", "#!/bin/sh\necho Darwin\n")
            stub(self.bin, "ipconfig", '#!/bin/sh\n[ "$2" = en0 ] && echo 192.0.2.10\n')
            stub(self.bin, "scutil", "#!/bin/sh\necho test-mac\n")
        else:
            stub(self.bin, "ip", "#!/bin/sh\necho '1.1.1.1 via 192.0.2.1 dev eth0 src 192.0.2.20 uid 1000'\n")
        env.update(extra_env or {})
        r = subprocess.run([DASH, str(LAUNCHER), *map(str, args)], cwd=cwd or self.film, env=env, capture_output=True, text=True)
        args_seen = self.log.read_text().splitlines() if self.log.exists() else []
        return r, args_seen

    def mounts(self, args):
        return [args[i + 1] for i, a in enumerate(args) if a == "-v"]

    def test_runs_in_the_image_with_same_path_mounts(self):
        r, args = self.run_launcher("timeline", "cut1", cwd=self.film / "cuts" / "cut1")
        self.assertEqual(r.returncode, 0, r.stderr)
        image = args.index("cutter-test:stub")
        self.assertEqual(args[image + 1:], ["timeline", "cut1"])
        m = self.mounts(args[:image])
        self.assertIn(f"{self.film}:{self.film}", m, "project read-write at the same path")
        self.assertIn(f"{self.footage}:{self.footage}:ro", m, "footage read-only at the same path, spaces and all")
        self.assertIn("cutter-models:/models", m)
        self.assertFalse([x for x in m if "/does/not/exist" in x], "missing folders are not mounted (Docker would create them)")
        self.assertEqual(args[args.index("-w") + 1], str(self.film / "cuts" / "cut1"))
        self.assertEqual(args[args.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")
        self.assertIn("TZ=Europe/Berlin", args)

    def test_footage_behind_a_symlink_is_mounted_as_written(self):
        link = self.d / "footage-link"
        link.symlink_to(self.footage)
        (self.film / "cutter.toml").write_text(f'[media]\ncam = "{link}"\n', encoding="utf-8")
        r, args = self.run_launcher("analyze")
        self.assertIn(f"{link}:{link}:ro", self.mounts(args), "clips refer to the path in cutter.toml")

    def test_new_makes_paths_absolute_and_mounts_them(self):
        r, args = self.run_launcher("new", "myfilm", "--media", "Footage with spaces", "--title", "A title", cwd=self.d)
        self.assertEqual(r.returncode, 0, r.stderr)
        image = args.index("cutter-test:stub")
        self.assertEqual(args[image + 1:], ["new", str(self.d / "myfilm"), "--media", str(self.footage), "--title", "A title"])
        self.assertTrue((self.d / "myfilm").is_dir(), "the project folder is created by the launcher, owned by the user")
        m = self.mounts(args[:image])
        self.assertIn(f"{self.d / 'myfilm'}:{self.d / 'myfilm'}", m)
        self.assertIn(f"{self.footage}:{self.footage}:ro", m)

    def test_model_cache_read_only(self):
        cache = self.d / "hub"
        cache.mkdir()
        r, args = self.run_launcher("analyze", extra_env={"CUTTER_MODEL_CACHE": str(cache)})
        self.assertIn(f"{cache}:/models/hub:ro", self.mounts(args))
        self.assertIn("HF_HUB_OFFLINE=1", args)

    def test_serve_maps_the_port_and_prints_addresses_linux(self):
        r, args = self.run_launcher("serve", extra_env={"CUTTER_REVIEW_PORT": "8299"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(args[args.index("-p") + 1], "8299:8089", "all interfaces by default")
        image = args.index("cutter-test:stub")
        self.assertEqual(args[image + 1:], ["serve", "--project", str(self.film), "--bind", "0.0.0.0", "--port", "8089"])
        self.assertIn("http://localhost:8299", r.stderr)
        self.assertIn("http://192.0.2.20:8299", r.stderr)
        self.assertIn("plain HTTP", r.stderr)

    def test_serve_bind_loopback(self):
        r, args = self.run_launcher("serve", "--bind", "127.0.0.1", "--port", "8298")
        self.assertEqual(args[args.index("-p") + 1], "127.0.0.1:8298:8089")
        self.assertIn("http://127.0.0.1:8298", r.stderr)
        self.assertNotIn("192.0.2", r.stderr)

    def test_serve_addresses_macos(self):
        r, args = self.run_launcher("serve", "--port", "8297", os_name="Darwin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("http://192.0.2.10:8297", r.stderr)
        self.assertIn("http://test-mac.local:8297", r.stderr)

    def test_deploy_mounts_ssh_read_only(self):
        (self.home / ".ssh").mkdir()
        r, args = self.run_launcher("deploy", "app", "--dry-run")
        self.assertIn(f"{self.home / '.ssh'}:/home/cutter/.ssh:ro", self.mounts(args))

    def test_output_paths_outside_the_project(self):
        out = self.d / "elsewhere" / "film.mp4"
        r, args = self.run_launcher("filmcraft", "export", "cut1", "--output", "../elsewhere/film.mp4")
        image = args.index("cutter-test:stub")
        self.assertEqual(args[image + 1:], ["filmcraft", "export", "cut1", "--output", str(out)])
        self.assertIn(f"{out.parent}:{out.parent}", self.mounts(args[:image]))

    def test_missing_image_without_terminal(self):
        r, _ = self.run_launcher("timeline", "cut1", extra_env={"STUB_NO_IMAGE": "1"})
        self.assertEqual(r.returncode, 3)
        self.assertIn("image pull", r.stderr)

    def test_doctor_without_image_reports_json(self):
        r, _ = self.run_launcher("doctor", "--json", extra_env={"STUB_NO_IMAGE": "1"})
        self.assertEqual(r.returncode, 4)
        d = json.loads(r.stdout)
        self.assertFalse(d["ok"])
        names = {c["name"]: c for c in d["checks"]}
        self.assertTrue(names["Docker"]["ok"])
        self.assertFalse(names["Docker image"]["ok"])
        self.assertIn("image pull", names["Docker image"]["fix"])

    def test_doctor_passes_host_facts_to_the_container(self):
        r, args = self.run_launcher("doctor")
        facts = json.loads(args[args.index("--host-facts") + 1])
        self.assertIn("Docker", [c["name"] for c in facts["checks"]])

    @unittest.skipUnless((SKILL / "SKILL.md").exists(), "skill not present")
    def test_agent_setup_on_the_host(self):
        r, args = self.run_launcher("agent", "setup", "--codex")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(args, [], "agent setup does not need Docker")
        link = self.home / ".agents" / "skills" / "video-editing"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), SKILL.resolve())
        self.assertFalse((self.home / ".claude").exists())
        r, _ = self.run_launcher("agent", "setup", "--codex")
        self.assertIn("unchanged", r.stdout)

    def test_posix_syntax(self):
        r = subprocess.run([DASH, "-n", str(LAUNCHER)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        code = "\n".join(line.split(" # ")[0] for line in LAUNCHER.read_text().splitlines() if not line.lstrip().startswith("#"))
        for gnu_only in ("readlink -f", "sed -i", "[[", "local ", "ip route", "hostname -I"):
            if gnu_only in ("ip route", "hostname -I"):
                continue                    # used only in the Linux branch
            self.assertNotIn(gnu_only, code)


@unittest.skipUnless((SKILL / "SKILL.md").exists(), "skill not present")
class AgentSetup(unittest.TestCase):
    def test_links_with_a_temporary_home(self):
        with TempDir() as home:
            r = run_cutter("agent", "setup", "--json", env={"HOME": str(home)})
            out = json.loads(r.stdout)
            self.assertEqual({x["tool"] for x in out["links"]}, {"Claude Code", "Codex"})
            for rel in (".claude/skills/video-editing", ".agents/skills/video-editing"):
                self.assertEqual((home / rel).resolve(), SKILL.resolve())
            other = home / "other"
            other.mkdir()
            (home / ".claude/skills/video-editing").unlink()
            (home / ".claude/skills/video-editing").symlink_to(other)
            r = run_cutter("agent", "setup", "--claude", env={"HOME": str(home)}, check=False)
            self.assertEqual(r.returncode, 3)
            r = run_cutter("agent", "setup", "--claude", "--replace", env={"HOME": str(home)})
            self.assertEqual((home / ".claude/skills/video-editing").resolve(), SKILL.resolve())

    def test_dry_run_changes_nothing(self):
        with TempDir() as home:
            run_cutter("agent", "setup", "--dry-run", env={"HOME": str(home)})
            self.assertEqual(list(home.iterdir()), [])

    def test_skill_frontmatter(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        head = text.split("---\n")[1]
        self.assertIn("name: video-editing", head)
        self.assertIn("license: MIT", head)
        self.assertIn("description:", head)
        self.assertNotIn("@", head, "no e-mail addresses in the skill")

    def test_plugin_manifests(self):
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
        plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(market["name"], "cutter")
        self.assertEqual(market["plugins"][0]["name"], "cutter")
        self.assertEqual(plugin["name"], "cutter")
        self.assertEqual(plugin["license"], "MIT")
        self.assertNotIn("@", json.dumps(market) + json.dumps(plugin), "no e-mail addresses in the manifests")


if __name__ == "__main__":
    unittest.main()
