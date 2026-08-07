# Steward — Operator Quickstart

End-to-end walkthrough of the current CLI surface (v0.3.x). Pick a
section by what you're trying to do.

- [Setup](#setup)
- [Scan + classify + dedup-retire](#scan--classify--dedup-retire)
- [Promotion (Backup → live tier)](#promotion-backup--live-tier)
- [Photos.app bulk import](#photosapp-bulk-import)
- [Local search](#local-search)
- [Replication (rclone)](#replication-rclone)
- [Archive (restic)](#archive-restic)
- [Continuous incremental scan (fsevents)](#continuous-incremental-scan-fsevents)
- [MCP integration](#mcp-integration)
- [Operator dashboard](#operator-dashboard)
- [Estate health (`steward health`)](#estate-health-steward-health)
- [Inventory aggregations (`steward stats`)](#inventory-aggregations-steward-stats)
- [Inventory surface (`steward surface`)](#inventory-surface-steward-surface)
- [Multi-machine awareness](#multi-machine-awareness)
- [Scheduled jobs (`steward schedule`)](#scheduled-jobs-steward-schedule)
- [Cloud-FP probe (`steward fp status`)](#cloud-fp-probe-steward-fp-status)

---

## Setup

```bash
# One-time: install Steward and create the inventory.
make install-dev
steward db migrate
```

That writes the inventory DB under the platform data dir (SQLite +
sqlite-vec, single file):

- **macOS:** `~/Library/Application Support/steward/inventory.db`
- **Linux:** `~/.local/share/steward/inventory.db`
- Override: `STEWARD_DATA_DIR` or `STEWARD_DB_PATH`

If you're coming from sprawl-audit, pull the legacy hash database in:

```bash
steward import legacy \
  --source ~/sprawl-audit/dedup-evidence/unified-hash.db
```

This is read-only on the source. The import runs in a single transaction
and writes a row to `legacy_import_log`. Re-running with the same
source file is a no-op.

---

## Scan + classify + dedup-retire

```bash
# 1. Walk a tier (parallel by top-level subdir; --resume reuses unchanged claims).
steward scan --root /Volumes/Backup --workers 4 --resume

# 2. Apply the classification policy retroactively.
steward classify --reclassify-all

# 3. Plan a dedup-retire under the bundled retention policy.
steward policy plan --policy retention.yml --out /tmp/retire.tsv

# 4. Dry-run the apply. Verifies hashes against on-disk state. Never writes.
steward apply --manifest /tmp/retire.tsv --dry-run

# 5. Execute. Stashes deletions into _cooling-off-stash/<run_id>/ for 7 days.
steward apply --manifest /tmp/retire.tsv --execute

# 6. Inspect the stash you created.
steward stash list

# 7. Once cooling-off has elapsed, finalize (real rm).
steward stash finalize --run-id <run-id>

# Optional: roll back instead of finalizing.
steward stash restore --run-id <run-id>

# Spot-check a permanode at any point.
steward inspect <canonical_hash-or-path>
```

Per [ADR-0002](adr/0002-operator-in-the-loop.md), `steward apply`
**requires** exactly one of `--dry-run` or `--execute`. No flag = exit 2.

---

## Promotion (Backup → live tier)

```bash
steward policy plan \
  --policy promotion.yml \
  --phase documents-validation \
  --limit 100 \
  --out /tmp/promote.tsv

steward apply --manifest /tmp/promote.tsv --dry-run
steward apply --manifest /tmp/promote.tsv --execute --max-files 50
```

The bundled `promotion.yml` carries the eight phases from the
sprawl-audit `promote_execute.py::PHASES` constants. Override per host
by writing your own `~/.config/steward/policies.d/promotion.yml`.

---

## Photos.app bulk import

The full workflow (three commands → operator runs `osxphotos`):

```bash
# 1. Scan the staging directory so its files have claims.
steward scan --root "/Volumes/Level 2/Photos/Heritage-Loose-Backup-Promote-2026-05-12/"

# 2. Inventory the Photos library (one claim per asset, classification=photos-app:<uuid>).
steward photos inventory \
  --library "/Volumes/Level 2/Photos/Photos Library.photoslibrary"

# 3. Plan the import. Each row = one parent dir + the osxphotos command to run.
steward photos plan \
  --source "/Volumes/Level 2/Photos/Heritage-Loose-Backup-Promote-2026-05-12/" \
  --library "/Volumes/Level 2/Photos/Photos Library.photoslibrary" \
  --out /tmp/photos-plan.tsv

# 4. The operator runs each row's osxphotos_command column. Steward never
#    invokes osxphotos import itself (per ADR-0009 pull-don't-push).
```

`osxphotos` is an optional install: `pip install osxphotos` or
`brew install osxphotos`. macOS-only.

---

## Local search

```bash
# Compute permanode embeddings with the deterministic stub backend
# (no model download required).
steward embed --backend stub

# Or the real ONNX backend (requires onnxruntime + tokenizers + a model
# downloaded under ~/.cache/steward/models/<model>/).
steward embed --backend onnx --model-name multilingual-e5-small

# Search.
steward search "vacation photos 2024" --k 10
```

The stub backend is non-semantic but the pipeline is end-to-end — useful
for smoke-testing. Real semantic search needs the ONNX backend.

---

## Replication (rclone)

```bash
# Inspect the bundled policy.
steward replicate show

# Dry-run: rclone --dry-run; neither side moves bytes.
steward replicate run --policy replication.yml --dry-run

# Execute.
steward replicate run --policy replication.yml --execute

# Override per-host: copy the bundled policy and edit.
cp $(python -c 'import steward.policies as p; from pathlib import Path; print(Path(p.__file__).parent / "replication.yml")') \
   ~/.config/steward/policies.d/replication.yml
$EDITOR ~/.config/steward/policies.d/replication.yml
steward replicate run --policy ~/.config/steward/policies.d/replication.yml --execute
```

The bundled policy mirrors `~/.local/share/steward/inventory.db` + the
user-data dir to `/Volumes/Backup/_steward-mirror/`. Add cloud remotes
(B2 / S3 / sftp via rclone backends) by editing the `destination` field.

---

## Archive (restic)

```bash
# One-time setup: stash a password in the macOS keychain.
security add-generic-password -a "$USER" -s steward-restic \
  -w "<your-restic-password>"

# Create the encrypted repository (requires --execute).
steward archive init --policy archive.yml --execute

# Snapshot. Requires --dry-run or --execute.
steward archive snapshot --policy archive.yml --dry-run
steward archive snapshot --policy archive.yml --execute

# List snapshots across every unique repository in the policy.
steward archive list --policy archive.yml
```

Steward never reads the password. The policy YAML supplies a
`password_command` (default: `security find-generic-password -s
steward-restic -w`) that restic invokes on demand.

---

## Continuous incremental scan (fsevents)

```bash
# Foreground (Ctrl-C to stop).
steward watch --root /Volumes/Level\ 2 --debounce-ms 750

# One-shot — wait for the first batch, process, exit.
steward watch --root /Volumes/Level\ 2 --once --idle-seconds 10

# Multiple roots are fine.
steward watch --root /Users/operator/Documents --root /Volumes/Backup
```

Per [ADR-0009](adr/0009-pull-dont-push-inventory.md), the watcher
**refreshes inventory only** — it never auto-applies a policy plan. You
still drive `steward apply` yourself.

---

## MCP integration

This repo ships `.mcp.json` for project-scoped IDE agents. Run explicitly:

```bash
steward mcp --transport stdio
# or loopback HTTP:
steward mcp --transport http --host 127.0.0.1 --port 8765
```

### Capability modes (ADR-0016)

| Env | Default | Effect |
|---|---|---|
| `STEWARD_MCP_MODE` | `plan` | `read` \| `plan` \| `write` |
| `STEWARD_MCP_ACTOR` | `steward-mcp` | Audit actor (set `steward-mcp:<client-id>`) |
| `STEWARD_MCP_MAX_FILES_CAP` | `50` | Cap for MCP `apply_execute` |

### Claude Desktop / Cerid dual-stack

```json
{
  "mcpServers": {
    "steward": {
      "command": "steward",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

Agents see **~25 tools**: inventory/status/inspect/fp, plan tools
(`policy_plan`, `apply_dry_run` → `plan_token`), and write tools
(`apply_execute`, replicate/archive/stash execute) gated by mode +
`destructiveHint=True`.

**FS apply execute path:** `apply_dry_run` → review → `STEWARD_MCP_MODE=write`
+ `apply_execute(plan_token, max_files=N)`. See
[`docs/CERID_AGENT_INTEGRATION.md`](CERID_AGENT_INTEGRATION.md).

Every MCP mutation appends `mcp_write_invoked` (actor from
`STEWARD_MCP_ACTOR`) so forensics can distinguish MCP vs CLI.

---

## Operator dashboard

```bash
# Rich tables, one per section.
steward status

# Multi-GB inventories: use cached count rollups; skip full audit-chain walk.
steward status --quick

# Recompute inventory COUNT rollups into meta (run after big scans/imports).
steward status --refresh

# JSON object for cron / alerting / jq pipelines.
steward status --json | jq '.audit_chain'
# {"rows_checked": 1234, "ok": true, "error": null, "skipped": false}

# Non-zero exit on broken audit chain — drop into launchd error handlers.
steward status >/dev/null || /usr/bin/say "Steward audit chain broken"
```

Sections: db file info, inventory counts (optionally rollup-cached),
latest scan, in-flight stash, last replicate/archive, audit-chain
integrity. Read-only (except `--refresh`, which writes rollup meta).

### HTML ops console (`steward dashboard`)

Browser ops console for posture, exploration, and plan hygiene — **not**
full CLI parity (see `docs/OPEN_DEVELOPMENT.md` § Dashboard product stance).

```bash
steward dashboard --open
# multi-GB: quick status seed (default)
steward dashboard --quick --refresh-seconds 30
```

Starts a loopback HTTP server (default `127.0.0.1:8080`). Soft-polls
`/status.json` without full-page reloads. Prefer `--quick` on multi‑GB
inventories.

```bash
# Default loopback bind:
steward dashboard

# LAN bind (deliberate only):
steward dashboard --host 0.0.0.0 --port 8080
```

**Tabs / APIs**

| UI | API / CLI |
|---|---|
| Overview + posture banner | `GET /api/health`, `/status.json` |
| Scans / Audit / Policies / Schedules | `GET /api/analysis` |
| Stats (tier/domain/volume/cross/…) | `GET /api/stats?axis=…` · `steward stats …` |
| Surface treemap | `GET /api/surface` · `steward surface tree` |
| Fleet matrix | `GET /api/fleet` · `steward machines health` |
| Inspector | path/hash search actions · `steward inspect` |
| Queues (plans + dual filter) | `GET /api/plans`, `/api/queues` · `steward plans …` |
| File Provider + dual-presence sample | `GET /api/fp` · `steward fp status` / `dual-presence` |
| Ops rail (28 actions) | `GET/POST /api/actions` |

**Ops rail notes**

- Destructive adapter actions (replicate/archive/stash) require typing
  `EXECUTE` and loopback clients only.
- **Apply dry-run** returns `execute_handoff` (CLI command + optional MCP
  `plan_token`). **`apply --execute` is not a GUI action** — use CLI or
  MCP write mode after a clean dry-run (ADR-0002 / 0016).
- Multi‑GB stats: set **path_prefix** (e.g. `/Volumes/Backup`) before
  unscoped pivots; unscoped can take minutes.

---

## Estate health (`steward health`)

Unified storage-estate gate (ADR-0017). Prefer this over ad-hoc
`status` + `fp status` for automation.

```bash
# Human-readable sections + overall banner.
steward health show

# Cheap automation default (cached rollups; audit walk skipped).
steward health check --quick
echo $?   # 0 = selected fail-on checks ok; 1 = fail; 2 = usage/error

# JSON for cron / monitoring.
steward health check --quick --json | jq '{overall: .report.overall, failed: .failed}'

# Persist a compact snapshot under <data_dir>/health/snapshots.jsonl
steward health check --quick --write-snapshot
# or: STEWARD_HEALTH_SNAPSHOT=1 steward health check --quick
```

### Default `--fail-on` (local integrity only)

When you omit `--fail-on`, check fails on:

| Token | Meaning |
|---|---|
| `stale_scan` | No recent finished scan for a tracked root |
| `broken_audit` | Audit chain not ok (needs `--full` or non-skipped chain) |
| `stash_overdue` | Cooling-off stash past policy window + grace |
| `rollup_stale` | Inventory count cache missing/stale |

### Opt-in tokens (explicit `--fail-on` only)

| Token | When to use |
|---|---|
| `fp_not_ready` | Cloud-propagating Dropbox/iCloud work (also `apply --require-fp-healthy`) |
| `dual_presence_poor` | Bulk cloud retire readiness (ADR-0020) |
| `fleet_stale_scan`, `fleet_chain_stale`, `envelope_sla`, `attached_missing` | Multi-machine envelope SLA (ADR-0021) |

```bash
# Cloud-retire gate before a large Dropbox plan:
steward health check --quick --fail-on fp_not_ready,dual_presence_poor

# Fleet / attached inventory SLA:
steward health check --include-imports --fail-on fleet_stale_scan,envelope_sla,attached_missing
steward machines health --check --include-imports
```

Related:

```bash
steward fp dual-presence --json
steward plans filter-dual-presence --manifest PATH --out-dir DIR
steward plans list
```

---

## Inventory aggregations (`steward stats`)

Read-only queries that aggregate over `claims` and `permanodes`.
Every subcommand supports `--json` for scripted consumers.

```bash
# Overview: headline counts + top 5 tiers + top 5 domains + largest permanode.
steward stats

# Group claims by tier.
steward stats by-tier

# Group by classification domain (photos / music / documents / ...).
steward stats by-domain

# Top file extensions by total bytes.
steward stats extensions --limit 20

# Top classification labels by claim count.
steward stats classifications --limit 20

# Permanodes with the most current claims — the dedup-candidate list.
steward stats duplicates --limit 20 --min-claims 3

# Bytes per volume (ADR-0022).
steward stats by-volume

# Cross-tab domain × extension (ADR-0022 data matrix).
steward stats cross domain --dim-b extension --limit 20
steward stats cross tier --path-prefix /Volumes/Backup --limit 50
```

JSON variants for cron / alerting:

```bash
# Alert when any tier is over a TiB.
steward stats by-tier --json | jq '.[] | select(.total_bytes > 1099511627776)'

# Find the 10 biggest extensions.
steward stats extensions --json --limit 10 | jq '.[] | .extension'

# Cross-tab sample.
steward stats cross domain --dim-b tier --json | jq '.cells[:5]'
```

## Inventory surface (`steward surface`)

Claim-based path tree (not live `du`). Prefer a prefix or tier on multi‑GB inventories.

```bash
steward surface tree --prefix /Volumes/Backup --color-by domain --limit 40
steward surface tree --tier L2 --json | jq '.children[:10]'
# Bounded dual-presence FS probe on children (Wave C; not full inventory walk)
steward surface tree --prefix /Volumes/DropboxStorage --color-by presence --limit 50
# Dry plan seed under a prefix (review TSV; never auto-execute)
steward surface plan-seed --prefix /Volumes/DropboxStorage/some/dir \
  --out /tmp/seed.tsv --action observe
steward surface plan-seed --prefix /Volumes/DropboxStorage/some/dir \
  --out /tmp/dual-seed.tsv --action retire_direct --dual-only --register
```

Dashboard **Surface** tab: `steward dashboard` → Surface → overlay domain/extension/tier,
drill directories, **Filter stats** to cross-tab the selection. API: `GET /api/surface`.

---

## Multi-machine awareness

Every claim / scan_run / audit row has carried a `machine_id` since
v0.1 (ADR-0008). `steward machines` exposes that axis:

```bash
# One row per machine that has touched this LOCAL inventory.
steward machines list

# Full details for one machine (accepts any unique UUID prefix).
steward machines show <machine-id-or-prefix>

# Fleet health matrix (local + optional attached imports).
steward machines health --include-imports
steward machines health --check --fail-on envelope_sla,attached_missing
```

On a single-machine setup `list` returns one row — the host's own
`machine_id`, marked as current.

### Cross-machine fan-out (v0.3.5)

After importing inventories from other machines via
`steward db import`, the `--include-imports` flag fans queries
across attached schemas via read-only ATTACH:

```bash
# List local + every attached machine; adds a "source" column.
steward machines list --include-imports

# Resolve a foreign machine_id; recent scan_runs + audit come
# from the attached schema.
steward machines show <foreign-prefix> --include-imports
```

Per ADR-0013, attached inventories are query-only. The
`apply` pre-flight (v0.3.4) blocks any manifest that tries to
mutate based on a foreign claim.

### Cross-machine read-side fan-out (v0.3.6)

`--include-imports` now extends to four more surfaces:

```bash
# inspect a hash that lives on another machine
steward inspect <hash> --include-imports

# aggregate across all machines (overview + every by-* subcommand)
steward stats --include-imports
steward stats by-tier --include-imports
steward stats duplicates --include-imports   # cross-machine dups!

# dashboard scope toggle (URL parameter)
steward dashboard            # then visit / for local only
                             # or /?include_imports=1 for all machines
```

MCP read tools (`inventory_stats`, `list_machines`, `get_machine`,
`get_permanode`) take an `include_imports` argument too — LLM
clients can query cross-machine state. Each returned record
carries a `source` field marking `local` or `attached`.

---

## Audit cold export (`steward db audit-export`)

Read-only JSONL dump of `audit_log` for offsite archival / analysis.
Does **not** delete rows or shrink the DB (append-only, ADR-0003).

```bash
steward db audit-export --out /tmp/audit.jsonl
steward db audit-export --out /tmp/old.jsonl --before 2026-01-01T00:00:00+00:00
steward db audit-export --out /tmp/applies.jsonl --action apply_end --limit 1000
```

## Audit chain archive (`steward db audit-archive`) — ADR-0018

Seal a contiguous `audit_log` id prefix into
`${STEWARD_DATA_DIR}/execution-log/audit-segment-*.tar.xz`, verify offline,
leave hot rows **unchanged** (no shrink in this phase).

```bash
# Plan only
steward db audit-archive --through-id 10000 --dry-run --hot-min-rows 100
# Seal
steward db audit-archive --through-id 10000 --execute --hot-min-rows 100
# Offline verify
steward db audit-archive --verify ~/Library/Application\ Support/steward/execution-log/audit-segment-….tar.xz
```

## Bulk dual-presence retire prep (execute gated)

```bash
steward plans filter-dual-presence --manifest plan.tsv --out-dir /tmp/dp
# Or one-shot prep (+ optional apply dry-run on dual bucket only):
steward plans bulk-retire-prep --manifest plan.tsv --out-dir /tmp/prep --dry-run-apply
# After operator review only:
# steward apply --manifest /tmp/prep/plan-dual.tsv --dry-run
# steward apply --manifest /tmp/prep/plan-dual.tsv --execute --require-fp-healthy
```

## Weekly health snapshot (launchd)

```bash
steward schedule show weekly-health-snapshot
steward schedule install weekly-health-snapshot --execute   # Sun 04:15
```

---

## Cross-machine export (`steward db export`)

`steward db export` (v0.3.0) produces a portable cross-machine
snapshot of the local inventory. The output is a `tar.xz` envelope
that another Steward instance can attach read-only (importer ships
in v0.3.1).

```bash
# Default destination: <inventory_dir>/exports/inventory-<short>-<iso>.tar.xz
steward db export

# Custom destination.
steward db export --out /Volumes/Backup/_steward-mirror/snapshot.tar.xz

# Include the embeddings tables in the payload (large; default excludes).
steward db export --with-embeddings

# Replace an existing envelope.
steward db export --out /Volumes/Backup/_steward-mirror/snapshot.tar.xz --overwrite
```

The envelope contains three files:

| File | Purpose |
|---|---|
| `inventory.db` | SQLite payload — same schema as ADR-0006, with excluded tables emptied + `VACUUM`ed. |
| `manifest.json` | Wire-format metadata: exporter info + payload blake3 + row counts. |
| `checksums.txt` | blake3 of the two files above (sha256sum-style format). |

Excluded by default: `tiers` (per-machine mount config),
`embeddings` / `embeddings_vec` (large + model-version coupled),
`legacy_import_log` (local provenance only),
`attached_inventories` (local bookkeeping).

Per ADR-0013, the exported inventory NEVER drives `apply --execute`
on the importing machine — query surface only.

```bash
# Snapshot the live DB first (defensive; v0.2.14 online-backup).
steward db backup

# Then export for cross-machine transport.
steward db export
```

### Importing a snapshot on another machine

Once the envelope is on the target machine, `steward db import`
(v0.3.1) unpacks and attaches it:

```bash
# Receives the envelope from machine A on machine B.
steward db import /path/to/inventory-<short>-<iso>.tar.xz
```

The importer:

1. Verifies the manifest bytes against the blake3 in `checksums.txt`.
2. Verifies the payload `inventory.db` against the blake3 in the
   manifest.
3. Walks the payload's audit chain end-to-end and verifies every
   `row_hash`.
4. Refuses if the envelope was exported from the SAME machine
   (you cannot import your own inventory).
5. Refuses if the envelope's `wire_format_version` is newer than
   this Steward supports.
6. Copies the verified payload to
   `<data_dir>/imports/<exporter_machine_id>/<iso>.db`.
7. Upserts a row into `attached_inventories` and appends one
   `inventory_attached` audit row to the LOCAL chain.

Re-importing from the same exporter machine_id is allowed — the new
payload replaces the previous one; both attach events are
audit-logged.

The imported file is NEVER opened writeable by Steward.
Cross-machine read-side queries (v0.3.5+) will open it via
`ATTACH DATABASE … ?mode=ro`.

### Managing attached inventories (`steward db imports`)

`steward db imports` (v0.3.2) exposes the attached inventories
table for operator inspection + detach:

```bash
# Show every attached inventory + payload status (ok / MISSING).
steward db imports list

# Preview a detach without making changes.
steward db imports detach <machine_id_prefix> --dry-run

# Remove the row + unlink the payload + audit-log it.
steward db imports detach <machine_id_prefix> --execute
```

`detach` accepts the full machine_id or any unique prefix.
Per ADR-0002 (operator-in-the-loop), it requires `--dry-run` or
`--execute` — running with neither exits 2. The
`inventory_detached` audit row records the payload path + blake3
+ whether the file existed at detach time.

### Verifying all imported chains (`steward db verify --imports`)

v0.3.3 extends `steward db verify` with an `--imports` flag that
walks each attached inventory's audit chain independently:

```bash
# Local chain only (default — same as v0.3.2 and earlier).
steward db verify

# Local chain + every attached inventory's chain.
steward db verify --imports
```

For each attached inventory the command reports `ok` / `BROKEN` /
`MISSING`. On success the row's `chain_verified_at` is refreshed
to the current ISO-8601 instant; on failure the prior good
timestamp is preserved as a last-known-good signal. The local
audit chain is NOT touched — verify is a read-side attestation,
not a chain event.

Exit code 0 only when the local chain AND every attached chain
verify cleanly; otherwise 1.

### Apply pre-flight (cross-machine safety)

v0.3.4 hardens `steward apply` against accidentally acting on
foreign claims. The pre-flight is **opportunistic** — it only
fires when at least one inventory is attached. Single-machine
installs see no overhead.

When attached inventories exist, every manifest row's
`permanode_id` is classified:

| Classification | Outcome |
|---|---|
| Has a current local claim | cleared — apply proceeds |
| Unknown locally + appears in an attached inventory | **REFUSED** |
| Unknown everywhere | cleared (apply's own downstream checks handle bogus paths) |

A refusal exits 2 (both for `--dry-run` and `--execute`) and
appends one `apply_rejected_imported_claim` audit row per refused
row. The CLI hints at `steward db imports list` / `detach` so
the operator can investigate.

Per ADR-0013, this is the structural enforcement of ADR-0009's
pull-don't-push invariant: attached inventories are a query
surface only.

---

## Retiring files in cloud-FP-backed tiers (`retire_direct`)

ADR-0014 (shipped v0.3.7) adds the `retire_direct` manifest
action — the right semantic for tiers backed by macOS File
Provider (Dropbox, iCloud Drive), where same-FS rename to a
stash dir would be propagated to the cloud as both a delete and
a re-upload.

`retire_direct` does a direct `Path.unlink()` and relies on the
tier's external trash / version history as the cooling-off
mechanism. **The recovery window is account-specific — verify it,
don't assume 30 days:** e.g. Dropbox Plus is 30 d by default but
**1 year** with the Extended Version History add-on; iCloud Drive
Deleted Items differs too. Set the `destination_tier` label to the
real window. The audit row records the cooling-off mechanism +
`verified` flag.

**ADR-0015 (v0.3.14+):** for Dropbox cloud-propagating retires, Steward
**verifies and unlinks the same mount path** (never hash-check A / delete B).
Manifests may still list store paths; default maps to the mount. For
local-only reclaim (cloud not guaranteed), pass
`--allow-store-path-unlink`. Gate cloud-intent applies with
`--require-fp-healthy`. See
[field notes](field-notes-2026-07-13-fp-cleanup.md) and
[ADR-0015](adr/0015-cloud-fp-mount-path-retire.md).

Manifest row shape (TSV columns same as `stash` / `promote`):

```
action          retire_direct
permanode_id    <32-hex>
canonical_hash  <hex>  (algo-aware: blake3, xxh3_128, or sha256)
size_bytes      <int>
source_path     /Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/...  (or mount path)
source_tier     DropboxStorage
destination_tier  dropbox-cloud-trash-<your-window>   ← cooling-off mechanism name
rationale       <human-readable>
```

### `--skip-verify` / `--allow-store-path-unlink`

```bash
# Default: per-file hash + size verify; Dropbox unlinks via mount (ADR-0015).
steward apply --manifest <plan.tsv> --execute

# Bulk mode: skip per-file verify; trust the inventory's recorded hash.
steward apply --manifest <plan.tsv> --execute --skip-verify

# Local-only reclaim: unlink claim/store path (cloud trash NOT guaranteed).
steward apply --manifest <plan.tsv> --execute --allow-store-path-unlink

# Refuse if fp status reports fork / missing mount (cloud-intent safety):
steward apply --manifest <plan.tsv> --dry-run --require-fp-healthy
```

Use `--skip-verify` when files haven't been modified since the
last scan AND the tier's external trash recovery is sufficient
fallback. The CLI prints a conspicuous warning when the flag is
set; the audit row records `verified: false` + `verify_algo: null`
so forensic queries can distinguish verified-vs-skip-verified
retires.

The flag only affects `retire_direct` rows; `stash` + `promote`
rows ignore it.

### Operating on cloud-FP tiers — macOS gotchas

Hard-won operational notes for running Steward against FP-backed
tiers (DropboxStorage, iCloudDrive). Full analysis in the
[field notes](field-notes-2026-07-13-fp-cleanup.md).

- **Let the FP settle before bulk retires.** Right after a migration,
  relocation, or reindex the FP is congested: deletes via the mount
  time out (`Errno 60`), and `os.path.exists()` can even return
  `False` for files that exist. Check it's idle first —
  `fileproviderctl dump -l <provider>` → `reconciliation` idle and
  `pending-indexable-count` low.
- **`fileproviderctl dump` is huge and slow** (scales with item count;
  ~400 K items → minutes / GB of output). Always use `dump -l`
  (limits items) and `dump <provider>` to scope, and pipe through
  `grep --line-buffered … | head` **or** dump-to-file — a plain
  `grep | head` block-buffers and hangs.
- **No `timeout(1)` on macOS.** Bound any hang-prone FP call with
  `perl -e 'alarm N; exec @ARGV' <cmd>`.
- **Keep the Mac awake** (`caffeinate`) during long FP operations;
  sleep interrupts reconciliation mid-flight.
- **If all FP clients break at once** (Dropbox won't boot, iCloud
  stuck, `fileproviderctl` itself errors `NSCocoaError 4099`), the
  cause is usually host FD exhaustion killing `fileproviderd` — check
  `sysctl kern.num_files kern.maxfiles`. Recovery needs a reboot;
  prevention is host-side (a virtiofs VM leaking host FDs). Not a
  Steward concern, but it corrupts FP-tier inventories, so know the
  signature.

---

## Scheduled jobs (`steward schedule`)

macOS-only. Steward bundles launchd plists for the common cadence
patterns and exposes a `steward schedule` subcommand that materializes
+ installs + manages them through `launchctl`.

```bash
# What schedules ship in the box?
steward schedule list

# Preview what would be installed (placeholders resolved).
steward schedule show nightly-archive

# Install + bootstrap (per ADR-0002, --execute required).
steward schedule install nightly-archive --execute

# Verify it loaded.
steward schedule status nightly-archive

# Uninstall when you're done.
steward schedule uninstall nightly-archive --execute
```

Three templates ship:

| Template | Schedule | Command |
|---|---|---|
| `nightly-archive` | every day at 02:15 | `steward archive snapshot --policy archive.yml --execute` |
| `nightly-replicate` | every day at 03:00 | `steward replicate run --policy replication.yml --execute` |
| `weekly-verify` | Sunday 04:00 | `steward db verify` |
| `weekly-inventory-export` | Monday 05:30 | `steward db export --overwrite` → Application Support exports |

Override the substituted paths per-host:

```bash
steward schedule install nightly-archive --execute \
  --home /Users/operator \
  --steward-bin /opt/homebrew/bin/steward \
  --log-dir /Users/operator/.local/share/steward/logs
```

The materialized plists land at `~/Library/LaunchAgents/com.cerid.steward.<name>.plist`.
You can hand-edit them after install — Steward's role is to bootstrap
the structure; subsequent tweaks are yours.

---

## Cloud-FP probe (`steward fp status`)

Lightweight check of Dropbox store vs CloudStorage mount — **no** full
`fileproviderctl dump` (that can run for minutes).

```bash
steward fp status
steward fp status --json
```

Reports: mount/store path existence, device IDs (fork detection),
sample dual-presence, and recommended steward flags. Does **not**
rewrite Dropbox trees — rectification is a separate high-risk
workstream (see `docs/OPEN_DEVELOPMENT.md`).

For retires on FP tiers see [Retiring files in cloud-FP-backed tiers](#retiring-files-in-cloud-fp-backed-tiers-retire_direct).
