"""Offline tests for shared/tools/kaggle_live_plan.py, the retired stub (stdlib unittest).

    python3 -m unittest discover -s plugins/kaggle/tests
"""

import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "shared" / "tools" / "kaggle_live_plan.py"


class StubTests(unittest.TestCase):
    def test_the_retired_live_plan_points_to_the_status_page_and_exits_2(self):
        for args in ([], ["--keep-rev"], ["--root", "/nowhere", "--no-render"]):
            p = subprocess.run([sys.executable, "-B", str(TOOL), *args], capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 2, args)
            self.assertEqual(p.stdout, "", args)
            self.assertTrue(p.stderr.startswith("kaggle_live_plan: retired"), p.stderr)
            for text in ("kaggle_status.py", "reports/status.pdf", "reports/status.json", "submissions/live-plan.json"):
                self.assertIn(text, p.stderr)
        # the rev marker stays the first line, the docstring says the same
        lines = TOOL.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith("# rev. "), lines[0])
        self.assertIn("retired", lines[2])


if __name__ == "__main__":
    unittest.main()
