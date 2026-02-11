# DAS Suite User Manual

This manual explains how to use DAS Suite to pull in the DAS toolchain repos and scaffold a new product repository.

## 1. Install prerequisites

You need:

- git
- Python 3.10+

Optional:

- Streamlit (GUI)

## 2. Clone DAS Suite

If you have access to every module:

```bash
git clone <DAS_SUITE_REPO_URL> --recurse-submodules
cd das-suite
```

If you do not have access to every private module:

```bash
git clone <DAS_SUITE_REPO_URL>
cd das-suite
```

## 3. Install the CLI

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## 4. Sync modules

Sync only OSS modules:

```bash
dassuite sync --tier oss
```

Try to sync everything you can access:

```bash
dassuite sync --tier all
```

If your repos use SSH:

```bash
dassuite sync --tier all --ssh
```

## 5. Create a product repo

```bash
dassuite new-product \
  --template base \
  --dest ../my-product \
  --master-doc ./examples/master_doc.md \
  --ui-spec ./examples/ui_spec.md \
  --change-id chg-001 \
  --intent "Initial scaffold"
```

## 6. Use the GUI

Install GUI dependencies:

```bash
pip install -r requirements-gui.txt
```

Launch the GUI:

```bash
dassuite gui
```

## 7. What to do when a module clone fails

This usually means:

- You do not have permission
- You are using the wrong URL (HTTPS vs SSH)

DAS Suite will skip optional modules automatically.

Required modules must be available for product scaffolding.

## 6. Vendor Cadence into a product repo

If you use DAS Suite, the easiest way is to vendor from your local suite checkout:

```bash
dassuite vendor-cadence --product-root /path/to/product-repo --tier oss
```

This calls the product repo script `scripts/install_cadence_vendor.sh` with `--from-suite`.
