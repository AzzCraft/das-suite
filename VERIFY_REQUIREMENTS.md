# Verify Requirements — DAS Suite

Run `bash scripts/verify --full` for the complete public release gate.
Run it from the Suite source root, using a Python environment outside that
source tree. Keep dependencies and their caches outside the source tree too.
Set `PYTHONDONTWRITEBYTECODE=1` when running Python commands directly from the
source checkout; the verifier sets it for its own children. Existing generated
cache directories are still rejected, even if Git ignores them.

The gate validates the checked-in Standard schemas, catalog/profile/lock
alignment, public CLI and closure tests, local links, source-boundary terms,
and generated cache residue. The selected Standard edition and independent
component versions come from `suite_lock.json` and `suite_version_closure.json`;
the current candidate selects Standard `1.5.0-beta.1`.

| Mode | What it establishes |
|---|---|
| Default, `--fast`, `--standalone`, `--full` | The same public Suite checks; modules may be absent. `--full` changes the reported mode, not the test selection. |
| `--workspace` | The same checks plus actual required-module states, selected component versions and schema-source agreement. |

The current catalog configures the managed checkouts as `src/das-standard`,
`src/dasops` and `src/cadence-oss`. Use `dassuite report --workspace --json`
to inspect their resolved paths and state; do not assume sibling directories.
`dassuite sync` can prepare the locked sources when those commits are available.
These checks do not install Cadence, run generated-product tests, publish a
release or establish full DAS/reference qualification.

Requirements: Python 3.10+, Bash 3.2+, Git 2.30+, and the exact packages in
`requirements.txt`.
