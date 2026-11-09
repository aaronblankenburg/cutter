"""The documentation stays in step with the code: the legacy mapping table and relative links."""
import re
import unittest

from helpers import REPO
from cutter import legacy

DOCS = [REPO / "README.md", REPO / "AGENTS.md", *sorted((REPO / "docs").glob("*.md")), *sorted((REPO / "skills").rglob("*.md")),
        *sorted((REPO / "examples").rglob("*.md")), *sorted((REPO / "tests").rglob("README.md"))]


@unittest.skipUnless((REPO / "docs").is_dir(), "docs/ is not part of the Docker image")
class Docs(unittest.TestCase):
    def test_every_legacy_key_is_documented(self):
        text = (REPO / "docs" / "formats.md").read_text(encoding="utf-8")
        for name in dir(legacy):
            table = getattr(legacy, name)
            if not name.isupper() or not isinstance(table, dict):
                continue
            for old, new in table.items():
                if old == new:
                    continue
                if name == "OTIO_META_KEYS":        # stored the other way round (English -> old key)
                    old, new = new, old
                with self.subTest(table=name, key=old):
                    self.assertIn(f"`{old}` → `{new}`", text)

    def test_relative_links_resolve(self):
        for doc in DOCS:
            if "node_modules" in doc.parts:
                continue
            for target in re.findall(r"\]\(([^)\s]+)\)", doc.read_text(encoding="utf-8")):
                if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                    continue
                path = target.split("#")[0]
                with self.subTest(doc=str(doc.relative_to(REPO)), link=target):
                    self.assertTrue((doc.parent / path).exists(), f"{doc.relative_to(REPO)} links to missing {target}")


if __name__ == "__main__":
    unittest.main()
