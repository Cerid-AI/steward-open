# Steward (open-core)

Filesystem stewardship: **scan, classify, plan, apply**.

This package is the **portable open-core** of Steward — permanode/claim
inventory, hash ladder, YAML policy engine, plan/apply lifecycle,
cooling-off stash, append-only audit chain, and cross-platform scanner
primitives.

Some operator-lab adapters (Photos.app, launchd schedules, private field
notes) live in the private Cerid overlay and are **not** required to run
the core CLI.

## Install (dev)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
steward --version
steward db migrate
```

## Quick start

```bash
steward scan --root /path/to/tier --workers 4
steward classify --reclassify-all
steward policy plan --policy retention.yml --out /tmp/retire.tsv
steward apply --manifest /tmp/retire.tsv --dry-run
steward status --quick
```

Destructive ops require explicit `--dry-run` or `--execute` (no default).

## Architecture

See `docs/adr/` for design decisions (permanode model, operator-in-the-loop,
audit chain, hash ladder, pull-don't-push inventory, cloud-FP retire).

## License

Apache-2.0.

## Note on cloud File Providers

macOS File Provider tiers (e.g. Dropbox) may expose store and mount paths
that are not the same inode. Cloud-propagating deletes use the
user-facing mount; local reclaim uses an explicit flag. See ADRs 0014–0015.
