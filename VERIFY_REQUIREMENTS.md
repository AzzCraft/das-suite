# Verify Requirements — das-suite

```bash
./scripts/verify
python3 -m dassuite closure check
```

## Prerequisites

- `python3` on PATH (3.10+)
- `bash` on PATH
- `git` on PATH

## What verify checks

1. Closure artifacts present and valid.
2. Module catalog schema valid.
3. Suite lock consistency.
4. Unit tests pass.
