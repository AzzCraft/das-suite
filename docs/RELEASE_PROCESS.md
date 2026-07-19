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
7. The release owner reviews the generated SBOM, provenance, checksums, and source archive.

## Release Steps

Remote publication is a separate, authorized operation. The release owner must use
the repository's protected review path, create a signed `v<version>` tag from the
reviewed commit, publish the verified artifacts, and verify the resulting GitHub
release. The local preparation workflow does not push, tag, or publish.

## Version Closure

Each release includes `suite_version_closure.json`, which records the public
component versions and immutable commits needed to reproduce the suite boundary.

## Rollback

Rollback is an owner-controlled release operation: restore the previous signed tag
and its matching closure as one unit. Never mix a prior suite version with newer
component pins.
