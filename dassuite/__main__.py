from __future__ import annotations

import argparse
import json
from pathlib import Path

from .closure import _validate_canonical_schemas, build_status_payload, validate_closure_artifacts
from .core import effective_git_base, load_manifest, suite_root_from
from .report import generate_report, render_report_text
from .sync import sync_modules


def _print_table(rows: list[list[str]]) -> None:
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for index, row in enumerate(rows):
        print("  ".join(cell.ljust(widths[pos]) for pos, cell in enumerate(row)))
        if index == 0:
            print("  ".join("-" * width for width in widths))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="dassuite",
        description="Manage the locked open-source DAS workspace.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    status = sub.add_parser("status", help="Show public module states")
    status.add_argument("--json", action="store_true")
    status.add_argument("--repo-root", default=".")

    closure = sub.add_parser("closure", help="Validate public closure artifacts")
    closure_sub = closure.add_subparsers(dest="closure_cmd", required=True)
    closure_check = closure_sub.add_parser("check")
    closure_check.add_argument("--json", action="store_true")
    closure_check.add_argument("--repo-root", default=".")

    report = sub.add_parser("report", help="Report public module and closure state")
    report.add_argument("--json", action="store_true")
    report.add_argument("--workspace", action="store_true", help="Fail on required disallowed local module states")
    report.add_argument("--repo-root", default=".")

    sync = sub.add_parser("sync", help="Clone or update public DAS modules")
    sync.add_argument("--json", action="store_true")
    sync.add_argument("--repo-root", default=".")

    args = parser.parse_args(argv)
    root = suite_root_from(Path(args.repo_root))

    # These commands otherwise read the catalog before the closure/report gate.
    if args.cmd in ("status", "sync"):
        issues = _validate_canonical_schemas(root)
        if issues:
            payload = {"issues": issues}
            if args.cmd == "status":
                payload["modules"] = []
            else:
                payload.update(locked=True, gitBase=None, results=[])
            print(json.dumps(payload, indent=2) if args.json else "\n".join("- " + issue for issue in issues))
            return 2

    if args.cmd == "status":
        payload = build_status_payload(root)
        if args.json:
            print(json.dumps({"modules": payload}, indent=2))
        else:
            rows = [["Module", "Required", "State", "Path"]]
            for item in payload:
                rows.append([
                    item["id"],
                    "yes" if item["required"] else "no",
                    item["state"],
                    item.get("resolvedPath") or item["path"],
                ])
            _print_table(rows)
        return 0

    if args.cmd == "closure" and args.closure_cmd == "check":
        issues = validate_closure_artifacts(root)
        if args.json:
            print(json.dumps({"issues": issues}, indent=2))
        elif issues:
            print("\n".join(f"- {issue}" for issue in issues))
        else:
            print("Closure artifacts: PASS")
        return 0 if not issues else 2

    if args.cmd == "report":
        payload = generate_report(root, enforce_workspace=bool(args.workspace))
        print(json.dumps(payload, indent=2) if args.json else render_report_text(payload))
        return 0 if not payload["issues"] else 2

    if args.cmd == "sync":
        manifest = load_manifest(root)
        git_base = effective_git_base(manifest, None)
        ok, results, issues = sync_modules(
            suite_root=root,
            tier="oss",
            git_base=git_base,
            use_ssh=False,
            prefer_submodules=False,
            locked=True,
        )
        payload = {"locked": True, "gitBase": git_base, "issues": issues, "results": results}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for issue in issues:
                print(f"- {issue}")
            for item in results:
                print(f"{item['id']}: {item['action']} ({item['message']})")
        return 0 if ok and not issues else 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
