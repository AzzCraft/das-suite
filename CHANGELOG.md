# Changelog

## 1.1.0 - 2026-07-20

- Advanced the Cadence OSS verification-runtime lock to the final public
  v1.1.0 tag-target commit, including the expanded public Cadence bundle.
- No DAS Suite API, schema, or supported-module changes from v1.0.1.

## 1.0.1 - 2026-07-20

- Re-pinned the public OSS closure to the final public DASOps and Cadence
  v1.0.0 tag-target commits instead of pull-request-head commits.
- No API, schema, or supported-module changes from v1.0.0.

## 1.0.0 - 2026-07-20

- Initial public OSS-only exact closure for das-standard, dasops, and cadence-oss.
- Added canonical schema validation, locked-default synchronization, closure
  status/report commands, and standalone/workspace verification.
- Supports Python 3.10+; the v1.0 public release intentionally excludes the
  prototype GUI, private tiers, and product-scaffolding commands.

## 1.2.0-beta.1 (source candidate)

- Support a fixed Standard 1.5 scaffold combination and validate real component
  and Python/SemVer identities in the existing closure/report entry points.
- Keep managed component clones separate from Suite source and move release
  verification environments outside the source tree.
- This candidate does not publish a tag or certify a product deployment.
