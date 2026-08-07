# Runbook — Dropbox store/mount rectification

**Audience:** operator on Mac Pro (or similar external-drive FP host)  
**Code posture:** Steward ≥ **0.3.19** (health verdict); scanner mid-walk commits ≥ **0.3.20**  
**Research:** [`field-notes-2026-07-28-dropbox-rectification.md`](../field-notes-2026-07-28-dropbox-rectification.md)

## What is healthy (do not “fix”)

| Observation | Meaning |
|---|---|
| Preferences path = `/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox` | Expected external-drive materialization |
| Dropbox tray green | Client health authoritative for sync |
| `steward fp status` → `layout=external_drive_fp`, `cloud_retire_ready=yes` | Different `st_dev` is OK |
| Domains.plist residual “unlinked” / `FPFS_SHOULD_NOT_BE_USED` | **Warning**, not re-link mandate while dual roots exist |

## Intents

| Intent | Paths | Apply flags |
|---|---|---|
| Cloud (trash / quota) | Prefer dual-present objects; plan from store claims mapped to mount (default) | `--require-fp-healthy` (no `--allow-store-path-unlink`) |
| Local free space on external volume | Store paths | `--allow-store-path-unlink` |

## Phase checklist

### 1. Probe

```bash
export STEWARD_DATA_DIR="$HOME/Library/Application Support/steward"
steward fp status
```

### 2. Conflict folders (Selective Sync Conflict)

- Content usually lives under **store** clean names.
- Empty conflict folders on **mount** can be deleted (Finder preferred when FP times out).
- Do **not** delete store clean trees.

### 3. Rescan (serialize — one scan at a time)

```bash
export STEWARD_DATA_DIR="$HOME/Library/Application Support/steward"
# Store first (canonical Preferences path).
# Prefer --workers 1 on multi-GB inventory.db: ProcessPool multi-writer
# scans have hit OperationalError: database is locked after large subtrees.
caffeinate -i steward scan --root /Volumes/DropboxStorage/.CloudStorage/Data/Dropbox --workers 1
# Resume after partial:
# caffeinate -i steward scan --root ... --workers 1 --resume
# Then mount
caffeinate -i steward scan --root "$HOME/Library/CloudStorage/Dropbox" --workers 1
```

Or after store finishes:

```bash
scripts/dropbox-post-scan.sh
```

Progress (0.3.20+): `scan_runs.files_walked` / `files_hashed` update during long walks.  
`STEWARD_SCAN_COMMIT_EVERY=250` (default); set `0` to disable mid-walk commits.

### 4. Re-plan

```bash
steward policy plan --policy retention.yml \
  --root /Volumes/DropboxStorage/.CloudStorage/Data/Dropbox \
  --out /tmp/plan-dropbox.tsv
```

Expect large `retire_direct` sets. **Do not bulk-execute** without dual-presence filter and intent.

Path-prefix splits (basename on both sides) are **not** enough. Prefer an offline per-file check (no inventory lock):

```bash
steward plans filter-dual-presence  # or: python scripts/filter-plan-dual-presence.py \
  --plan /path/to/plan-dropbox-retention.tsv \
  --out-dir /tmp/dropbox-plan-filter \
  --limit 5000   # raise after smoke
# Emits plan-dual.tsv / plan-store_only.tsv + filter-stats.json
```

2026-07-29 spot sample: forked top-levels (ArchDev, B848-CAD, …) were **store_only** for real files; shared `Home` samples were **dual**. Ghost mount conflict dirs hold nested empty structure only.

### 5. Sample dry-run

```bash
steward apply --manifest /path/to/sample.tsv --dry-run --require-fp-healthy
```

If mount-path hash verify hits `TimeoutError: [Errno 60]` (common under File Provider load), smoke the pipeline with:

```bash
steward apply --manifest /path/to/sample.tsv --dry-run --require-fp-healthy --skip-verify
```

Do **not** use `--skip-verify` on real `--execute` unless inventory hashes are trusted and the operator accepts cooling-off as the only recovery path.

**Mount full-tree scan:** often impractical while FP times out on `fast_hash` (walks without claims). Prefer store root (`info.json` / Preferences path) as the inventory authority; use dual-presence FS checks for cloud intent.

### 6. Execute only after dry-run

```bash
steward apply --manifest /path/to/sample.tsv --execute --require-fp-healthy
# or local reclaim:
steward apply --manifest /path/to/store-only.tsv --execute --allow-store-path-unlink
```

## Artifacts from 2026-07-28 run

`~/Library/Application Support/steward/runs/dropbox-rectif-20260728T230940Z/`

| File | Role |
|---|---|
| `RUN_STATUS.json` | Phase A–E status |
| `plan-dropbox-retention.tsv` | Full re-plan (~221k `retire_direct`) |
| `plan-dropbox-sample-dryrun.tsv` | 8 dual-present sample rows |
| `plan-dropbox-dual-top-prefix.tsv` | Rows under dual top-level basenames |
| `scan-store.pid` | Background store scan if still running |

## Anti-patterns

- Concurrent `steward scan` / `apply` against the same multi-GB `inventory.db`
- Bulk `UPDATE claims` store→mount paths
- Blind re-link of Dropbox solely for residual Domains.plist flags
- Executing the full 221k-row plan without filtering
