from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from dassuite.closure import validate_closure_artifacts
from dassuite.core import Module, module_path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_MODULES = {"das-standard", "dasops", "cadence-oss"}
FINAL_PUBLIC_RELEASE_COMMITS = {
    "das-standard": "ac5a52fe9e8997392b3d21a488097e394d8e3102",
    "dasops": "01f54872349812598190ecf0b28e0dcad2927914",
    "cadence-oss": "e4054537c0962441037dd5403d1a1692434fde73",
}


class PublicReleaseTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        return subprocess.run(
            [sys.executable, "-m", "dassuite", *args],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )

    def test_module_catalog_is_oss_only(self) -> None:
        data = yaml.safe_load((ROOT / "modules.yaml").read_text(encoding="utf-8"))
        self.assertEqual({item["id"] for item in data["modules"]}, PUBLIC_MODULES)
        self.assertTrue(all(item["tier"] == "oss" and item["required"] for item in data["modules"]))

    def test_lock_has_only_public_exact_commits(self) -> None:
        lock = json.loads((ROOT / "suite_lock.json").read_text(encoding="utf-8"))
        self.assertEqual(set(lock["modules"]), PUBLIC_MODULES)
        for module, item in lock["modules"].items():
            ref = item["lockedRef"]
            self.assertEqual(ref["pinType"], "commit")
            self.assertRegex(ref["pinValue"], r"^[0-9a-f]{40}$")
            self.assertEqual(ref["uri"], f"https://github.com/AzzCraft/{module}.git")
            self.assertFalse(ref["floating"])

    def test_lock_pins_final_public_release_commits(self) -> None:
        lock = json.loads((ROOT / "suite_lock.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {
                module: item["lockedRef"]["pinValue"]
                for module, item in lock["modules"].items()
            },
            FINAL_PUBLIC_RELEASE_COMMITS,
        )

    def test_three_lock_views_agree(self) -> None:
        lock = json.loads((ROOT / "suite_lock.json").read_text(encoding="utf-8"))
        closure = json.loads((ROOT / "suite_version_closure.json").read_text(encoding="utf-8"))
        tooling = json.loads((ROOT / "suite.tooling_lock.json").read_text(encoding="utf-8"))
        mapping = {"standard": "das-standard", "factory": "dasops", "verifyRuntime": "cadence-oss"}
        for ref_name, module in mapping.items():
            self.assertEqual(closure["refs"][ref_name], lock["modules"][module]["lockedRef"])
        self.assertEqual(tooling["verifyRuntime"], lock["modules"]["cadence-oss"]["lockedRef"])

    def test_closure_validator_passes(self) -> None:
        self.assertEqual(validate_closure_artifacts(ROOT), [])

    def test_local_canonical_schemas_match_das_standard_v1_4_0(self) -> None:
        expected = {
            "suite_lock.schema.json": "64532328f53009b64ec71a9461b7da3c4ed8648a6b9d958446c245684e9953d8",
            "suite_version_closure.schema.json": "330210438d4fbc0e879d528526c6a7efb4c638ba7f70419efcaf58fd9086e8e8",
            "tooling_lock.schema.json": "3ab5e0a50c5e6dd355b3d882eab2a2258b1daf170eb8c9d9541b6cf2ee380cf5",
        }
        for name, digest in expected.items():
            payload = (ROOT / "schemas" / "das-standard" / name).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), digest, name)

    def test_public_tree_has_no_private_product_names(self) -> None:
        forbidden = ["cadence-" + "pro", "cadence-" + "enterprise", "dasops-" + "pro", "das-" + "leafer"]
        public_files = [
            *(ROOT / name for name in (
                "README.md", "THIRD_PARTY_NOTICES.md", "modules.yaml", "suite_lock.json",
                "suite_version_closure.json", "suite.tooling_lock.json", "pyproject.toml",
                "setup.py", "requirements.txt", "VERSION",
            )),
            *(ROOT / "dassuite" / name for name in (
                "__init__.py", "__main__.py", "closure.py", "core.py", "locks.py",
                "refs.py", "report.py", "sync.py",
            )),
            ROOT / "profiles" / "oss.json",
            ROOT / "docs" / "README.md",
            ROOT / "docs" / "USER_MANUAL.md",
        ]
        paths = public_files if (ROOT / "dassuite" / "rollback.py").exists() else ROOT.rglob("*")
        for path in paths:
            if path.is_file() and ".git" not in path.parts:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                for value in forbidden:
                    self.assertNotIn(value, text, str(path.relative_to(ROOT)))

    def test_module_paths_are_contained(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = Module("bad", "bad", "../escape", "oss", True)
            with self.assertRaises(ValueError):
                module_path(root, module)

    def test_status_command(self) -> None:
        result = self.run_cli("status", "--json")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual({item["id"] for item in json.loads(result.stdout)["modules"]}, PUBLIC_MODULES)

    def test_report_command(self) -> None:
        result = self.run_cli("report", "--json")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["summary"]["totalModules"], 3)

    def test_closure_command(self) -> None:
        result = self.run_cli("closure", "check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Closure artifacts: PASS", result.stdout)

    def test_sync_help_documents_locked_default(self) -> None:
        result = self.run_cli("sync", "--help")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("--repo-root", result.stdout)
        self.assertNotIn("--git-base", result.stdout)


if __name__ == "__main__":
    unittest.main()
