"""The browser tests of the review app (tests/browser, headless Chrome) as one unittest – opt-in, they take minutes.

  CUTTER_BROWSER=1 python -m unittest tests/test_browser.py -v
Needs node, Chrome (CHROME=/path) and playwright-core ("npm ci" in tests/browser, or NODE_PATH). See tests/browser/run.py.
"""
import os
import subprocess
import sys
import unittest

from helpers import TESTS


@unittest.skipUnless(os.environ.get("CUTTER_BROWSER") == "1", "set CUTTER_BROWSER=1 to run the browser tests")
class Browser(unittest.TestCase):
    def test_review_app_in_english_and_german(self):
        r = subprocess.run([sys.executable, str(TESTS / "browser" / "run.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])
        self.assertIn("checks passed in 7 runs", r.stdout)


if __name__ == "__main__":
    unittest.main()
