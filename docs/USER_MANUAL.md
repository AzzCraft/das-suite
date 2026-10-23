# DAS Suite user manual

DAS Suite 1.2.0-beta.1 is a source candidate for a fixed Standard 1.5 OSS combination. Component versions and source commits are independent. A source pin does not assert a published tag or installed runtime.

## Commands

- `python3 -m dassuite closure check` validates the three lock views.
- `python3 -m dassuite status [--json]` reports local module state.
- `python3 -m dassuite report [--json]` summarizes closure and module state.
- `python3 -m dassuite sync` clones/checks out the exact public pins.
- `bash ./scripts/verify` runs every test and content check shipped here.

The module destination is always below `src/`. Existing dirty repositories,
wrong remotes, floating refs, missing pins, and non-40-character commit IDs are
errors. The initial release has no GUI, rollback command, clean-room bundle
builder, commercial profile, or private-module integration.

Use `report --workspace --json` to enforce actual module states. Install the Suite CLI and provide its complete workspace separately. DASOps creates projects from that workspace; Suite does not create them itself.
