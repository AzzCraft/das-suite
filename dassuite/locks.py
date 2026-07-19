from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import normalize_git_uri


STRUCTURED_REF_KINDS = {
    "git",
    "container_image",
    "package",
    "file",
    "ci_workflow",
    "custom",
}
PIN_TYPES = {"commit", "tag", "digest", "version", "path", "manifest"}
LOCAL_STATES = {
    "missing",
    "present_locked_match",
    "present_locked_mismatch",
    "present_local_override",
    "dirty",
}
OVERRIDE_POLICIES = {"forbidden", "explicit_ticket_required", "allowed_for_local_only"}
SUITE_VERSION_CLOSURE_FILE = "suite_version_closure.json"
SUITE_LOCK_FILE = "suite_lock.json"
SUITE_TOOLING_LOCK_FILE = "suite.tooling_lock.json"


@dataclass(frozen=True)
class StructuredRef:
    kind: str
    uri: str
    pin_type: str
    pin_value: str
    floating: bool = False
    notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "uri": self.uri,
            "pinType": self.pin_type,
            "pinValue": self.pin_value,
            "floating": self.floating,
        }
        if self.notes:
            payload["notes"] = self.notes
        return payload

    def normalized_uri(self) -> str:
        if self.kind == "git":
            return normalize_git_uri(self.uri)
        return self.uri


def load_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_structured_ref_dict(data: Any, context: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(data, dict):
        return [f"{context} must be an object"]

    for field in ("kind", "uri", "pinType", "pinValue"):
        if not data.get(field):
            issues.append(f"{context}.{field} is required")

    kind = data.get("kind")
    pin_type = data.get("pinType")
    floating = data.get("floating")

    if kind and kind not in STRUCTURED_REF_KINDS:
        issues.append(f"{context}.kind must be one of {sorted(STRUCTURED_REF_KINDS)}")
    if pin_type and pin_type not in PIN_TYPES:
        issues.append(f"{context}.pinType must be one of {sorted(PIN_TYPES)}")
    if floating is not False:
        issues.append(f"{context}.floating must be false")

    if kind == "git" and pin_type not in {"commit", "tag"}:
        issues.append(f"{context}.pinType must be commit or tag for git refs")
    if kind == "container_image" and pin_type != "digest":
        issues.append(f"{context}.pinType must be digest for container_image refs")
    if kind == "package" and pin_type != "version":
        issues.append(f"{context}.pinType must be version for package refs")
    if kind in {"file", "ci_workflow"} and pin_type not in {"path", "manifest"}:
        issues.append(f"{context}.pinType must be path or manifest for {kind} refs")
    if kind == "custom" and pin_type not in {"path", "version"}:
        issues.append(f"{context}.pinType must be path or version for custom refs")

    return issues


def structured_ref_from_dict(data: Any, context: str) -> StructuredRef:
    issues = validate_structured_ref_dict(data, context)
    if issues:
        raise ValueError("; ".join(issues))
    return StructuredRef(
        kind=str(data["kind"]),
        uri=str(data["uri"]),
        pin_type=str(data["pinType"]),
        pin_value=str(data["pinValue"]),
        floating=bool(data.get("floating", False)),
        notes=str(data["notes"]) if data.get("notes") is not None else None,
    )


def closure_path(suite_root: Path) -> Path:
    return suite_root / SUITE_VERSION_CLOSURE_FILE


def suite_lock_path(suite_root: Path) -> Path:
    return suite_root / SUITE_LOCK_FILE


def tooling_lock_path(suite_root: Path) -> Path:
    return suite_root / SUITE_TOOLING_LOCK_FILE


def load_suite_version_closure(suite_root: Path) -> dict[str, Any]:
    return load_json_file(closure_path(suite_root))


def load_suite_lock(suite_root: Path) -> dict[str, Any]:
    return load_json_file(suite_lock_path(suite_root))


def load_tooling_lock(suite_root: Path) -> dict[str, Any]:
    return load_json_file(tooling_lock_path(suite_root))
