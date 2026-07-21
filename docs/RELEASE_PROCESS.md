# Release Process

## Overview

This document describes the public source release process for `das-suite`.

## Pre-Release Checklist

1. `suite_lock.json` pins the exact public commits of `das-standard`, `dasops`, and `cadence-oss`.
2. `suite_version_closure.json` and `suite.tooling_lock.json` agree with that lock.
3. `VERSION` matches the intended release version.
4. `CHANGELOG.md` contains the release entry.
5. `bash scripts/verify` passes from a source tree without sibling repositories.
6. `bash scripts/verify --workspace` passes in the prepared four-repository workspace.
7. The release owner reviews the generated source inventory, any separately
   required dependency/license SBOM, provenance, checksums, and source archive.

## Release Steps

Remote publication is a separate, authorized operation. The release owner must use
the repository's protected review path to create a signed immutable `v<version>`
tag from the reviewed commit, validate that exact tag, publish the verified
artifacts, and verify the resulting GitHub release. The local preparation
workflow does not push, tag, or publish.

## CI Release Validation Evidence

`.github/workflows/release.yml` is manual-dispatch only. The dispatch must run
the workflow itself from the default branch and name the exact annotated `v*`
tag in `ref`; the workflow resolves the tag, its tag object, target commit,
and `VERSION` before it runs target code. This permits validation of a
historical tag without moving it, while preventing an unprotected tag from
receiving attestation permissions: tag-source validation has read-only
contents permission only, and a separate fresh trusted job re-resolves the
same tag and rebuilds/extracts the source archive without executing tag code.
It runs
the standalone public verification gate, `pip-audit` against the pinned
verification lock, and Bandit against `dassuite`. It then emits a deterministic
`git archive` source tarball (`gzip -n`), a deterministic SPDX 2.3 source
inventory from the exact commit (tracked-file checksums and a
package-verification code), a canonical tag-object/commit/version manifest, and
`SHA256SUMS`.

Those files are retained as an Actions artifact; the independently rebuilt
source archive is covered by a GitHub artifact provenance attestation. The
workflow has no repository write permission and does not publish a tag or
GitHub Release. The generated source inventory records
source-tree identity only; it is not a third-party dependency or
license-compliance SBOM and cannot by itself satisfy a release-truth-pack
dependency or license SBOM requirement. A release owner must still verify the
checksums and attestation and perform publication as a separate authorized
action.

The workflow verifies an annotated tag and its target commit, not an approved
GPG, SSH, or Sigstore signer. Cryptographic tag verification remains a
protected publication gate until the release owner publishes the signer trust
policy and verification evidence.

## Version Closure

Each release includes `suite_version_closure.json`, which records the public
component versions and immutable commits needed to reproduce the suite boundary.

## Rollback

Rollback is an owner-controlled release operation: restore the previous signed tag
and its matching closure as one unit. Never mix a prior suite version with newer
component pins.
