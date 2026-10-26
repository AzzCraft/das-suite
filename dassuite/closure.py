from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

from .core import (
    Module,
    git_output,
    is_git_repo,
    load_manifest,
    module_path,
    normalize_git_uri,
    parse_modules,
    read_suite_version,
    run,
)
from .locks import (
    LOCAL_STATES,
    OVERRIDE_POLICIES,
    closure_path,
    load_suite_lock,
    load_suite_version_closure,
    load_tooling_lock,
    suite_lock_path,
    tooling_lock_path,
    validate_structured_ref_dict,
)
from .refs import path_present, resolve_structured_ref_path


REQUIRED_CLOSURE_REF_TO_MODULE = {
    "standard": "das-standard",
    "factory": "dasops",
    "verifyRuntime": "cadence-oss",
}

OPTIONAL_CLOSURE_REF_TO_MODULE: dict[str, str] = {}

ZERO_PIN = "0000000000000000000000000000000000000000"
CANONICAL_SCHEMA_FILES = {
    "suite_lock.json": "schemas/das-standard/suite_lock.schema.json",
    "suite_version_closure.json": "schemas/das-standard/suite_version_closure.schema.json",
    "suite.tooling_lock.json": "schemas/das-standard/tooling_lock.schema.json",
    "modules.yaml": "schemas/modules.schema.json",
    "profiles/oss.json": "schemas/oss_profile.schema.json",
}


def _validate_canonical_schemas(
    suite_root: Path, *, document_paths: dict[str, Path] | None = None,
) -> list[str]:
    """Validate all released closure inputs with the selected checked-in Standard schemas."""
    import yaml

    issues: list[str] = []
    checker = FormatChecker()
    if "date-time" not in checker.checkers:
        return ["missing date-time validator; install the declared rfc3339-validator dependency"]
    for document_name, schema_name in CANONICAL_SCHEMA_FILES.items():
        if document_paths is not None and document_name not in document_paths:
            continue
        document_path = document_paths[document_name] if document_paths is not None else suite_root / document_name
        schema_path = suite_root / schema_name
        if not schema_path.is_file():
            issues.append(f"missing canonical schema: {schema_name}")
            continue
        try:
            text = document_path.read_text(encoding="utf-8")
            document = json.loads(text) if document_path.suffix == ".json" else yaml.safe_load(text)
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            errors = sorted(Draft202012Validator(schema, registry=Registry(), format_checker=checker).iter_errors(document), key=lambda item: tuple(str(part) for part in item.path))
            for error in errors:
                location = ".".join(str(item) for item in error.path) or "root"
                issues.append(f"{document_name} canonical schema {location}: {error.message}")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            issues.append(f"{document_name} canonical schema validation failed: {exc}")
    return issues


def _release_version(value):
    import re
    # Compare the same beta/rc/a release without dropping prerelease identity.
    return re.sub(r"-(alpha|beta|rc)\.([0-9]+)$", lambda m: {"alpha":"a","beta":"b","rc":"rc"}[m[1]]+m[2], value)


def validate_version_semantics(suite_root: Path, *, workspace: bool = False) -> list[str]:
    import re
    issues=[]
    try:
        version=(suite_root/"VERSION").read_text().strip()
        package=re.search(r'^version\s*=\s*"([^"\n]+)"', (suite_root/"pyproject.toml").read_text(),re.M)
        module=re.search(r'__version__\s*=\s*"([^"\n]+)"', (suite_root/"dassuite/__init__.py").read_text())
        if not package or not module or any(_release_version(v)!=_release_version(version) for v in (package[1],module[1])):
            issues.append("Suite VERSION, Python package and __version__ differ")
        closure=load_suite_version_closure(suite_root);tooling=load_tooling_lock(suite_root)
        if closure["refs"]["suite"]["pinValue"]!=version or tooling["sourceOfTruth"]["pinValue"]!=version:
            issues.append("Suite self references differ from VERSION")
        if workspace:
            manifest=load_manifest(suite_root)
            for module in parse_modules(manifest):
                path=_observed_module_path(suite_root,module,load_suite_lock(suite_root)["modules"].get(module.id))
                if not path.is_dir():
                    issues.append("missing module source: "+module.id);continue
                actual=(path/"VERSION").read_text().strip()
                key={"das-standard":"standardVersion","dasops":"factoryVersion","cadence-oss":"verifyRuntimeVersion"}[module.id]
                if _release_version(actual)!=_release_version(closure[key]):issues.append(module.id+" VERSION differs from closure")
                if module.id=="das-standard":
                    standard=json.loads((path/"standard_manifest.json").read_text())
                    if standard["standardVersion"]!=closure["standardManifestVersion"]:issues.append("Standard manifest edition differs")
                    for name in ("suite_lock","suite_version_closure","tooling_lock"):
                        if (path/(name+".schema.json")).read_bytes()!=(suite_root/"schemas/das-standard"/(name+".schema.json")).read_bytes():
                            issues.append("Standard schema source differs: "+name)
    except (OSError,ValueError,KeyError,TypeError) as exc:issues.append("version semantics: "+str(exc))
    return issues


