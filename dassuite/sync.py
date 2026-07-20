from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .closure import build_status_payload, validate_closure_artifacts
from .core import (
    Module,
    effective_git_base,
    is_git_repo,
    load_manifest,
    module_path,
    module_present,
    module_url,
    normalize_git_uri,
    parse_modules,
    run,
    should_include,
)
from .locks import load_suite_lock
from .refs import is_offline_fixture_git_ref, resolve_structured_ref_path

OFFLINE_FIXTURE_PIN = "216f9f26569ac02ec2a1cc6945a87277ce30ae45"


_COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}")


def _exact_commit(value: object) -> str:
    pin = str(value)
    if not _COMMIT_RE.fullmatch(pin):
        raise ValueError("locked git references must use a full 40-character commit id")
    return pin


def _iter_refs(obj: Any):
    if isinstance(obj, dict):
        if any(k in obj for k in ("uri", "pinType", "pinValue", "floating", "kind")):
            yield obj
        for value in obj.values():
            yield from _iter_refs(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_refs(value)


def _load_lock_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_lock_for_closure_mode(
    *,
    suite_root: Path,
    lock_data: dict[str, Any],
    lock_path: Path | None,
    closure_mode: str | None,
    offline: bool,
) -> list[str]:
    if closure_mode is None:
        if offline:
            return ["--offline requires --closure-mode fixture"]
        return []
    issues: list[str] = []
    declared = lock_data.get("closureMode")
    if declared and declared != closure_mode:
        issues.append(f"lock closureMode {declared!r} does not match --closure-mode {closure_mode!r}")
    if closure_mode == "production" and offline:
        issues.append("production closure-mode cannot run with --offline")
    if closure_mode == "fixture":
        if lock_path is None:
            issues.append("fixture closure-mode requires --lock under tests/fixtures/closure")
        else:
            suite_resolved = suite_root.resolve()
            lock_resolved = lock_path.resolve()
            try:
                rel = lock_resolved.relative_to(suite_resolved).as_posix()
            except ValueError:
                rel = lock_path.as_posix()
            if not rel.startswith("tests/fixtures/closure/"):
                issues.append("fixture closure-mode only accepts locks under tests/fixtures/closure")
    for ref in _iter_refs(lock_data):
        uri = str(ref.get("uri", ""))
        kind = ref.get("kind")
        pin_type = ref.get("pinType")
        pin_value = str(ref.get("pinValue", ""))
        if closure_mode == "production":
            if "example.invalid" in uri or uri.startswith(("file:", "./", "../", "/")):
                issues.append(f"production closure-mode rejects non-production URI: {uri}")
            if ref.get("floating") is not False:
                issues.append(f"production closure-mode requires floating=false for {uri}")
            if kind == "git" and (pin_type != "commit" or not _COMMIT_RE.fullmatch(pin_value)):
                issues.append(f"production closure-mode requires exact commit pin for {uri}")
        if closure_mode == "fixture" and kind == "git" and not pin_value:
            issues.append(f"fixture closure-mode requires deterministic pin for {uri}")
    return issues


def _materialize_offline_fixture_repo(
    *,
    dest: Path,
    module: Module,
    locked_ref: dict[str, Any],
) -> tuple[bool, str]:
    pin_value = str(locked_ref.get("pinValue", ""))
    if pin_value != OFFLINE_FIXTURE_PIN:
        return (
            False,
            (
                f"offline fixture requires pinValue {OFFLINE_FIXTURE_PIN}, "
                f"got {pin_value!r} for module {module.id}"
            ),
        )

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    rc, out = run(["git", "init"], cwd=dest)
    if rc != 0:
        return False, out or "offline fixture git init failed"

    rc, out = run(["git", "remote", "add", "origin", str(locked_ref.get("uri", ""))], cwd=dest)
    if rc != 0:
        return False, out or "offline fixture remote setup failed"

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "das-suite",
            "GIT_AUTHOR_EMAIL": "suite@example.invalid",
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_NAME": "das-suite",
            "GIT_COMMITTER_EMAIL": "suite@example.invalid",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        }
    )
    rc, out = run(
        ["git", "commit", "--allow-empty", "-m", "dassuite locked snapshot"],
        cwd=dest,
        env=env,
    )
    if rc != 0:
        return False, out or "offline fixture commit creation failed"

    current_head = run(["git", "rev-parse", "HEAD"], cwd=dest)[1].strip()
    if current_head != pin_value:
        return (
            False,
            (
                "offline fixture commit hash drifted: "
                f"expected {pin_value}, got {current_head}"
            ),
        )

    rc, out = run(["git", "checkout", "--detach", pin_value], cwd=dest)
    if rc != 0:
        return False, out or "offline fixture detach checkout failed"
    return True, "materialized deterministic offline fixture repository"


