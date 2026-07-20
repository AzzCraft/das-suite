"""DAS-02-021 CLI compatibility tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class CliCompatibilityTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "dassuite", *args],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )

    def test_status_json_contract(self) -> None:
        proc = self._run("status", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertIn("modules", payload)
        self.assertTrue(any(item["id"] == "das-standard" for item in payload["modules"]))

    def test_closure_check_contract(self) -> None:
        proc = self._run("closure", "check")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("Closure artifacts: PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main()