def _is_placeholder_uri(uri: str) -> bool:
    """Detect placeholder URIs that are not actionable."""
    return "<ORG>" in uri or "<org>" in uri or "<PLACEHOLDER>" in uri


def _is_zero_pin(pin_value: str) -> bool:
    """Detect zero commit pins that are not actionable."""
    return pin_value == ZERO_PIN


def _compare_ref_dicts(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("kind") == right.get("kind")
        and left.get("pinType") == right.get("pinType")
        and left.get("pinValue") == right.get("pinValue")
        and bool(left.get("floating")) is False
        and bool(right.get("floating")) is False
        and (
            normalize_git_uri(str(left.get("uri", "")))
            if left.get("kind") == "git"
            else str(left.get("uri", ""))
        )
        == (
            normalize_git_uri(str(right.get("uri", "")))
            if right.get("kind") == "git"
            else str(right.get("uri", ""))
        )
    )


def _resolve_relative_file_ref(suite_root: Path, ref: dict[str, Any]) -> Path | None:
    if ref.get("kind") != "file":
        return None
    return resolve_structured_ref_path(suite_root, ref)


def _repo_dirty(path: Path) -> bool:
    return bool(git_output(path, "status", "--porcelain"))


def _module_dirty(path: Path) -> bool:
    """Check if *only* the module directory has uncommitted changes.

    Unlike ``_repo_dirty`` (which reports the whole repo), this scopes the
    check to files under *path* so that unrelated changes in sibling
    directories of a monorepo do not flag the module as dirty.
    """
    return bool(git_output(path, "status", "--porcelain", "--", str(path)))


def _expected_git_head(path: Path, ref: dict[str, Any]) -> str:
    pin_type = ref.get("pinType")
    pin_value = str(ref.get("pinValue", ""))
    if pin_type == "commit":
        return pin_value
    if pin_type == "tag":
        return git_output(path, "rev-list", "-n", "1", pin_value)
    return ""


def _validate_actionable_local_ref(
    suite_root: Path,
    ref: dict[str, Any],
    *,
    context: str,
) -> list[str]:
    resolved = resolve_structured_ref_path(suite_root, ref)
    if resolved is None:
        return []

    issues: list[str] = []
    if not resolved.exists():
        return [f"{context} local path does not exist: {resolved}"]

    kind = str(ref.get("kind", ""))
    pin_type = str(ref.get("pinType", ""))
    pin_value = str(ref.get("pinValue", ""))

    if kind == "git":
        if not is_git_repo(resolved):
            issues.append(f"{context} local path is not a git repository: {resolved}")
            return issues
        rev = pin_value if pin_type == "commit" else f"refs/tags/{pin_value}"
        if not git_output(resolved, "rev-parse", f"{rev}^{{commit}}"):
            issues.append(f"{context} pinValue does not resolve in local git repo: {pin_value}")
        return issues

    if kind == "custom" and pin_type == "version":
        version_file = resolved / "VERSION"
        if not version_file.exists():
            issues.append(f"{context} local module is missing VERSION: {resolved}")
            return issues
        local_version = version_file.read_text(encoding="utf-8").strip()
        if local_version != pin_value:
            issues.append(
                f"{context} local VERSION {local_version} does not match pinValue {pin_value}"
            )
        return issues

    if kind == "file" and not resolved.is_file():
        issues.append(f"{context} must resolve to a file: {resolved}")

    return issues


