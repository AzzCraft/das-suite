# DAS Suite

DAS Suite is the exact locked closure manager for `das-standard`, `dasops`, and
`cadence-oss`. Its public source surface is deliberately limited to those three
repositories; it contains no GUI, private tiers, product scaffolding, or
unlocked branch-tip synchronization.

Install and verify:

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install .
bash scripts/verify --full
```

Inspect the closure with `dassuite closure check`, `dassuite status`, and
`dassuite report`. `dassuite sync` is locked by default and checks out the
exact public commits named in `suite_lock.json`; a dirty or wrong-remote module
fails instead of being silently rewritten.

The three released closure artifacts are `suite_lock.json`,
`suite_version_closure.json`, and `suite.tooling_lock.json`. Use
`bash scripts/verify --workspace` only when the three synced component
repositories are expected to match those pins.

## Standard 1.5 scaffold candidate

The 1.2.0-beta.1 source candidate selects Standard 1.5 and independent
DASOps/Cadence tool versions through exact source commits. The CLI package and
complete Suite workspace are separate inputs. Run `dassuite sync --repo-root
/path/to/suite`, then `dassuite report --workspace --repo-root /path/to/suite
--json`. Managed clones live under `src/`; a failed module sync stops the
operation and does not imply cross-repository rollback.

Invoke DASOps from the selected factory to create projects; Suite does not
provide a second scaffolder. Creation needs the matching Cadence dual-lock
reader and `scaffold-v15` metadata selection. Candidate source pins are updated
when the reviewed component commits are prepared. Source preparation does not
assert a published tag, installed runtime, or full project qualification.

Suite validates its own version views in standalone mode. Workspace reporting
also compares actual component VERSION files and the Standard schema source.
SemVer `1.2.0-beta.1` and Python `1.2.0b1` represent the same release; beta.2
and the final release remain distinct.
