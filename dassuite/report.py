from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .closure import build_status_payload, validate_closure_artifacts, validate_version_semantics
from .core import read_suite_version


def generate_report(suite_root: Path, *, enforce_workspace: bool = False) -> dict[str, Any]:
    issues = validate_closure_artifacts(suite_root)
    modules = build_status_payload(suite_root) if not issues else []
    issues.extend(item["error"] for item in modules if item.get("error"))
    if enforce_workspace and not issues:
        issues.extend(validate_version_semantics(suite_root, workspace=True))
        lock_modules = json.loads((suite_root / "suite_lock.json").read_text(encoding="utf-8"))["modules"]
        for module in modules:
            allowed = set(lock_modules.get(module["id"], {}).get("allowedLocalState", []))
            if module["required"] and module["state"] not in allowed:
                issues.append(f"required module {module['id']} is {module['state']}; allowedLocalState={sorted(allowed)}")
    state_counts = Counter(module["state"] for module in modules)
    managed = [module["id"] for module in modules if module["managedByLock"]]
    unmanaged = [module["id"] for module in modules if not module["managedByLock"]]

    return {
        "suiteRoot": str(suite_root.resolve()),
        "suiteVersion": read_suite_version(suite_root),
        "workspaceEnforced": enforce_workspace,
        "issues": issues,
        "summary": {
            "totalModules": len(modules),
            "managedModules": len(managed),
            "unmanagedModules": len(unmanaged),
            "stateCounts": dict(sorted(state_counts.items())),
        },
        "managedModules": managed,
        "unmanagedModules": unmanaged,
        "modules": modules,
    }


def render_report_text(report: dict[str, Any]) -> str:
    lines = [
        f"Suite root: {report['suiteRoot']}",
        f"Suite version: {report['suiteVersion']}",
        f"Managed modules: {report['summary']['managedModules']}/{report['summary']['totalModules']}",
    ]

    if report["issues"]:
        lines.append("Closure issues:")
        lines.extend(f"- {issue}" for issue in report["issues"])
    else:
        lines.append("Closure issues: none")

    lines.append("Module states:")
    for module in report["modules"]:
        managed = "managed" if module["managedByLock"] else "unmanaged"
        location = module.get("resolvedPath") or module["path"]
        lines.append(f"- {module['id']}: {module['state']} ({managed}) [{location}]")

    return "\n".join(lines)
