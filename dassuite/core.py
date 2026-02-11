from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


TIER_RANK = {
    "oss": 0,
    "pro": 1,
    "enterprise": 2,
    "all": 99,
}


@dataclass(frozen=True)
class Module:
    id: str
    repo: str
    path: str
    tier: str
    required: bool


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


def is_git_repo(cwd: Path) -> bool:
    rc, out = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd)
    return rc == 0 and out.strip().lower() == "true"


def suite_root_from(cwd: Path) -> Path:
    cwd = cwd.expanduser().resolve()
    if (cwd / "modules.yaml").exists():
        return cwd
    # Walk up for safety
    for p in [cwd] + list(cwd.parents):
        if (p / "modules.yaml").exists():
            return p
    return cwd


def load_manifest(suite_root: Path) -> dict[str, Any]:
    path = suite_root / "modules.yaml"
    if not path.exists():
        raise SystemExit(f"modules.yaml not found at: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def parse_modules(manifest: dict[str, Any]) -> list[Module]:
    modules: list[Module] = []
    for m in manifest.get("modules", []) or []:
        modules.append(
            Module(
                id=str(m.get("id", "")),
                repo=str(m.get("repo", "")),
                path=str(m.get("path", "")),
                tier=str(m.get("tier", "oss")),
                required=bool(m.get("required", False)),
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
        # Fallback placeholder
        base = "https://github.com/<ORG>"

    if use_ssh:
        # Convert https://github.com/org -> git@github.com:org
        if base.startswith("https://github.com/"):
            org = base.replace("https://github.com/", "", 1).strip("/")
            return f"git@github.com:{org}/{module.repo}.git"
        if base.startswith("git@github.com:"):
            org = base.replace("git@github.com:", "", 1).strip("/")
            return f"git@github.com:{org}/{module.repo}.git"
        return f"{base}/{module.repo}.git"

    # HTTPS style
    if base.startswith("git@github.com:"):
        org = base.replace("git@github.com:", "", 1).strip("/")
        return f"https://github.com/{org}/{module.repo}.git"
    return f"{base}/{module.repo}.git"


def module_present(suite_root: Path, module: Module) -> bool:
    p = (suite_root / module.path).expanduser().resolve()
    if not p.exists():
        return False
    # Consider "present" if it contains any files.
    try:
        return any(p.iterdir())
    except Exception:
        return True


def should_include(module: Module, tier: str) -> bool:
    max_rank = TIER_RANK.get(tier, 0)
    return TIER_RANK.get(module.tier, 0) <= max_rank


def sync_one_module(
    *,
    suite_root: Path,
    module: Module,
    git_base: str,
    use_ssh: bool,
    prefer_submodules: bool,
) -> tuple[bool, str]:
    """Ensure a module is checked out.

    Returns (ok, message). "ok" means "module is present after sync".
    """
    dest = (suite_root / module.path).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    if module_present(suite_root, module):
        return True, "already present"

    url = module_url(module, git_base, use_ssh)

    # If this suite is a git repo and .gitmodules is present, try submodules first.
    if prefer_submodules and is_git_repo(suite_root) and (suite_root / ".gitmodules").exists():
        rc, out = run(
            ["git", "submodule", "update", "--init", "--recursive", module.path],
            cwd=suite_root,
        )
        if rc == 0 and module_present(suite_root, module):
            return True, "submodule updated"
        # Fall through to clone for zip-mode users.

    rc, out = run(["git", "clone", "--depth", "1", url, str(dest)], cwd=suite_root)
    if rc == 0 and module_present(suite_root, module):
        return True, "cloned"

    # Do not treat missing optional modules as fatal.
    return False, (out or "clone failed")


def run_dasops_create(
    *,
    suite_root: Path,
    dest: Path,
    master_doc: Path,
    ui_spec: Path,
    change_id: str,
    intent: str,
) -> tuple[int, str]:
    dasops_root = (suite_root / "src" / "dasops").resolve()
    if not dasops_root.exists():
        raise SystemExit("dasops module not found. Run: dassuite sync --tier oss")

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{dasops_root}{os.pathsep}{env.get('PYTHONPATH','')}".strip(os.pathsep)

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


def run_dasleafer_create(
    *,
    suite_root: Path,
    dest: Path,
    master_doc: Path,
    ui_spec: Path,
    change_id: str,
    intent: str,
) -> tuple[int, str]:
    tool_root = (suite_root / "src" / "das-leafer").resolve()
    if not tool_root.exists():
        raise SystemExit("das-leafer module not found. Run: dassuite sync --tier oss")

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{tool_root}{os.pathsep}{env.get('PYTHONPATH','')}".strip(os.pathsep)

    cmd = [
        sys.executable,
        "-m",
        "dasleafer",
        "create",
        "--factory-root",
        str(tool_root),
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


def run_vendor_cadence(
    suite_root: Path,
    product_root: Path,
    *,
    tier: str,
    force: bool,
) -> tuple[int, str, str]:
    """Vendor Cadence into a product repo using the product repo's own script.

    This keeps behavior deterministic and centrally documented in the product template.

    The product repo must contain:
      scripts/install_cadence_vendor.sh
    """
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

    return run(cmd, cwd=product_root)
