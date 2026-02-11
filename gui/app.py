from __future__ import annotations

from pathlib import Path

import streamlit as st

from dassuite.core import (
    effective_git_base,
    load_manifest,
    module_present,
    parse_modules,
    run_dasleafer_create,
    run_dasops_create,
    should_include,
    suite_root_from,
    sync_one_module,
)


def main() -> None:
    st.set_page_config(page_title="DAS Suite", layout="wide")

    st.title("DAS Suite")
    st.write("Workspace manager for DAS toolchain modules and product scaffolding.")

    suite_root = suite_root_from(Path.cwd())
    manifest = load_manifest(suite_root)
    modules = parse_modules(manifest)

    st.header("1) Modules")

    col_a, col_b, col_c = st.columns([1, 1, 2])

    with col_a:
        tier = st.selectbox("Tier", options=["oss", "pro", "enterprise", "all"], index=0)
    with col_b:
        use_ssh = st.checkbox("Use SSH", value=False)
    with col_c:
        git_base = st.text_input("Git base URL", value=effective_git_base(manifest, None))

    selected = []
    with st.expander("Select modules", expanded=True):
        for m in modules:
            default = should_include(m, tier)
            present = module_present(suite_root, m)
            label = f"{m.id}  [{m.tier}]" + ("  (installed)" if present else "")
            if st.checkbox(label, value=default, key=f"mod_{m.id}"):
                selected.append(m)

    if st.button("Sync selected modules"):
        results = []
        for m in selected:
            ok, msg = sync_one_module(
                suite_root=suite_root,
                module=m,
                git_base=git_base,
                use_ssh=use_ssh,
                prefer_submodules=True,
            )
            results.append((m.id, ok, msg))

        st.subheader("Sync results")
        for mid, ok, msg in results:
            st.write(f"- {mid}: {'OK' if ok else 'SKIPPED/FAILED'}")
            if msg:
                st.code(msg)

    st.divider()

    st.header("2) Create a product repo")

    template = st.selectbox("Template", options=["base", "leafer-map"], index=0)
    dest = st.text_input("Destination folder", value=str((suite_root / ".." / "product-repo").resolve()))
    change_id = st.text_input("Initial change id", value="chg-001")
    intent = st.text_input("Intent", value="Initial change")

    st.write("Upload your Master Doc and UI Spec.")
    master_doc_file = st.file_uploader("Master Doc (markdown)", type=["md", "markdown"], key="master")
    ui_spec_file = st.file_uploader("UI Spec (markdown)", type=["md", "markdown"], key="ui")

    if st.button("Create product repo"):
        if not master_doc_file or not ui_spec_file:
            st.error("Please upload both Master Doc and UI Spec.")
            return

        tmp_dir = suite_root / ".dassuite_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        master_path = tmp_dir / "master_doc.md"
        ui_path = tmp_dir / "ui_spec.md"

        master_path.write_bytes(master_doc_file.getvalue())
        ui_path.write_bytes(ui_spec_file.getvalue())

        dest_path = Path(dest).expanduser().resolve()

        if template == "base":
            rc, out = run_dasops_create(
                suite_root=suite_root,
                dest=dest_path,
                master_doc=master_path,
                ui_spec=ui_path,
                change_id=change_id,
                intent=intent,
            )
        else:
            rc, out = run_dasleafer_create(
                suite_root=suite_root,
                dest=dest_path,
                master_doc=master_path,
                ui_spec=ui_path,
                change_id=change_id,
                intent=intent,
            )

        if out:
            st.code(out)

        if rc == 0:
            st.success(f"Created: {dest_path}")
        else:
            st.error(f"Failed with exit code {rc}")


if __name__ == "__main__":
    main()