def sync_one_module(
    *,
    suite_root: Path,
    module: Module,
    git_base: str,
    use_ssh: bool,
    prefer_submodules: bool,
) -> tuple[bool, str]:
    del prefer_submodules

    dest = module_path(suite_root, module)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if module_present(suite_root, module):
        return True, "already present"

    try:
        url = module_url(module, git_base, use_ssh)
    except ValueError as exc:
        return False, str(exc)
    rc, out = run(["git", "clone", "--", url, str(dest)], cwd=suite_root)
    if rc == 0 and module_present(suite_root, module):
        return True, "cloned"
    return False, (out or "clone failed")


def _checkout_locked_ref(repo_path: Path, locked_ref: dict[str, Any]) -> tuple[int, str]:
    pin_value = _exact_commit(locked_ref.get("pinValue", ""))
    return run(["git", "checkout", "--detach", pin_value], cwd=repo_path)


def _sync_locked_module(
    *,
    suite_root: Path,
    module: Module,
    locked_ref: dict[str, Any],
    allow_dirty: bool = False,
    closure_mode: str | None = None,
    offline: bool = False,
) -> tuple[bool, str]:
    dest = module_path(suite_root, module)
    state_map = {item["id"]: item for item in build_status_payload(suite_root)}
    state = state_map[module.id]["state"]
    local_path = resolve_structured_ref_path(suite_root, locked_ref)
    actual_path = Path(state_map[module.id].get("resolvedPath", dest))

    if local_path is not None:
        if state == "present_locked_match":
            return True, "already present at locked ref"
        if state == "missing":
            return False, f"local managed module not found at {local_path}"
        if state == "dirty":
            if allow_dirty:
                return True, "dirty but allowed by --allow-dirty-module"
            return False, "module is dirty; clean it before locked sync"
        if state == "present_local_override":
            return False, "module is a local override; locked sync will not rewrite it"
        if locked_ref.get("kind") == "custom":
            return False, "local managed module does not match locked version"
        rc, out = _checkout_locked_ref(actual_path, locked_ref)
        if rc != 0:
            return False, out or "checkout failed"
        refreshed = {item["id"]: item for item in build_status_payload(suite_root)}
        if refreshed[module.id]["state"] == "present_locked_match":
            return True, "locked ref applied"
        return False, f"locked sync left {module.id} in {refreshed[module.id]['state']}"

    if state == "present_locked_match":
        return True, "already present at locked ref"
    if state == "dirty":
        if allow_dirty:
            return True, "dirty but allowed by --allow-dirty-module"
        return False, "module is dirty; clean it before locked sync"
    if state == "present_local_override":
        return False, "module is a local override; locked sync will not rewrite it"

    dest.parent.mkdir(parents=True, exist_ok=True)
    if is_offline_fixture_git_ref(locked_ref) and not dest.exists():
        ok, message = _materialize_offline_fixture_repo(
            dest=dest,
            module=module,
            locked_ref=locked_ref,
        )
        if not ok:
            return False, message
        if closure_mode == "fixture" and offline:
            return True, message
        refreshed = {item["id"]: item for item in build_status_payload(suite_root)}
        if refreshed[module.id]["state"] == "present_locked_match":
            return True, message
        return False, f"offline fixture left {module.id} in {refreshed[module.id]['state']}"

    if not dest.exists():
        pin_value = _exact_commit(locked_ref.get("pinValue", ""))
        rc, out = run(["git", "clone", "--", str(locked_ref["uri"]), str(dest)], cwd=suite_root)
        if rc != 0:
            if is_offline_fixture_git_ref(locked_ref):
                ok, message = _materialize_offline_fixture_repo(
                    dest=dest,
                    module=module,
                    locked_ref=locked_ref,
                )
                if not ok:
                    return False, (out or "clone failed") + "\n" + message
                if closure_mode == "fixture" and offline:
                    return True, message
                refreshed = {item["id"]: item for item in build_status_payload(suite_root)}
                if refreshed[module.id]["state"] == "present_locked_match":
                    return True, message
                return False, f"offline fixture left {module.id} in {refreshed[module.id]['state']}"
            return False, out or "clone failed"
    else:
        if not is_git_repo(dest):
            return False, "existing path is not a git repository"
        current_remote = run(["git", "remote", "get-url", "origin"], cwd=dest)[1].strip()
        if current_remote and normalize_git_uri(current_remote) != normalize_git_uri(
            str(locked_ref["uri"])
        ):
            return False, "existing repo remote differs from locked ref"
        rc, out = run(["git", "fetch", "--tags", "origin"], cwd=dest)
        if rc != 0:
            if is_offline_fixture_git_ref(locked_ref):
                ok, message = _materialize_offline_fixture_repo(
                    dest=dest,
                    module=module,
                    locked_ref=locked_ref,
                )
                if not ok:
                    return False, (out or "fetch failed") + "\n" + message
                if closure_mode == "fixture" and offline:
                    return True, message
                refreshed = {item["id"]: item for item in build_status_payload(suite_root)}
                if refreshed[module.id]["state"] == "present_locked_match":
                    return True, message
                return False, f"offline fixture left {module.id} in {refreshed[module.id]['state']}"
            return False, out or "fetch failed"

    rc, out = _checkout_locked_ref(dest, locked_ref)
    if rc != 0:
        if is_offline_fixture_git_ref(locked_ref):
            ok, message = _materialize_offline_fixture_repo(
                dest=dest,
                module=module,
                locked_ref=locked_ref,
            )
            if not ok:
                return False, (out or "checkout failed") + "\n" + message
            refreshed = {item["id"]: item for item in build_status_payload(suite_root)}
            if refreshed[module.id]["state"] == "present_locked_match":
                return True, message
            return False, f"offline fixture left {module.id} in {refreshed[module.id]['state']}"
        return False, out or "checkout failed"

    refreshed = {item["id"]: item for item in build_status_payload(suite_root)}
    if refreshed[module.id]["state"] == "present_locked_match":
        return True, "locked ref applied"
    return False, f"locked sync left {module.id} in {refreshed[module.id]['state']}"


