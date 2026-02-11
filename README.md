# DAS Suite

DAS Suite is a beginner-friendly workspace repo that organizes multiple DAS toolchain repositories:

- One "suite" repo
- A stable `src/` folder where optional modules are checked out
- A single command that pulls in everything you are allowed to access

The goal is to make it easy for an intern to set up a consistent DAS development environment without learning git submodules on day one.

## What you get

- `dassuite` CLI
  - `dassuite sync` pulls modules in, skipping repos you cannot access
  - `dassuite status` shows what is installed
  - `dassuite new-product` scaffolds a product repo using `dasops`
- Optional GUI (Streamlit)
  - Pick which modules to add
  - Create a product repo scaffold without memorizing commands

## Module tiers

Some modules may be private.

- oss
- pro
- enterprise

`dassuite sync` can be told to try only OSS, or to try everything and skip missing permissions.

## Quickstart

Clone the suite.

```bash
git clone <DAS_SUITE_REPO_URL> --recurse-submodules
cd das-suite
```

If you do not have access to every private module, cloning with `--recurse-submodules` may fail.

In that case:

```bash
git clone <DAS_SUITE_REPO_URL>
cd das-suite
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

dassuite sync --tier oss
```

To try everything you can access:

```bash
dassuite sync --tier all
```

## Suite layout

- `modules.yaml`
  - Single source of truth for module list, tiers, and checkout paths
- `src/`
  - Where modules are checked out
- `gui/`
  - Optional Streamlit GUI

## Notes

- This repository is tooling only.
- The software you build is generated into a separate product repo by `dasops`.

## Vendoring Cadence into a product repo

After you scaffold a product repo, you can vendor Cadence from your local suite checkout:

```bash
dassuite vendor-cadence --product-root ../my-product --tier oss
```
