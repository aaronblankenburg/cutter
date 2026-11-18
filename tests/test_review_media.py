"""The review app's rendered cuts can live apart from its comments (paths.review_media)."""
import pathlib
import tempfile
import unittest

from cutter.config import Project


class ReviewMedia(unittest.TestCase):
    def test_default_is_next_to_the_comments(self):
        with tempfile.TemporaryDirectory() as d:
            p = Project(d, {})
            self.assertEqual(p.path("review_media"), p.path("review"))
            p = Project(d, {"paths": {"review": "/elsewhere/review"}})
            self.assertEqual(p.path("review_media"), pathlib.Path("/elsewhere/review"))

    def test_app_config_for_an_embedding_app(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotIn("embedded", Project(d, {}).app_config())
            c = Project(d, {"review": {"person": "Host User", "submit": False, "embedded": True}}).app_config()
            self.assertEqual((c["person"], c["submit"], c["embedded"]), ("Host User", False, True))

    def test_own_folder(self):
        with tempfile.TemporaryDirectory() as d:
            p = Project(d, {"paths": {"review_media": "/work/p1/review"}})
            self.assertEqual(p.path("review_media"), pathlib.Path("/work/p1/review"))
            self.assertEqual(p.path("review"), pathlib.Path(d).resolve() / ".cutter/review/data")


if __name__ == "__main__":
    unittest.main()
