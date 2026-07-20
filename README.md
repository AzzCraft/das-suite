# DAS Suite

DAS Suite is the exact locked closure manager for `das-standard`, `dasops`, and
`cadence-oss`. Its public v1.0 surface is deliberately limited to those three
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
`bash scripts/verify --workspace` only when the three checked-out sibling
repositories are expected to match those pins.
