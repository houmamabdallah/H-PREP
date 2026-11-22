import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import test_scrub


class ScrubSkipTests(unittest.TestCase):
    def test_denylist_outcomes(self):
        cases = (
            ("missing", None, 1, None),
            # Split the word so it also clears the scan of this tracked file.
            ("no_hit", "zqxj" + "vkpw", 0, None),
            ("empty", "", 0, "needs at least one entry"),
            ("hit", "unittest", 0, "matches a restricted term"),
        )
        for arm, content, skips, failure in cases:
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "denylist.txt"
                if content is not None:
                    path.write_text(content, encoding="utf-8")
                suite = unittest.TestSuite([test_scrub.ScrubTests("test_private_names")])
                result = unittest.TestResult()
                with patch.dict(os.environ, HPREP_DENYLIST=str(path)):
                    suite.run(result)

                self.assertEqual(result.testsRun, 1)
                self.assertEqual(result.errors, [])
                self.assertEqual(len(result.skipped), skips)
                if skips:
                    reason = result.skipped[0][1]
                    self.assertIn(str(path), reason)
                    self.assertIn("HPREP_DENYLIST", reason)
                if failure is None:
                    self.assertEqual(result.failures, [])
                else:
                    self.assertEqual(len(result.failures), 1)
                    self.assertIn(failure, result.failures[0][1])
