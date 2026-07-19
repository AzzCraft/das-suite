# DAS Suite

DAS Suite is the open-source workspace and exact-version closure manager for
three public repositories:

- `das-standard`
- `dasops`
- `cadence-oss`

The lock files contain only public GitHub URLs and immutable 40-character
commit IDs. DAS Suite does not contain or describe commercial/private modules
in this public v1 boundary.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
```

## Check the closure

```bash
python3 -m dassuite closure check
python3 -m dassuite status
python3 -m dassuite report
```

## Sync exact public commits

```bash
python3 -m dassuite sync --locked
```

Locked sync clones only the three modules above and checks out the exact commits
in `suite_lock.json`. An existing dirty or mismatched checkout fails closed.

## Verification

```bash
bash ./scripts/verify
```

Use `bash ./scripts/verify --workspace` only in a release workspace that has
the three sibling public repositories; it additionally checks their current
Git commits against the lock.
