from __future__ import annotations

import hashlib
import json
import os
import shutil
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
SELECTED_PUBLIC_SOURCE_COMMITS = {
    "das-standard": "57304e33344ae8e3542aca35a38a3c0386834ff9",
    "dasops": "d18bce91443e050472f613ebcb1e3b96e5f89d93",
    "cadence-oss": "6cde6d02e771a7ce0d4ae705debec84826dd5582",
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

    def test_lock_pins_selected_public_source_commits(self) -> None:
        lock = json.loads((ROOT / "suite_lock.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {
                module: item["lockedRef"]["pinValue"]
                for module, item in lock["modules"].items()
            },
            SELECTED_PUBLIC_SOURCE_COMMITS,
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

    def test_invalid_inputs_stop_before_semantics_or_sync(self) -> None:
        from unittest.mock import patch
        from dassuite.closure import CANONICAL_SCHEMA_FILES
        from dassuite.report import generate_report
        from dassuite.sync import sync_modules
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (*CANONICAL_SCHEMA_FILES, *CANONICAL_SCHEMA_FILES.values(),
                         "VERSION", "pyproject.toml", "dassuite/__init__.py"):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            originals = {name: (root / name).read_bytes() for name in CANONICAL_SCHEMA_FILES}
            cases = [(name, b"[]\n") for name in CANONICAL_SCHEMA_FILES]
            cases += [
                ("suite_lock.json", b"{"),
                ("suite_version_closure.json", yaml.safe_dump(json.loads(originals["suite_version_closure.json"])).encode()),
                ("suite.tooling_lock.json", None),
                ("modules.yaml", b"modules: [\n"),
            ]
            for name, invalid in cases:
                with self.subTest(input=name, invalid=invalid):
                    try:
                        if invalid is None:
                            (root / name).unlink()
                        else:
                            (root / name).write_bytes(invalid)
                        issues = validate_closure_artifacts(root)
                        self.assertTrue(issues)
                        self.assertTrue(any(name in issue for issue in issues), issues)
                        with patch("dassuite.report.build_status_payload", side_effect=AssertionError("invalid inputs consumed")):
                            for workspace in (False, True):
                                report = generate_report(root, enforce_workspace=workspace)
                                self.assertTrue(report["issues"])
                                self.assertEqual(report["modules"], [])
                        with patch("dassuite.sync._sync_locked_module", side_effect=AssertionError("sync attempted")):
                            ok, results, issues = sync_modules(suite_root=root, tier="oss")
                            self.assertFalse(ok)
                            self.assertEqual(results, [])
                            self.assertTrue(issues)
                        for command in (("closure", "check"), ("report",), ("report", "--workspace"), ("status",), ("sync",)):
                            proc = self.run_cli(*command, "--repo-root", str(root), "--json")
                            self.assertEqual(proc.returncode, 2, proc.stdout)
                            payload = json.loads(proc.stdout)
                            self.assertTrue(any(name in issue for issue in payload["issues"]), payload)
                        self.assertFalse((root / "src").exists())
                    finally:
                        (root / name).write_bytes(originals[name])
            self.assertEqual(validate_closure_artifacts(root), [])
            report = generate_report(root)
            self.assertEqual(report["issues"], [])
            self.assertEqual(len(report["modules"]), 3)
            self.assertTrue(all(item["state"] == "missing" for item in report["modules"]))

    def test_duplicate_module_ids_stop_closure_and_sync(self) -> None:
        from unittest.mock import patch
        from dassuite.closure import CANONICAL_SCHEMA_FILES
        from dassuite.report import generate_report
        from dassuite.sync import sync_modules
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (*CANONICAL_SCHEMA_FILES, *CANONICAL_SCHEMA_FILES.values(),
                         "VERSION", "pyproject.toml", "dassuite/__init__.py"):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            manifest = root / "modules.yaml"
            original = manifest.read_bytes()
            data = yaml.safe_load(original)
            data["modules"].append({**data["modules"][0], "path": "src/duplicate-standard"})
            manifest.write_text(yaml.safe_dump(data))
            issues = validate_closure_artifacts(root)
            self.assertTrue(any("duplicate module ID" in issue for issue in issues), issues)
            with patch("dassuite.report.build_status_payload", side_effect=AssertionError("ambiguous modules observed")):
                self.assertTrue(generate_report(root, enforce_workspace=True)["issues"])
            with patch("dassuite.sync._sync_locked_module", side_effect=AssertionError("ambiguous sync attempted")):
                ok, results, issues = sync_modules(suite_root=root, tier="oss")
                self.assertFalse(ok)
                self.assertEqual(results, [])
                self.assertTrue(issues)
            for command in (("closure", "check"), ("report", "--workspace"), ("sync",)):
                proc = self.run_cli(*command, "--repo-root", str(root), "--json")
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertTrue(json.loads(proc.stdout)["issues"])
            self.assertFalse((root / "src").exists())
            manifest.write_bytes(original)
            self.assertEqual(validate_closure_artifacts(root), [])

    def test_local_canonical_schemas_match_selected_standard(self) -> None:
        expected = {
            "suite_lock.schema.json": "7a8b8ba8b9b8b0c47c53f708f272dbf7bee73a6d80b080945ee4989062c99285",
            "suite_version_closure.schema.json": "160dc5cd80b4a9c8ceac8f5584054aadf04b4219a75c7eaecc47219bf2985d90",
            "tooling_lock.schema.json": "8352c45a4f0dc994ec9a83a197bfcac9da377e323832606bdf12540e4caf3f0f",
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
            # Workspace modules own their content policies and are not shipped
            # inside the Suite source archive.
            if path.relative_to(ROOT).parts[0] != "src" and path.is_file() and ".git" not in path.parts:
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


class VersionSemanticsTests(unittest.TestCase):
    def test_pep440_does_not_erase_beta_identity(self):
        from dassuite.closure import _release_version
        self.assertEqual(_release_version("1.2.0-beta.1"),"1.2.0b1")
        self.assertNotEqual(_release_version("1.2.0-beta.1"),_release_version("1.2.0-beta.2"))
        self.assertNotEqual(_release_version("1.2.0-beta.1"),"1.2.0")

    def test_suite_self_version_drift_rejects(self):
        import shutil
        from dassuite.closure import validate_version_semantics
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ("VERSION","pyproject.toml","dassuite/__init__.py","suite_version_closure.json","suite.tooling_lock.json"):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,path)
            (root/"dassuite/__init__.py").write_text('__version__ = "1.2.0b2"\n')
            self.assertTrue(validate_version_semantics(root))


if __name__ == "__main__":
    unittest.main()
