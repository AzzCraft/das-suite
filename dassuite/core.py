from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml


TIER_RANK = {"oss": 0}


@dataclass(frozen=True)
class Module:
    id: str
    repo: str
    path: str
    tier: str
    required: bool
    role: str | None = None
    closure_ref: str | None = None


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        return p.returncode, (p.stdout or "").strip()
    except FileNotFoundError:
        return 127, ""


def git_output(cwd: Path, *args: str) -> str:
    rc, out = run(["git", *args], cwd=cwd)
    return out.strip() if rc == 0 else ""


def is_git_repo(cwd: Path) -> bool:
    return git_output(cwd, "rev-parse", "--is-inside-work-tree").lower() == "true"


def suite_root_from(cwd: Path) -> Path:
    cwd = cwd.expanduser().resolve()
    if (cwd / "modules.yaml").exists():
        return cwd
    for path in [cwd, *cwd.parents]:
        if (path / "modules.yaml").exists():
            return path
    return cwd


def load_manifest(suite_root: Path) -> dict[str, Any]:
    path = suite_root / "modules.yaml"
    if not path.exists():
        raise SystemExit(f"modules.yaml not found at: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def parse_modules(manifest: dict[str, Any]) -> list[Module]:
    modules: list[Module] = []
    for entry in manifest.get("modules", []) or []:
        modules.append(
            Module(
                id=str(entry.get("id", "")),
                repo=str(entry.get("repo", "")),
                path=str(entry.get("path", "")),
                tier=str(entry.get("tier", "oss")),
                required=bool(entry.get("required", False)),
                role=str(entry.get("role")) if entry.get("role") is not None else None,
                closure_ref=(
                    str(entry.get("closureRef")) if entry.get("closureRef") is not None else None
                ),
            )
        )
    return modules


def effective_git_base(manifest: dict[str, Any], override: str | None) -> str:
    if override:
        return override
    env = (os.getenv("DAS_SUITE_GIT_BASE") or "").strip()
    if env:
        return env
    return str(manifest.get("defaultGitBase") or "").strip()


def module_url(module: Module, git_base: str, use_ssh: bool) -> str:
    base = (git_base or "").rstrip("/")
    if not base:
        raise ValueError(
            "No actionable git base configured. Set DAS_SUITE_GIT_BASE, pass --git-base, "
            "or use the workspace-local .gitmodules entries."
        )
    if "<" in base and ">" in base:
        raise ValueError(
            f"Configured git base contains a placeholder: {base!r}. "
            "Set DAS_SUITE_GIT_BASE or pass --git-base with a real URL."
        )

    if use_ssh:
        if base.startswith("https://github.com/"):
            org = base.replace("https://github.com/", "", 1).strip("/")
            return f"git@github.com:{org}/{module.repo}.git"
        if base.startswith("git@github.com:"):
            org = base.replace("git@github.com:", "", 1).strip("/")
            return f"git@github.com:{org}/{module.repo}.git"
        return f"{base}/{module.repo}.git"

    if base.startswith("git@github.com:"):
        org = base.replace("git@github.com:", "", 1).strip("/")
        return f"https://github.com/{org}/{module.repo}.git"
    return f"{base}/{module.repo}.git"


def normalize_git_uri(uri: str) -> str:
    value = uri.strip()
    if not value:
        return value

    if value.startswith("ssh://git@github.com/"):
        value = "https://github.com/" + value.replace("ssh://git@github.com/", "", 1)
    elif value.startswith("git@github.com:"):
        value = "https://github.com/" + value.replace("git@github.com:", "", 1)
    elif value.startswith("file://"):
        parsed = urlparse(value)
        value = str(Path(unquote(parsed.path)).expanduser().resolve())
    elif "://" not in value and value.startswith("/"):
        value = str(Path(value).expanduser().resolve())

    if value.endswith(".git"):
        value = value[:-4]
    return value.rstrip("/")


def module_path(suite_root: Path, module: Module) -> Path:
    root = suite_root.expanduser().resolve()
    candidate = (root / module.path).expanduser().resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"module {module.id!r} escapes the suite root: {module.path!r}") from exc
    return candidate


def module_present(suite_root: Path, module: Module) -> bool:
    path = module_path(suite_root, module)
    if not path.exists():
        return False
    if path.is_file():
        return True
    try:
        return any(path.iterdir())
    except OSError:
        return True


def should_include(module: Module, tier: str) -> bool:
    max_rank = TIER_RANK.get(tier, 0)
    return TIER_RANK.get(module.tier, 0) <= max_rank


def read_suite_version(suite_root: Path) -> str:
    version_file = suite_root / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()

    pyproject = suite_root / "pyproject.toml"
    if pyproject.exists():
        match = re.search(
            r'^version = "([^"]+)"',
            pyproject.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        if match:
            return match.group(1)

    return "0.0.0"


def run_dasops_create(
    *,
    suite_root: Path,
    dest: Path,
    master_doc: Path,
    ui_spec: Path,
    change_id: str,
    intent: str,
) -> tuple[int, str]:
    dasops_root = _release_sibling_tool_root(suite_root, "dasops")

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{dasops_root}{os.pathsep}{env.get('PYTHONPATH', '')}".strip(
        os.pathsep
    )

    cmd = [
        sys.executable,
        "-m",
        "dasops",
        "create",
        "--factory-root",
        str(dasops_root),
        "--dest",
        str(dest),
        "--master-doc",
        str(master_doc),
        "--ui-spec",
        str(ui_spec),
        "--change-id",
        change_id,
        "--intent",
        intent,
    ]

    return run(cmd, cwd=suite_root, env=env)


def _release_sibling_tool_root(suite_root: Path, tool_name: str) -> Path:
    suite_root = suite_root.expanduser().resolve()
    tool_root = (suite_root.parent / tool_name).resolve()
    if not tool_root.exists():
        raise SystemExit(
            f"{tool_name} module not found in release package at: {tool_root}. "
            f"Pass --repo-root as <release-root>/das-suite with sibling "
            f"<release-root>/{tool_name} present."
        )
    return tool_root


def run_vendor_cadence(
    suite_root: Path,
    product_root: Path,
    *,
    tier: str,
    force: bool,
) -> tuple[int, str, str]:
    script = (product_root / "scripts" / "install_cadence_vendor.sh").resolve()
    if not script.exists():
        return (2, "", f"Missing {script}. Is this a DAS product repo?\n")

    cmd = [
        "bash",
        str(script),
        "--from-suite",
        str(suite_root),
        "--tier",
        tier,
    ]
    if force:
        cmd.append("--force")

    rc, out = run(cmd, cwd=product_root)
    return rc, out, ""
