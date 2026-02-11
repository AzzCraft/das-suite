from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import (
    effective_git_base,
    load_manifest,
    module_present,
    parse_modules,
    run_dasleafer_create,
    run_dasops_create,
    run_vendor_cadence,
    should_include,
    suite_root_from,
    sync_one_module,
)


def _print_table(rows: list[list[str]]) -> None:
    widths = [0 for _ in rows[0]]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))

    def fmt(r: list[str]) -> str:
        return "  ".join(r[i].ljust(widths[i]) for i in range(len(r)))

    for idx, r in enumerate(rows):
        print(fmt(r))
        if idx == 0:
            print(fmt(["-" * w for w in widths]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dassuite", description="DAS Suite workspace manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Show which modules are present")
    p_status.add_argument("--json", action="store_true")
    p_status.add_argument("--repo-root", default=".")

    p_sync = sub.add_parser("sync", help="Sync modules (submodules/clones), skipping repos you cannot access")
    p_sync.add_argument("--tier", default="oss", choices=["oss", "pro", "enterprise", "all"], help="Maximum tier to try")
    p_sync.add_argument("--git-base", default=None, help="Override git base URL")
    p_sync.add_argument("--ssh", action="store_true", help="Use SSH URLs")
    p_sync.add_argument("--no-submodules", action="store_true", help="Do not use git submodule update (force clone mode)")
    p_sync.add_argument("--json", action="store_true")
    p_sync.add_argument("--repo-root", default=".")

    p_new = sub.add_parser("new-product", help="Create a new product repo scaffold")
    p_new.add_argument("--template", default="base", choices=["base", "leafer-map"], help="Scaffold template")
    p_new.add_argument("--dest", required=True, help="Destination directory for the new product repo")
    p_new.add_argument("--master-doc", required=True, help="Path to Master Doc")
    p_new.add_argument("--ui-spec", required=True, help="Path to UI Spec")
    p_new.add_argument("--change-id", default="chg-001", help="Initial change id folder")
    p_new.add_argument("--intent", default="Initial change", help="Intent for the initial change")
    p_new.add_argument("--repo-root", default=".")

    p_vendor = sub.add_parser("vendor-cadence", help="Vendor Cadence into an existing product repo")
    p_vendor.add_argument("--product-root", required=True, help="Path to the product repo")
    p_vendor.add_argument("--tier", default="oss", choices=["oss", "pro", "enterprise"], help="Cadence tier to vendor")
    p_vendor.add_argument("--force", action="store_true", help="Overwrite existing vendor/cadence")
    p_vendor.add_argument("--repo-root", default=".")

    p_gui = sub.add_parser("gui", help="Launch the optional Streamlit GUI")
    p_gui.add_argument("--repo-root", default=".")

    args = parser.parse_args(argv)

    suite_root = suite_root_from(Path(args.repo_root))

    if args.cmd == "status":
        manifest = load_manifest(suite_root)
        modules = parse_modules(manifest)

        payload = []
        for m in modules:
            payload.append(
                {
                    "id": m.id,
                    "tier": m.tier,
                    "required": m.required,
                    "path": m.path,
                    "present": module_present(suite_root, m),
                }
            )

        if args.json:
            print(json.dumps({"modules": payload}, indent=2))
            return 0

        rows = [["Module", "Tier", "Required", "Present", "Path"]]
        for p in payload:
            rows.append(
                [
                    p["id"],
                    p["tier"],
                    "yes" if p["required"] else "no",
                    "yes" if p["present"] else "no",
                    p["path"],
                ]
            )
        _print_table(rows)
        return 0

    if args.cmd == "sync":
        manifest = load_manifest(suite_root)
        git_base = effective_git_base(manifest, args.git_base)
        modules = parse_modules(manifest)

        selected = [m for m in modules if should_include(m, args.tier)]

        results = []
        ok_required = True
        for m in selected:
            ok, msg = sync_one_module(
                suite_root=suite_root,
                module=m,
                git_base=git_base,
                use_ssh=bool(args.ssh),
                prefer_submodules=(not args.no_submodules),
            )
            if m.required and not ok:
                ok_required = False

            results.append(
                {
                    "id": m.id,
                    "path": m.path,
                    "tier": m.tier,
                    "required": m.required,
                    "ok": ok,
                    "message": msg,
                }
            )

        if args.json:
            print(json.dumps({"gitBase": git_base, "results": results}, indent=2))
        else:
            rows = [["Module", "Tier", "OK", "Message"]]
            for r in results:
                rows.append(
                    [
                        r["id"],
                        r["tier"],
                        "yes" if r["ok"] else "no",
                        (r["message"] or "").splitlines()[-1][:80],
                    ]
                )
            _print_table(rows)

            if not ok_required:
                print("\nOne or more required modules failed to sync.")
                print("- Check your network and git credentials")
                print("- Or run: dassuite sync --tier oss --ssh")

        return 0 if ok_required else 2

    if args.cmd == "new-product":
        dest = Path(args.dest).expanduser().resolve()
        master_doc = Path(args.master_doc).expanduser().resolve()
        ui_spec = Path(args.ui_spec).expanduser().resolve()

        if args.template == "base":
            rc, out = run_dasops_create(
                suite_root=suite_root,
                dest=dest,
                master_doc=master_doc,
                ui_spec=ui_spec,
                change_id=args.change_id,
                intent=args.intent,
            )
        else:
            rc, out = run_dasleafer_create(
                suite_root=suite_root,
                dest=dest,
                master_doc=master_doc,
                ui_spec=ui_spec,
                change_id=args.change_id,
                intent=args.intent,
            )

        if out:
            print(out)

        if rc == 0:
            print("\nCreated product repo:")
            print(f"- {dest}")

        return rc

    if args.cmd == "vendor-cadence":
        product_root = Path(args.product_root).expanduser().resolve()
        rc, stdout, stderr = run_vendor_cadence(
            suite_root=suite_root,
            product_root=product_root,
            tier=args.tier,
            force=args.force,
        )
        if stdout:
            print(stdout)
        if stderr:
            print(stderr)
        return rc

    if args.cmd == "gui":
        # Lazy import so Streamlit is optional.
        try:
            import streamlit.web.cli as stcli
        except Exception:
            print("Streamlit is not installed.")
            print("Install GUI deps:\n  pip install -r requirements-gui.txt")
            return 2

        app = suite_root / "gui" / "app.py"
        if not app.exists():
            print(f"GUI not found: {app}")
            return 2

        sys.argv = ["streamlit", "run", str(app)]
        stcli.main()
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
