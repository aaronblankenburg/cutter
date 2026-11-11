"""The version and the published image belong together: the launcher pulls ghcr.io/<owner>/cutter:<VERSION>, and the
plugin runs whatever is on the main branch. So

  - the version is the same everywhere it is written, and
  - what goes into the image must not change under a version that is already tagged: such a checkout would run
    its new documentation and skill against the old code in the image without any error.

Needs git and the tags (CI: fetch-depth 0). Without a checkout (in the image) the second test skips itself.
"""
import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE_INPUT = ("src", "requirements.txt", "Dockerfile", "docker", "pyproject.toml")     # what the Dockerfile copies


def version():
    return re.search(r'^VERSION = "(.+)"', (ROOT / "src/cutter/__init__.py").read_text(encoding="utf-8"), re.M).group(1)


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


class Release(unittest.TestCase):
    def test_the_version_is_the_same_everywhere(self):
        v = version()
        self.assertRegex(v, r"^\d+\.\d+\.\d+$")
        found = {"pyproject.toml": re.search(r'^version = "(.+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)}
        if (ROOT / ".claude-plugin").is_dir():                # not in the image
            market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
            found["plugin.json"] = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
            found["marketplace.json metadata"] = market["metadata"]["version"]
            found["marketplace.json plugin"] = market["plugins"][0]["version"]
            for name in ("compose.yaml", "docs/development.md", "docs/deploy.md"):
                for tag in re.findall(r"/cutter:(\d+\.\d+\.\d+)", (ROOT / name).read_text(encoding="utf-8")):
                    found[name] = tag
        self.assertEqual(found, {k: v for k in found})

    def test_a_tagged_version_has_the_code_of_its_tag(self):
        if not shutil.which("git") or git("rev-parse", "--is-inside-work-tree").returncode != 0:
            self.skipTest("not a git checkout")
        if git("rev-parse", "--show-toplevel").stdout.strip() != str(ROOT):
            self.skipTest("not the checkout of cutter itself")
        tag = f"v{version()}"
        if git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}").returncode != 0:
            return                                            # not released yet: the launcher builds this image itself
        changed = git("diff", "--name-only", tag, "--", *IMAGE_INPUT).stdout.split()
        changed += git("ls-files", "--others", "--exclude-standard", "--", *IMAGE_INPUT).stdout.split()
        self.assertEqual(changed, [], f"\n{len(changed)} files of the image changed since {tag}, but the version is still "
                         f"{version()}: the launcher would run them against the old image.\nBump the version "
                         "(docs/development.md → Releases) in the same change.")


if __name__ == "__main__":
    unittest.main()
