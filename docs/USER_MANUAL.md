# DAS Suite user manual

DAS Suite v1.1.0 manages one OSS-only dependency closure. It preserves the
v1.0.0 public interface while pinning its dependencies to their final public
release commits, including Cadence OSS v1.1.0.

## Commands

- `python3 -m dassuite closure check` validates the three lock views.
- `python3 -m dassuite status [--json]` reports local module state.
- `python3 -m dassuite report [--json]` summarizes closure and module state.
- `python3 -m dassuite sync --locked` clones/checks out the exact public pins.
- `bash ./scripts/verify` runs every test and content check shipped here.

The module destination is always below `src/`. Existing dirty repositories,
wrong remotes, floating refs, missing pins, and non-40-character commit IDs are
errors. The initial release has no GUI, rollback command, clean-room bundle
builder, commercial profile, or private-module integration.