def _observed_module_path(
    suite_root: Path,
    module: Module,
    lock_entry: dict[str, Any] | None,
) -> Path:
    locked_ref = lock_entry.get("lockedRef") if isinstance(lock_entry, dict) else None
    resolved = resolve_structured_ref_path(suite_root, locked_ref)
    return resolved if resolved is not None else module_path(suite_root, module)


def validate_sync_lock(suite_root: Path, suite_lock: dict[str, Any], modules: list[Module]) -> list[str]:
    """Check the selected lock's semantics after its Standard schema passes."""
    issues: list[str] = []
    modules_by_id = {module.id: module for module in modules}
    suite_version = read_suite_version(suite_root)
    for key in (
        "schemaVersion",
        "suiteId",
        "suiteVersion",
        "standardManifestVersion",
        "syncMode",
        "allowCloneTip",
        "defaultOverridePolicy",
        "modules",
    ):
        if suite_lock.get(key) in (None, ""):
            issues.append(f"suite_lock.json missing {key}")

    if suite_lock.get("suiteVersion") != suite_version:
        issues.append("suite_lock.json suiteVersion must match VERSION/pyproject version")
    if suite_lock.get("allowCloneTip") is not False:
        issues.append("suite_lock.json allowCloneTip must be false")
    if suite_lock.get("defaultOverridePolicy") not in OVERRIDE_POLICIES:
        issues.append("suite_lock.json defaultOverridePolicy is invalid")

    lock_modules = suite_lock.get("modules", {})
    if not isinstance(lock_modules, dict):
        issues.append("suite_lock.json modules must be an object")
    else:
        for module_id in ("das-standard", "dasops", "cadence-oss"):
            if module_id not in lock_modules:
                issues.append(f"suite_lock.json modules must include {module_id}")

        for module in modules:
            if module.closure_ref is None:
                continue
            entry = lock_modules.get(module.id)
            if entry is None:
                issues.append(f"suite_lock.json missing lock entry for {module.id}")
                continue
            locked_ref = entry.get("lockedRef")
            issues.extend(
                validate_structured_ref_dict(
                    locked_ref,
                    f"suite_lock.modules.{module.id}.lockedRef",
                )
            )
            for dependency in entry.get("dependsOn", []):
                if dependency not in modules_by_id or not isinstance(lock_modules.get(dependency, {}).get("lockedRef"), dict):
                    issues.append(f"suite_lock.modules.{module.id}.dependsOn: unknown or unlocked dependency {dependency}")
            if isinstance(locked_ref, dict):
                if locked_ref.get("kind") != "git" or locked_ref.get("pinType") != "commit" or not re.fullmatch(r"[0-9a-fA-F]{40}", str(locked_ref.get("pinValue", ""))):
                    issues.append(f"suite_lock.modules.{module.id}.lockedRef requires git/commit with a full 40-character commit id")
                if _is_placeholder_uri(str(locked_ref.get("uri", ""))):
                    issues.append(
                        f"suite_lock.modules.{module.id}.lockedRef URI contains a placeholder; must be an actionable ref"
                    )
                if _is_zero_pin(str(locked_ref.get("pinValue", ""))):
                    issues.append(
                        f"suite_lock.modules.{module.id}.lockedRef pinValue is a zero pin; must be an actionable commit or version"
                    )
                issues.extend(
                    _validate_actionable_local_ref(
                        suite_root,
                        locked_ref,
                        context=f"suite_lock.modules.{module.id}.lockedRef",
                    )
                )
            if bool(entry.get("required")) != module.required:
                issues.append(
                    f"suite_lock.json required flag for {module.id} must match modules.yaml"
                )
            if module.role and entry.get("role") != module.role:
                issues.append(
                    f"suite_lock.json role for {module.id} must match modules.yaml role"
                )
            allowed_local_state = entry.get("allowedLocalState", [])
            if not isinstance(allowed_local_state, list) or not allowed_local_state:
                issues.append(
                    f"suite_lock.modules.{module.id}.allowedLocalState must be a non-empty list"
                )
            elif any(state not in LOCAL_STATES for state in allowed_local_state):
                issues.append(
                    f"suite_lock.modules.{module.id}.allowedLocalState contains invalid values"
                )
            if entry.get("overridePolicy") not in OVERRIDE_POLICIES:
                issues.append(
                    f"suite_lock.modules.{module.id}.overridePolicy must be valid"
                )
    if set(suite_lock["modules"]) != set(modules_by_id):
        issues.append("suite_lock.json module IDs must match modules.yaml")
    return issues


