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
    "das-standard": "5529ebaa286017950b76ddda5c82ec695979818a",
    "dasops": "ae2047ae1e3977d815c30594cb8899e7eb2218ff",
    "cadence-oss": "6cb3ed56fc7947d8583022a3053599a02eb40def",
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
            "suite_lock.schema.json": "91361cd60ff526e0e002b9cf1e2505340ebcab2a5b1cae81f490d494a0cd34fa",
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



class SyncBoundaryTests(unittest.TestCase):
    def setUp(self):
        from dassuite.closure import CANONICAL_SCHEMA_FILES
        from unittest.mock import patch
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "das-suite"
        self.root.mkdir()
        for name in (*CANONICAL_SCHEMA_FILES, *CANONICAL_SCHEMA_FILES.values(),
                     "VERSION", "pyproject.toml", "dassuite/__init__.py"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.sources = {}
        self.pins = {}
        for index, module in enumerate(("das-standard", "dasops", "cadence-oss")):
            source = self.base / module
            source.mkdir()
            self.git(source, "init", "-q")
            self.git(source, "config", "user.name", "test")
            self.git(source, "config", "user.email", "test@example.com")
            (source / ".gitignore").write_text("local.env\nspare.env\n")
            (source / "README.md").write_text("original\n")
            self.git(source, "add", ".")
            self.git(source, "commit", "-qm", "initial")
            self.sources[module] = source
            self.pins[module] = self.git(source, "rev-parse", "HEAD")
        # Redirect only Git transport to local real repositories. Identity,
        # checkout, conflict detection and all observations still execute Git.
        from dassuite.core import run as real_run
        def transport(cmd, cwd, **kwargs):
            if cmd[:2] == ["git", "clone"]:
                uri = cmd[-2]
                module = uri.rsplit("/", 1)[-1].removesuffix(".git")
                rc, out = real_run([*cmd[:-2], str(self.sources[module]), cmd[-1]], cwd=cwd, **kwargs)
                if not rc:
                    self.git(Path(cmd[-1]), "remote", "set-url", "origin", uri)
                return rc, out
            if cmd[:2] == ["git", "fetch"]:
                uri = self.git(cwd, "remote", "get-url", "origin")
                module = uri.rsplit("/", 1)[-1].removesuffix(".git")
                return real_run(["git", "-c", f"url.{self.sources[module].as_uri()}.insteadOf={uri}", *cmd[1:]], cwd=cwd, **kwargs)
            return real_run(cmd, cwd=cwd, **kwargs)
        patcher = patch("dassuite.sync.run", side_effect=transport)
        patcher.start()
        self.addCleanup(patcher.stop)
        for module, pin in self.pins.items():
            self.pin(module, pin)
        self.assertEqual(validate_closure_artifacts(self.root), [])

    def git(self, root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT).strip()

    def pin(self, module, pin):
        ref = {"kind": "git", "uri": f"https://test.example/{module}.git",
               "pinType": "commit", "pinValue": pin, "floating": False}
        key = {"das-standard": "standard", "dasops": "factory", "cadence-oss": "verifyRuntime"}[module]
        for name in ("suite_lock.json", "suite_version_closure.json", "suite.tooling_lock.json"):
            path = self.root / name
            data = json.loads(path.read_text())
            if name == "suite_lock.json": data["modules"][module]["lockedRef"] = ref
            elif name == "suite_version_closure.json": data["refs"][key] = ref
            elif module == "cadence-oss": data["verifyRuntime"] = ref
            path.write_text(json.dumps(data))

    def sync(self):
        from dassuite.sync import sync_modules
        return sync_modules(suite_root=self.root, tier="oss")

    def test_ignored_conflict_is_retained_and_retry_succeeds(self):
        self.assertTrue(self.sync()[0])
        source = self.sources["das-standard"]
        (source / "local.env").write_text("tracked default")
        self.git(source, "add", "-f", "local.env")
        self.git(source, "commit", "-qm", "track formerly ignored file")
        self.pin("das-standard", self.git(source, "rev-parse", "HEAD"))
        dest = self.root / "src/das-standard"
        (dest / "local.env").write_text("user value")
        (dest / "spare.env").write_text("keep unrelated")
        before = self.git(dest, "rev-parse", "HEAD")
        ok, results, issues = self.sync()
        self.assertFalse(ok)
        self.assertFalse(issues)
        self.assertEqual([r["action"] for r in results[1:]], ["not_attempted", "not_attempted"])
        self.assertEqual((dest / "local.env").read_text(), "user value")
        self.assertEqual(self.git(dest, "rev-parse", "HEAD"), before)
        (dest / "local.env").unlink()
        self.assertTrue(self.sync()[0])
        self.assertEqual((dest / "local.env").read_text(), "tracked default")
        self.assertEqual((dest / "spare.env").read_text(), "keep unrelated")

    def test_first_clone_failure_keeps_absent_or_empty_destination(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                target = self.root / "src/das-standard"
                if empty: target.mkdir(parents=True)
                self.pin("das-standard", "f" * 40)
                ok, results, _ = self.sync()
                self.assertFalse(ok)
                self.assertEqual(target.exists(), empty)
                if empty: self.assertEqual(list(target.iterdir()), [])
                self.assertEqual(results[1]["action"], "not_attempted")
                self.pin("das-standard", self.pins["das-standard"])
                self.assertTrue(self.sync()[0])
                shutil.rmtree(self.root / "src")

    def test_git_observation_errors_block_status_and_sync(self):
        from dassuite.closure import build_status_payload
        from dassuite.report import generate_report
        self.assertTrue(self.sync()[0])
        index = self.root / "src/das-standard/.git/index"
        original = index.read_bytes()
        index.write_bytes(b"corrupt index")
        self.assertEqual(build_status_payload(self.root)[0]["state"], "observation_error")
        self.assertTrue(generate_report(self.root)["issues"])
        self.assertFalse(self.sync()[0])
        self.assertEqual(index.read_bytes(), b"corrupt index")
        index.write_bytes(original)
        self.assertTrue(self.sync()[0])

    def test_parent_markers_do_not_select_siblings_or_parent_identity(self):
        from dassuite.closure import build_status_payload
        self.assertTrue(self.sync()[0])
        for name in ("GIT_REVISION.txt", "RELEASE_SHA256SUMS"):
            (self.base / name).write_text("unrelated outer archive")
        self.assertTrue(all(r["state"] == "present_locked_match" for r in build_status_payload(self.root)))
        shutil.rmtree(self.root / "src/das-standard/.git")
        self.git(self.root, "init", "-q")
        self.assertEqual(build_status_payload(self.root)[0]["state"], "present_local_override")
        self.assertFalse(self.sync()[0])

    def test_metadata_errors_reject_before_writes(self):
        cases = [
            ("suite_lock.json", lambda d: d["modules"]["dasops"].update(dependsOn=["missing-repo"])),
            ("profiles/oss.json", lambda d: d["modules"]["dasops"].update(required=False)),
            ("suite_version_closure.json", lambda d: d["closureProfiles"]["oss-only"].update(requiredModules=["missing-repo"])),
            ("suite_version_closure.json", lambda d: d.update(closureUpdatedAt="2026-99-99T25:99:99Z")),
            ("modules.yaml", lambda d: d["modules"][1].update(path=d["modules"][0]["path"])),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                data = yaml.safe_load(original)
                mutate(data)
                path.write_text(json.dumps(data))
                self.assertTrue(validate_closure_artifacts(self.root))
                self.assertFalse(self.sync()[0])
                self.assertFalse((self.root / "src").exists())
                path.write_bytes(original)
        self.assertEqual(validate_closure_artifacts(self.root), [])

    def test_ordinary_sync_never_materializes_fixture_sources(self):
        from dassuite.sync import OFFLINE_FIXTURE_PIN
        for name in ("suite_lock.json", "suite_version_closure.json", "suite.tooling_lock.json"):
            path = self.root / name
            text = path.read_text().replace("test.example", "fixture.invalid")
            for pin in self.pins.values(): text = text.replace(pin, OFFLINE_FIXTURE_PIN)
            path.write_text(text)
        self.assertFalse(self.sync()[0])
        self.assertFalse((self.root / "src").exists())

    def test_existing_match_cannot_bypass_full_git_pin_policy(self):
        from dassuite.sync import _sync_locked_module
        from dassuite.core import load_manifest, parse_modules
        self.assertTrue(self.sync()[0])
        module=parse_modules(load_manifest(self.root))[0]
        lock=json.loads((self.root/'suite_lock.json').read_text())
        reference=lock['modules'][module.id]['lockedRef']
        before=self.git(self.root/module.path,'rev-parse','HEAD')
        for change in ({'pinType':'tag'}, {'pinValue':reference['pinValue'][:7]}, {'kind':'custom'}):
            bad=dict(reference,**change)
            ok,_=_sync_locked_module(suite_root=self.root,module=module,locked_ref=bad)
            self.assertFalse(ok)
            self.assertEqual(self.git(self.root/module.path,'rev-parse','HEAD'),before)

    def test_fixture_materialization_requires_explicit_offline_lock(self):
        from dassuite.sync import sync_modules, OFFLINE_FIXTURE_PIN
        lock=json.loads((self.root/'suite_lock.json').read_text())
        for module,entry in lock['modules'].items():
            entry['lockedRef'].update(uri='https://fixture.invalid/'+module+'.git',pinValue=OFFLINE_FIXTURE_PIN)
        path=self.root/'tests/fixtures/closure/lock.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(lock))
        for mode,offline,selected in [('fixture',False,path),('fixture',True,None),(None,True,path)]:
            ok,_,_=sync_modules(suite_root=self.root,tier='oss',closure_mode=mode,offline=offline,lock_path=selected)
            self.assertFalse(ok);self.assertFalse((self.root/'src').exists())
        ok,results,issues=sync_modules(suite_root=self.root,tier='oss',closure_mode='fixture',offline=True,lock_path=path)
        self.assertTrue(ok,(results,issues))
        for module in lock['modules']:
            self.assertEqual(self.git(self.root/'src'/module,'rev-parse','HEAD'),OFFLINE_FIXTURE_PIN)
        # An idempotent retry must observe the explicitly selected fixture lock.
        ok,results,issues=sync_modules(suite_root=self.root,tier='oss',closure_mode='fixture',offline=True,lock_path=path)
        self.assertTrue(ok,(results,issues))

    def test_explicit_lock_is_observed_before_and_after_sync(self):
        from dassuite.sync import sync_modules
        self.assertTrue(self.sync()[0])
        module='das-standard';source=self.sources[module]
        (source/'README.md').write_text('selected alternate revision')
        self.git(source,'add','README.md');self.git(source,'commit','-qm','next revision')
        selected_pin=self.git(source,'rev-parse','HEAD')
        lock=json.loads((self.root/'suite_lock.json').read_text())
        lock['modules'][module]['lockedRef']['pinValue']=selected_pin
        alternate=self.root/'alternate-lock.json';alternate.write_text(json.dumps(lock))
        before=(self.root/'suite_lock.json').read_bytes()
        for attempt in range(2):
            ok,results,issues=sync_modules(suite_root=self.root,tier='oss',lock_path=alternate)
            self.assertTrue(ok,(results,issues))
            self.assertEqual(self.git(self.root/'src'/module,'rev-parse','HEAD'),selected_pin)
        self.assertEqual((self.root/'suite_lock.json').read_bytes(),before)

    def test_explicit_lock_rejects_invalid_inputs_before_any_sync(self):
        from copy import deepcopy
        from unittest.mock import patch
        from dassuite.sync import sync_modules

        canonical = (self.root / 'suite_lock.json').read_bytes()
        valid = json.loads(canonical)
        alternate = self.root / 'alternate-lock.json'
        cases = [('root', []), ('root', None), ('validation', '{'), ('missing', ...)]
        for field, mutate in (
            ('modules', lambda d: d.update(modules=[])),
            ('lockedRef', lambda d: d['modules']['dasops'].update(lockedRef=[])),
            ('floating', lambda d: d['modules']['dasops']['lockedRef'].update(floating=True)),
            ('dependsOn', lambda d: d['modules']['dasops'].update(dependsOn=['missing-repo'])),
            ('module IDs', lambda d: d['modules'].pop('das-standard')),
        ):
            bad = deepcopy(valid)
            mutate(bad)
            cases.append((field, bad))
        for field, bad in cases:
            with self.subTest(field=field, bad=bad):
                if bad is ...:
                    alternate.unlink(missing_ok=True)
                else:
                    alternate.write_text(bad if isinstance(bad, str) else json.dumps(bad))
                before = alternate.read_bytes() if alternate.exists() else None
                with patch('dassuite.sync._sync_locked_module', side_effect=AssertionError('invalid input consumed')):
                    ok, results, issues = sync_modules(suite_root=self.root, tier='oss', lock_path=alternate)
                self.assertFalse(ok)
                self.assertEqual(results, [])
                self.assertTrue(issues)
                self.assertIn('alternate-lock.json', ' '.join(issues))
                self.assertEqual(alternate.read_bytes() if alternate.exists() else None, before)
                self.assertEqual((self.root / 'suite_lock.json').read_bytes(), canonical)
                self.assertFalse((self.root / 'src').exists())

    def test_explicit_lock_cannot_bypass_catalog_and_profile_preflight(self):
        from unittest.mock import patch
        from dassuite.sync import sync_modules

        alternate = self.root / 'alternate-lock.json'
        alternate.write_bytes((self.root / 'suite_lock.json').read_bytes())
        for name, mutate in (
            ('modules.yaml', lambda d: d['modules'][1].update(path=d['modules'][0]['path'])),
            ('profiles/oss.json', lambda d: d['modules']['dasops'].update(required=False)),
        ):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                data = yaml.safe_load(original)
                mutate(data)
                path.write_text(json.dumps(data))
                try:
                    with patch('dassuite.sync._sync_locked_module', side_effect=AssertionError('invalid catalog consumed')):
                        ok, results, issues = sync_modules(suite_root=self.root, tier='oss', lock_path=alternate)
                    self.assertFalse(ok)
                    self.assertEqual(results, [])
                    self.assertTrue(any(name in issue for issue in issues), issues)
                    self.assertFalse((self.root / 'src').exists())
                finally:
                    path.write_bytes(original)



if __name__ == "__main__":
    unittest.main()