def sync_modules(
    *,
    suite_root: Path,
    tier: str,
    git_base: str | None = None,
    use_ssh: bool = False,
    prefer_submodules: bool = True,
    locked: bool = True,
    allow_dirty_modules: list[str] | None = None,
    closure_mode: str | None = None,
    lock_path: Path | None = None,
    offline: bool = False,
) -> tuple[bool, list[dict[str, Any]], list[str]]:
    manifest = load_manifest(suite_root)
    modules = [module for module in parse_modules(manifest) if should_include(module, tier)]

    issues: list[str] = []
    lock_modules: dict[str, Any] = {}
    if closure_mode or lock_path is not None:
        locked = True
    if locked:
        lock_data: dict[str, Any]
        resolved_lock_path: Path | None = None
        if lock_path is not None:
            resolved_lock_path = lock_path if lock_path.is_absolute() else suite_root / lock_path
            lock_data = _load_lock_file(resolved_lock_path)
        else:
            issues = validate_closure_artifacts(suite_root)
            if issues:
                return False, [], issues
            lock_data = load_suite_lock(suite_root)
        issues.extend(
            _validate_lock_for_closure_mode(
                suite_root=suite_root,
                lock_data=lock_data,
                lock_path=resolved_lock_path,
                closure_mode=closure_mode,
                offline=offline,
            )
        )
        if issues:
            return False, [], issues
        lock_modules = lock_data.get("modules", {})

    results: list[dict[str, Any]] = []
    ok_required = True
    effective_base = effective_git_base(manifest, git_base)

    for module in modules:
        if locked:
            entry = lock_modules.get(module.id)
            if not entry:
                results.append(
                    {
                        "id": module.id,
                        "path": module.path,
                        "tier": module.tier,
                        "required": module.required,
                        "ok": not module.required,
                        "action": "skipped_unmanaged",
                        "message": "module is not part of the locked closure for this tranche",
                    }
                )
                if module.required:
                    ok_required = False
                continue
            ok, message = _sync_locked_module(
                suite_root=suite_root,
                module=module,
                locked_ref=entry["lockedRef"],
                allow_dirty=module.id in (allow_dirty_modules or []),
                closure_mode=closure_mode,
                offline=offline,
            )
            action = "locked_sync"
        else:
            ok, message = sync_one_module(
                suite_root=suite_root,
                module=module,
                git_base=effective_base,
                use_ssh=use_ssh,
                prefer_submodules=prefer_submodules,
            )
            action = "best_effort_sync"

        if module.required and not ok:
            ok_required = False

        results.append(
            {
                "id": module.id,
                "path": module.path,
                "tier": module.tier,
                "required": module.required,
                "ok": ok,
                "action": action,
                "message": message,
            }
        )

    return ok_required, results, issues