def validate_closure_artifacts(suite_root: Path) -> list[str]:
    issues: list[str] = []
    suite_root = suite_root.resolve()

    issues.extend(_validate_canonical_schemas(suite_root))
    if issues:
        return issues
    issues.extend(validate_version_semantics(suite_root))

    manifest = load_manifest(suite_root)
    modules = parse_modules(manifest)
    modules_by_id = {module.id: module for module in modules}
    if len(modules_by_id) != len(modules):
        return ["modules.yaml contains duplicate module IDs"]
    seen_paths = {}
    for module in modules:
        try:
            path = module_path(suite_root, module)
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if path in seen_paths:
            issues.append(f"modules.yaml duplicate path for {seen_paths[path]} and {module.id}: {module.path}")
        seen_paths[path] = module.id
    suite_version = read_suite_version(suite_root)

    if "das-standard" not in modules_by_id:
        issues.append("modules.yaml must include das-standard")

    try:
        closure = load_suite_version_closure(suite_root)
    except FileNotFoundError:
        issues.append(f"Missing {closure_path(suite_root).name}")
        closure = None

    try:
        suite_lock = load_suite_lock(suite_root)
    except FileNotFoundError:
        issues.append(f"Missing {suite_lock_path(suite_root).name}")
        suite_lock = None

    try:
        tooling_lock = load_tooling_lock(suite_root)
    except FileNotFoundError:
        issues.append(f"Missing {tooling_lock_path(suite_root).name}")
        tooling_lock = None

    if closure is not None:
        for key in (
            "schemaVersion",
            "standardVersion",
            "standardManifestVersion",
            "suiteVersion",
            "factoryVersion",
            "verifyRuntimeVersion",
            "closureUpdatedAt",
            "suiteLockRef",
            "refs",
        ):
            if closure.get(key) in (None, ""):
                issues.append(f"suite_version_closure.json missing {key}")

        if closure.get("suiteVersion") != suite_version:
            issues.append(
                "suite_version_closure.json suiteVersion must match VERSION/pyproject version"
            )

        suite_lock_ref = closure.get("suiteLockRef")
        if suite_lock_ref is not None:
            issues.extend(validate_structured_ref_dict(suite_lock_ref, "suiteLockRef"))
            resolved = _resolve_relative_file_ref(suite_root, suite_lock_ref)
            if resolved is None or resolved != suite_lock_path(suite_root).resolve():
                issues.append("suiteLockRef must point to ./suite_lock.json")

        lock_refs = closure.get("lockRefs", {})
        if lock_refs:
            tooling_ref = lock_refs.get("suiteToolingLock")
            if tooling_ref is not None:
                issues.extend(
                    validate_structured_ref_dict(tooling_ref, "lockRefs.suiteToolingLock")
                )
                resolved = _resolve_relative_file_ref(suite_root, tooling_ref)
                if resolved is None or resolved != tooling_lock_path(suite_root).resolve():
                    issues.append("lockRefs.suiteToolingLock must point to ./suite.tooling_lock.json")

        refs = closure.get("refs", {})
        if not isinstance(refs, dict):
            issues.append("suite_version_closure.json refs must be an object")
        else:
            for ref_name, module_id in REQUIRED_CLOSURE_REF_TO_MODULE.items():
                ref = refs.get(ref_name)
                if ref is None:
                    issues.append(f"suite_version_closure.json refs.{ref_name} is required")
                    continue
                issues.extend(validate_structured_ref_dict(ref, f"refs.{ref_name}"))
                if _is_placeholder_uri(str(ref.get("uri", ""))):
                    issues.append(
                        f"refs.{ref_name} URI contains a placeholder (<ORG>); must be an actionable ref"
                    )
                if _is_zero_pin(str(ref.get("pinValue", ""))):
                    issues.append(
                        f"refs.{ref_name} pinValue is a zero pin; must be an actionable commit or version"
                    )
                module = modules_by_id.get(module_id)
                if module is not None and module.closure_ref != ref_name:
                    issues.append(
                        f"modules.yaml {module_id} closureRef must be {ref_name}"
                    )
                issues.extend(
                    _validate_actionable_local_ref(
                        suite_root,
                        ref,
                        context=f"refs.{ref_name}",
                    )
                )

            suite_ref = refs.get("suite")
            if suite_ref is None:
                issues.append("suite_version_closure.json refs.suite is required")
            else:
                issues.extend(validate_structured_ref_dict(suite_ref, "refs.suite"))
                if suite_ref.get("kind") != "custom" or suite_ref.get("uri") != "public-component://das-suite":
                    issues.append("suite_version_closure.json refs.suite must be the public das-suite version ref")

            for ref_name, module_id in OPTIONAL_CLOSURE_REF_TO_MODULE.items():
                ref = refs.get(ref_name)
                if ref is None:
                    continue
                issues.extend(validate_structured_ref_dict(ref, f"refs.{ref_name}"))
                if _is_placeholder_uri(str(ref.get("uri", ""))):
                    issues.append(
                        f"refs.{ref_name} URI contains a placeholder (<ORG>); must be an actionable ref"
                    )
                if _is_zero_pin(str(ref.get("pinValue", ""))):
                    issues.append(
                        f"refs.{ref_name} pinValue is a zero pin; must be an actionable commit or version"
                    )
                module = modules_by_id.get(module_id)
                if module is not None and module.closure_ref != ref_name:
                    issues.append(
                        f"modules.yaml {module_id} closureRef must be {ref_name}"
                    )
                issues.extend(
                    _validate_actionable_local_ref(
                        suite_root,
                        ref,
                        context=f"refs.{ref_name}",
                    )
                )

    if suite_lock is not None:
        issues.extend(validate_sync_lock(suite_root, suite_lock, modules))

    if tooling_lock is not None:
        for key in ("schemaVersion", "standardManifestVersion", "sourceOfTruth", "verifyRuntime", "toolchain"):
            if tooling_lock.get(key) in (None, ""):
                issues.append(f"suite.tooling_lock.json missing {key}")
        issues.extend(
            validate_structured_ref_dict(tooling_lock.get("sourceOfTruth"), "tooling.sourceOfTruth")
        )
        issues.extend(
            validate_structured_ref_dict(tooling_lock.get("verifyRuntime"), "tooling.verifyRuntime")
        )
        if isinstance(tooling_lock.get("sourceOfTruth"), dict):
            issues.extend(
                _validate_actionable_local_ref(
                    suite_root,
                    tooling_lock["sourceOfTruth"],
                    context="tooling.sourceOfTruth",
                )
            )
        if isinstance(tooling_lock.get("verifyRuntime"), dict):
            issues.extend(
                _validate_actionable_local_ref(
                    suite_root,
                    tooling_lock["verifyRuntime"],
                    context="tooling.verifyRuntime",
                )
            )

        for key in ("suiteVersionClosureRef", "suiteLockRef"):
            ref = tooling_lock.get(key)
            if ref is None:
                issues.append(f"suite.tooling_lock.json missing {key}")
                continue
            issues.extend(validate_structured_ref_dict(ref, f"tooling.{key}"))
            resolved = _resolve_relative_file_ref(suite_root, ref)
            expected = closure_path(suite_root).resolve() if key == "suiteVersionClosureRef" else suite_lock_path(suite_root).resolve()
            if resolved is None or resolved != expected:
                issues.append(f"suite.tooling_lock.json {key} must point to ./{expected.name}")

        toolchain = tooling_lock.get("toolchain", [])
        if not isinstance(toolchain, list) or not toolchain:
            issues.append("suite.tooling_lock.json toolchain must be a non-empty list")

    if closure is not None and suite_lock is not None:
        if closure.get("standardManifestVersion") != suite_lock.get("standardManifestVersion"):
            issues.append("suite_version_closure.json and suite_lock.json must share standardManifestVersion")

        lock_modules = suite_lock.get("modules", {})
        refs = closure.get("refs", {})
        for ref_name, module_id in REQUIRED_CLOSURE_REF_TO_MODULE.items():
            closure_ref = refs.get(ref_name)
            if module_id == "das-suite":
                lock_ref = lock_modules.get("das-suite", {}).get("lockedRef")
            else:
                lock_ref = lock_modules.get(module_id, {}).get("lockedRef")
            if closure_ref and lock_ref and not _compare_ref_dicts(closure_ref, lock_ref):
                issues.append(
                    f"suite_version_closure.json refs.{ref_name} must match suite_lock.json module {module_id}"
                )

        profile_path = suite_root / "profiles" / "oss.json"
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            catalog_ids = {module.id for module in modules}
            lock_ids = set(lock_modules)
            profile_ids = set(profile.get("required", []))
            if catalog_ids != lock_ids or catalog_ids != profile_ids:
                issues.append("modules.yaml, profiles/oss.json, and suite_lock.json must have the same module IDs")
            for module in modules:
                if profile.get("modules", {}).get(module.id) != {"tier": module.tier, "required": module.required}:
                    issues.append(f"profiles/oss.json modules.{module.id} tier/required differs from modules.yaml")
            expected_required = {m.id for m in modules if m.required}
            expected_optional = catalog_ids - expected_required
            selected = closure.get("closureProfiles", {}).get("oss-only", {})
            if set(selected.get("requiredModules", [])) != expected_required or set(selected.get("optionalModules", [])) != expected_optional:
                issues.append("suite_version_closure.json closureProfiles.oss-only required/optional modules differ from catalog")
            if set(profile.get("modules", {})) != catalog_ids:
                issues.append("profiles/oss.json modules must match modules.yaml")
        except (OSError, ValueError):
            issues.append("profiles/oss.json is unreadable")

    if closure is not None and tooling_lock is not None:
        if closure.get("standardManifestVersion") != tooling_lock.get("standardManifestVersion"):
            issues.append(
                "suite_version_closure.json and suite.tooling_lock.json must share standardManifestVersion"
            )
        refs = closure.get("refs", {})
        if refs.get("suite") and tooling_lock.get("sourceOfTruth") and not _compare_ref_dicts(
            refs["suite"], tooling_lock["sourceOfTruth"]
        ):
            issues.append("suite.tooling_lock.json sourceOfTruth must match refs.suite")
        if refs.get("verifyRuntime") and tooling_lock.get("verifyRuntime") and not _compare_ref_dicts(
            refs["verifyRuntime"], tooling_lock["verifyRuntime"]
        ):
            issues.append("suite.tooling_lock.json verifyRuntime must match refs.verifyRuntime")

    return issues


def build_status_payload(suite_root: Path, *, lock_modules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    suite_root = suite_root.resolve()
    manifest = load_manifest(suite_root)
    modules = parse_modules(manifest)

    if lock_modules is None:
        try:
            suite_lock = load_suite_lock(suite_root)
            lock_modules = suite_lock.get("modules", {})
        except FileNotFoundError:
            lock_modules = {}

    payload: list[dict[str, Any]] = []
    for module in modules:
        lock_entry = lock_modules.get(module.id)
        managed = isinstance(lock_entry, dict)
        path = _observed_module_path(suite_root, module, lock_entry)
        present = path_present(path)

        item: dict[str, Any] = {
            "id": module.id,
            "tier": module.tier,
            "required": module.required,
            "path": module.path,
            "resolvedPath": str(path),
            "role": module.role,
            "closureRef": module.closure_ref,
            "managedByLock": managed,
            "present": present,
            "state": "missing",
            "expectedUri": None,
            "expectedPinType": None,
            "expectedPinValue": None,
            "currentHead": None,
            "currentRemote": None,
        }

        if managed:
            locked_ref = lock_entry.get("lockedRef", {})
            item["expectedUri"] = locked_ref.get("uri")
            item["expectedPinType"] = locked_ref.get("pinType")
            item["expectedPinValue"] = locked_ref.get("pinValue")

        if not present:
            payload.append(item)
            continue

        if managed:
            locked_ref = lock_entry.get("lockedRef", {})
            if locked_ref.get("kind") == "custom":
                if locked_ref.get("pinType") == "version":
                    version_file = path / "VERSION"
                    if version_file.exists():
                        local_version = version_file.read_text(encoding="utf-8").strip()
                        if local_version == str(locked_ref.get("pinValue", "")):
                            item["state"] = "present_locked_match"
                        else:
                            item["state"] = "present_locked_mismatch"
                    else:
                        item["state"] = "present_locked_mismatch"
                    payload.append(item)
                    continue
                if locked_ref.get("pinType") == "path":
                    item["state"] = "present_locked_match"
                    payload.append(item)
                    continue

        if not is_git_repo(path):
            item["state"] = "present_local_override"
            payload.append(item)
            continue

        rc, top = run(["git", "rev-parse", "--show-toplevel"], cwd=path)
        if rc or not top:
            item.update(state="observation_error", error=f"{module.id}: cannot observe Git worktree root ({rc}): {top}")
            payload.append(item)
            continue
        if Path(top).resolve() != path.resolve():
            # A plain directory inside another worktree is local content, not
            # a failed Git observation and never this component's identity.
            item.update(state="present_local_override", note="module is not an independent Git worktree root")
            payload.append(item)
            continue
        observations = {}
        for name, args in (("currentHead", ["rev-parse", "HEAD"]), ("currentRemote", ["remote", "get-url", "origin"]), ("dirty", ["status", "--porcelain"])):
            rc, out = run(["git", *args], cwd=path)
            if rc:
                item.update(state="observation_error", error=f"{module.id}: git {' '.join(args)} failed ({rc}): {out}")
                break
            observations[name] = out
        if item["state"] == "observation_error":
            payload.append(item)
            continue
        item["currentHead"] = observations["currentHead"]
        item["currentRemote"] = observations["currentRemote"]
        is_dirty = bool(observations["dirty"])

        if is_dirty:
            item["state"] = "dirty"
            payload.append(item)
            continue

        if not managed:
            item["state"] = "present_local_override"
            payload.append(item)
            continue

        locked_ref = lock_entry.get("lockedRef", {})
        if locked_ref.get("kind") != "git":
            item["state"] = "present_local_override"
            payload.append(item)
            continue

        local_locked_path = resolve_structured_ref_path(suite_root, locked_ref)
        if (
            local_locked_path is None
            and normalize_git_uri(item["currentRemote"] or "") != normalize_git_uri(str(locked_ref.get("uri", "")))
        ):
            item["state"] = "present_local_override"
            payload.append(item)
            continue

        expected_head = _expected_git_head(path, locked_ref)
        if expected_head and expected_head == item["currentHead"]:
            item["state"] = "present_locked_match"
        else:
            item["state"] = "present_locked_mismatch"
        payload.append(item)

    return payload


def generate_suite_version_closure(suite_root: Path) -> dict[str, Any]:
    """Generate a suite_version_closure.json from current repo states.

    Reads VERSION files and git HEADs from each configured module to produce
    a machine-readable closure document suitable for deterministic verification.
    """
    manifest = load_manifest(suite_root)
    modules = parse_modules(manifest)
    suite_version = read_suite_version(suite_root)

    component_versions: dict[str, str] = {}
    for mod in modules:
        mod_path = module_path(suite_root, mod)
        version_file = mod_path / "VERSION"
        if version_file.exists():
            component_versions[mod.id] = version_file.read_text().strip()
        elif mod_path.exists() and is_git_repo(mod_path):
            head = git_output(mod_path, "rev-parse", "HEAD").strip()
            component_versions[mod.id] = head[:12]

    standard_version = component_versions.get("das-standard", "unknown")

    closure: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "suiteVersion": suite_version,
        "standardVersion": standard_version,
        "componentVersions": component_versions,
    }

    canonical = json.dumps(closure, sort_keys=True, separators=(",", ":"))
    closure["suiteDigest"] = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

    return closure
