# Changelog

All notable changes to Steward will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.25] — 2026-08-07

Continuous stewardship ops: estate-health polish, audit seal/verify, Wave C surface,
bulk dual-presence prep. No daemon; no bulk execute; no audit shrink.

### Added

- **`steward status --include-imports`** — CLI parity with machines/stats fan-out.
- **Launchd** `weekly-health-snapshot.plist` — Sun 04:15 `health check --quick --write-snapshot`.
- **ADR-0018 phases A–B:** `steward db audit-archive` (seal + offline verify, no shrink);
  migration `0003_audit_chain_segments`; segments under `data_dir/execution-log/`.
- **Wave C:** `surface tree --color-by presence` (bounded dual-presence FS probe);
  `steward surface plan-seed` (dry TSV skeleton; optional `--dual-only` / `--register`).
- **`steward plans bulk-retire-prep`** — dual-presence filter + optional apply dry-run;
  **execute always blocked** (operator must run `apply --execute` after review).
- **PyPI prep:** export stages package name `steward-fs`; checklist `docs/open-core/PYPI.md`.

### Changed

- **ADR-0017** Status → Accepted.
- **ADR-0018** Status → Accepted (phases A–B); shrink remains phase D.
- Open-core export allowlist includes `surface_plan_seed` + `bulk_retire_prep`.

### Notes

- `OPEN_CORE_DEPLOY_TOKEN` remains operator-configured for GHA open-repo publish;
  local `OPEN_CORE_PUSH=1 scripts/sync-steward-open.sh` still works without it.
- First PyPI upload is operator-gated (token / Trusted Publishing).

## [0.3.24] — 2026-08-07

Inventory **data matrix** + **graphic surface** (ADR-0022): multi-dim claim
aggregations, SQL path-tree drill, dashboard treemap with dynamic overlays.
Dashboard wiring expanded for fleet, dual-presence, stats cross, plan detail.
No daemon; no claim path rewrite.

### Added

- **ADR-0022** inventory surface and data matrix (Accepted).
- **`steward.core.matrix`**: pure types, validation, path-segment helpers.
- **`steward stats by-volume`** / **`by_volume`** aggregator (`claims.volume`).
- **`steward stats cross`** / **`cross_stats`**: 1–2 axis pivots with
  `path_prefix`, limit, optional `include_imports` (source dimension).
- **`steward surface tree`**: depth-1 inventory path tree (CLI); **SQL GROUP BY**
  next segment (no undercount from raw-row caps).
- **MCP** `inventory_cross_stats`, `inventory_path_tree`.
- **Dashboard** Surface tab + `GET /api/surface`; **Fleet** tab + `/api/fleet`;
  stats chips (volume/classifications) + **cross** controls; FP dual-presence
  sample; Queues plan Detail + dual-presence filter; ops actions
  `fleet_health`, `dual_presence_sample`, `filter_plan_dual_presence`,
  `surface_tree`, `plan_show`.
- Plan: `docs/superpowers/plans/2026-08-07-inventory-surface-data-mx.md`.

### Fixed

- Dashboard `show_policy` / `policy_plan` success payloads now set `ok: true`
  so HTTP status is 200 (previously missing `ok` mapped to 400).
- Surface path-tree undercount on multi‑GB inventories (SQL segment aggregate
  instead of 50k raw-row fetch).

### Changed

- **CI (GHA minutes):** family policy on `.github/workflows/ci.yml` —
  concurrency cancel, docs `paths-ignore`, consolidated lint (+ silent-catch),
  `test`/`security`/`preservation` need lint+typecheck, dependabot skips
  full test unless `dependabot-full-ci`, preservation merge-only
  (main / merge_group / dispatch).

### Notes

- Surface is **inventory claims only** (not live `du`). Prefer `--prefix` /
  `--tier` / `--volume` on multi‑GB inventories.
- Dashboard is an **ops console** (not full CLI parity). Existing EXECUTE-gated
  rail actions retained. Apply **dry-run** returns `execute_handoff` (CLI +
  MCP `plan_token`); apply **execute** stays CLI/MCP.
- Still CLI/MCP-only primary: `scan`/`watch`/`classify`/`embed`/`search`, `db *`,
  photos, schedule install.
- Docs: QUICKSTART dashboard section, OPEN_DEVELOPMENT stance, HANDOFF 0.3.24.

## [0.3.23] — 2026-08-06

Estate health foundation: unified storage-estate gate, plan backlog, dual-presence
cloud-truth hygiene, fleet matrix. No daemon; no audit shrink; no claim path rewrite.

### Added

- **ADR-0017** estate health: `steward health show|check`, `core.health` /
  `infra.health` (collect, probes, data-dir JSONL snapshots), MCP
  `estate_health` / `estate_health_check`, dashboard `GET /api/health` +
  `/api/health/series` + posture banner + `refresh_health`,
  `status --refresh` snapshot hook.
- **ADR-0018** audit chain-archive: design only (Proposed; no seal/shrink yet).
- **ADR-0019** plan backlog + schedule reliability: `steward plans *`,
  schedule reliability collect, dashboard Queues, MCP `plan_backlog_*`.
- **ADR-0020** dual-presence: plan filter, `fp dual-presence`, health section,
  MCP sample/filter tools.
- **ADR-0021** fleet health matrix: `machines health [--check]`, MCP
  `fleet_health*`, `GET /api/fleet`, estate health `.fleet` + envelope SLA.
- **Workflow:** `.grok/workflows/estate-health.rhai` for multi-slice delivery.
- Docs: QUICKSTART estate health section; README CLI surface; OPEN_DEVELOPMENT
  landed checklist.

### Fixed

- **`DEFAULT_CHECK_FAIL_ON`** is local integrity only
  (`stale_scan`, `broken_audit`, `stash_overdue`, `rollup_stale`).
  `dual_presence_poor`, `fp_not_ready`, and fleet SLA tokens remain known
  **opt-in** `--fail-on` values (ADR-0017/0020/0021).

## [0.3.22] — 2026-07-29

Cerid agent MCP integration: capability modes, plan tokens, gated apply_execute.
Open-core extract factory hardened and re-sync automation.

### Added

- **ADR-0016** — `STEWARD_MCP_MODE` (`read`/`plan`/`write`), `STEWARD_MCP_ACTOR`,
  one-shot `plan_token` from `apply_dry_run`, MCP `apply_execute` with mandatory
  `max_files` (cap via `STEWARD_MCP_MAX_FILES_CAP`, default 50).
- **MCP tools:** `mcp_capability`, `status`, `scan_status`, `inspect_target`,
  `apply_execute`; `apply_dry_run` gains `require_fp_healthy` + plan_token.
- **`.mcp.json`** project registration (stdio) + `docs/CERID_AGENT_INTEGRATION.md`.
- **Open-core factory:** `export-open-core.sh --verify`; private CI
  `open-core-export`; tag/`workflow_dispatch` publish via
  `OPEN_CORE_DEPLOY_TOKEN` (`open-core-publish.yml`).
- **Product identity:** public package name target **`steward-fs`**; private
  monorepo remains sole source of truth until Phase 2 invert
  (`docs/OPEN_CORE.md`, public `CONTRIBUTING.md`).

### Changed

- Destructive MCP execute tools require `STEWARD_MCP_MODE=write` (default remains
  `plan` so external agents cannot ambient-execute).
- `mcp_write_invoked` actor uses `STEWARD_MCP_ACTOR` when set.

## [0.3.21] — 2026-07-29

Field-hardened apply + scan visibility after Dropbox store rescan / FP congestion.

### Fixed

- **`retire_direct` verify-path FP timeouts** (`TimeoutError` / Errno 60 on
  mount `stat`/`hash`) map to `FPUnavailableError` so apply defers that row
  instead of aborting the whole batch (same posture as unlink timeout).
- **Apply belt-and-suspenders:** bare `TimeoutError` from a row is deferred
  per-row rather than unwinding the apply transaction.

### Changed

- **Serial scanner** commits the `scan_runs` + `scan_start` audit row **before**
  walking (same as parallel workers). Operators see an unfinished run immediately
  rather than only after the first mid-walk commit (`STEWARD_SCAN_COMMIT_EVERY`).

### Docs / tooling

- Dual-presence offline filter: `scripts/filter-plan-dual-presence.py`
- Runbook: mount full-tree scan often impractical under FP hash timeouts;
  store Preferences path is inventory authority; `--skip-verify` dry-run smoke.

## [0.3.20] — 2026-07-28

Scanner progress for multi-hour Dropbox trees; Dropbox rectification runbook.

### Changed

- **Scanner mid-walk commits** every 250 files (override
  `STEWARD_SCAN_COMMIT_EVERY`; `0` disables). Parallel workers no longer hold a
  single transaction for an entire top-level subtree — live `scan_runs`
  counters bump and claims become visible during long walks.
- **Runbook** `docs/runbooks/dropbox-rectification.md` +
  `scripts/dropbox-post-scan.sh` (wait → mount scan → sample dry-run).

## [0.3.19] — 2026-07-28

Systemic Dropbox handling correction: external-drive FP is a healthy layout;
health verdict separates hard fails from warnings.

### Changed

- **`classify_tier`:** `~/Library/CloudStorage/Dropbox` (and `Dropbox-*`) is
  **DropboxStorage**, not boot — required for mount rescans.
- **`fp status`:** structured `FPHealthVerdict` (`layout`, `cloud_retire_ready`,
  `local_reclaim_ready`, problems vs warnings). Different `st_dev` and residual
  Domains.plist "unlinked" / `FPFS_SHOULD_NOT_BE_USED` are **warnings** on
  external-drive layouts when mount+store exist and dual samples are healthy.
- **`--require-fp-healthy`:** hard-fails only on real cloud blockers (missing
  mount, store-only samples, hard disconnect without dual roots). Prints
  warnings (name divergence, residual domain metadata) without aborting.
- ADR-0015 / retire docs: verify==unlink (not store-verify / mount-unlink).

### Docs

- Field notes + OPEN_DEVELOPMENT: Preferences store path + green client are
  compatible with dual path families; re-link is not mandated by residual
  Domains.plist alone.

## [0.3.18] — 2026-07-28

Dropbox rectification research pass: domain/unlinked detection, name-divergence probe, field notes.

### Added

- **`steward fp status`** — File Provider **Domains.plist** probe (connected /
  unlinked, `DisconnectionReason`, `Path`, `SupportsSyncingTrash`);
  `~/.dropbox/info.json` path; top-level **store vs mount basename divergence**.
- **`apply --require-fp-healthy`** also fails on **unlinked/disconnected** Dropbox
  domain and top-level name divergence (cloud-propagating intent).
- **`docs/field-notes-2026-07-28-dropbox-rectification.md`** — inventory
  provenance (legacy import), FP domain unlinked diagnosis, Selective Sync
  Conflict split, operator repair protocol (no bulk path rewrite).

### Changed

- OPEN_DEVELOPMENT / ROADMAP: Dropbox workstream phase 1–2 research complete;
  host re-link remains operator-led.

## [0.3.17] — 2026-07-28

Open-core Phase 1 stage extract, audit cold export, weekly inventory export schedule.

### Added

- **`steward db audit-export`** — read-only JSONL dump of `audit_log`
  (`--before` / `--after` / `--limit` / `--action`). Does not delete rows
  (ADR-0003).
- **`scripts/export-open-core.sh --stage --tarball`** — stage tree + host-path
  scrub + `docs/open-core/PUBLIC_README.md`.
- **`weekly-inventory-export`** launchd template — weekly `db export --overwrite`.

### Changed

- OPEN_CORE / OPEN_DEVELOPMENT / ROADMAP updated for Phase 1 staging readiness.
- Dropbox rectification remains deferred.

## [0.3.16] — 2026-07-28

Dashboard quick default, apply FP health gate, MCP plan/dry-run tools.

### Added

- **Dashboard** defaults to quick status collection (`--full` or `?full=1`
  for complete audit-chain walks). Multi‑GB inventories stay responsive.
- **`steward apply --require-fp-healthy`** — refuse when the plan touches
  Dropbox/cloud-FP rows and `fp status` reports fork / missing mount /
  congestion (does not rectify Dropbox trees).
- **MCP (read-only):** `policy_plan`, `apply_dry_run`, `fp_status`.
- Tier-auditor agent: `status --quick`, `fp status`.

### Changed

- `PlanSummary` includes `retire_direct_rows`.

## [0.3.15] — 2026-07-28

Docs consistency + multi‑GB status operability + FP probe. Dropbox
**tree rectification** explicitly deferred (history + API review required).

### Added

- **`steward status --quick`** — skip full audit-chain walk and heavy
  stash CTE; prefer inventory COUNT rollups when fresh.
- **`steward status --refresh`** — recompute inventory counts into
  ``meta.status_inventory_rollups``.
- **`steward fp status`** — lightweight Dropbox store vs mount fork probe
  (no `fileproviderctl dump`).
- Preservation tests for verify==unlink and Dropbox → `retire_direct`.
- Docs: `OPEN_DEVELOPMENT.md` rewritten for post-0.3.14; ROADMAP /
  README / QUICKSTART / AGENTS aligned; retire-decider agent FP-aware.

### Notes

- Bulk Dropbox store↔mount “healing” is **not** in scope; use mount
  rescans for cloud intent or `--allow-store-path-unlink` for local
  reclaim until a dedicated rectification workstream completes.

## [0.3.14] — 2026-07-28

Logic-law fixes from store/mount inventory sampling + FP experiment.

### Fixed

- **Verify == unlink invariant** in `resolve_fp_paths` / `retire_direct`.
  Never hash-check store and delete mount (forked trees). Cloud mode
  uses mount for both; `--allow-store-path-unlink` uses claim path for both.
- **Reconciler** emits ``retire_direct`` for `CLOUD_FP_TIERS` (DropboxStorage)
  instead of same-FS ``stash`` (ADR-0014 was incomplete in plan generation).

### Added

- `CLOUD_FP_TIERS` + `CLOUD_FP_COOLING_OFF` in `core/tiers.py`.
- Unit coverage for Dropbox → `retire_direct` plan rows.

### Inventory sample (live Mac Pro, 2026-07-28)

- DropboxStorage current claims: **357 733**, all store-path; **0** mount-path.
- Sample n=95: store exists 94/95; mount exists 18/95; both 18 (sizes matched).
- Implication: cloud-propagating bulk retire needs a CloudStorage mount rescan.

## [0.3.13] — 2026-07-27

Track 1b cloud-FP path policy (ADR-0015), honest `nas_manifest` export,
docs/open-core scaffolding. Full suite targeted green at ~440 tests.

### Added

- **ADR-0015** — cloud-FP retire via user-facing mount path; verify on store.
- **`steward.core.fp_paths`** — pure Dropbox store ↔ mount mapping + claim aliases.
- **`steward apply --allow-store-path-unlink`** — opt out of mount-prefer unlink
  for local-only reclaim (cloud trash not guaranteed).
- **`nas_manifest` apply handler** — writes
  `<data_dir>/runs/<run_id>/nas_manifest.tsv` + `nas_manifest_exported` audit
  (no silent skip; no NAS FS mutation).
- **`docs/OPEN_CORE.md`** — approved open-core public/private split plan.
- **`docs/runbooks/cloud-fp-retire.md`**, **`nas-manifest-export.md`**.
- **`scripts/export-open-core.sh`** — path allowlist dry-run for public extract.
- Unit + integration tests for FP paths, mount unlink, nas export.

### Changed

- **`retire_direct`** resolves verify/unlink paths per ADR-0015; audit payload
  includes `unlink_path`, `verify_path`, `used_mount_for_unlink`.
- CLI-only actions (`restore`, `finalize_stash`, `reclassify`) error with a
  pointer to the correct CLI instead of “not implemented in v0.1”.
- **`scripts/weekly-run.sh`** resolves macOS Application Support data dir
  (no longer defaults blindly to `~/.local/share/steward`).
- Docs: README status, root ROADMAP pointer, QUICKSTART, CLAUDE.md, field notes,
  MCP server instructions, scan help text.

## [0.3.12] — 2026-07-15

Dashboard UX: the single-page status dashboard now adapts to the operator's
OS theme. Previously the inline CSS was hardcoded light, so a Mac in dark mode
got a blinding white page. The read-only, self-contained, no-dependency design
is unchanged — only the stylesheet gained a dark variant.

### Changed

- **`render_status_html`** now emits a `@media (prefers-color-scheme: dark)`
  block mirroring the light palette (background, cards, text, `ok`/`bad`
  accents, links). No JS, no toggle — it follows the OS setting.
- The broken-audit banner moved off a hardcoded light inline style onto an
  `.audit-banner` class, so it re-themes in dark mode instead of staying a
  bright box.

## [0.3.11] — 2026-07-15

FP-tier robustness: a cloud-File-Provider delete that times out (`Errno 60`)
no longer aborts the whole `apply` batch. Surfaced by a real-world Dropbox
cleanup where one congested-mount `unlink()` rolled back an entire retire run.
Full suite green (423 passed, 6 network-gated deselected; +1 regression test).

### Added

- **`steward.core.errors.FPUnavailableError`** — typed, retryable error meaning
  a cloud-FP tier couldn't service a filesystem operation (the sync agent's
  delete propagation timed out). The manifest and data are fine; the FP is
  congested or degraded.

### Changed

- **`retire_direct`** now catches a `TimeoutError` from `source_path.unlink()`
  and re-raises it as `FPUnavailableError` (no claims/audit write has happened
  yet, so the row's transaction state is clean).
- **`apply`** catches `FPUnavailableError` per row: it defers that row
  (`rows_errored`, "retry later" message) and continues the batch, instead of
  letting a raw `TimeoutError` escape `except ManifestError` and roll back
  every other row.

### Notes

- Forward-looking: today `retire_direct` unlinks the materialized store path (a
  fast local removal that doesn't time out); this handling becomes load-bearing
  once cloud-FP retires move to the user-facing mount path — see
  `docs/field-notes-2026-07-13-fp-cleanup.md` (gaps #1/#2).

## [0.3.10] — 2026-05-17

Universalizes algo-aware hash-verify across all Steward code
paths that re-hash files against inventory-recorded hashes (F10).
Before this, `promote_with_verify` hard-assumed blake3 — would
have failed verification on every legacy-imported (sha256) and
small-file (xxh3_128) permanode. 428 tests passing (was 427 at
v0.3.9; +1 promote-sha256 regression test).

### Added

- **`steward.core.hashing.hash_file_by_algo(path, algo=...)`** —
  single canonical helper. Streams once, returns
  `(hex_digest, size_bytes)`. Supports `blake3`, `xxh3_128`,
  `sha256`. Falls back to blake3 for unrecognised algos.
- **`steward.core.hashing.new_hasher_for(algo)`** — returns a
  hashlib-style hasher instance for incremental copy-and-hash
  use cases (promote's single-pass execute path).

### Changed

- `infra/promote.py::promote_with_verify` — all three hash sites
  (idempotency check on existing dst, dry-run source verify,
  execute copy-and-verify) now look up the permanode's recorded
  `canonical_hash_algo` and use the matching algorithm. Existing
  blake3-only behaviour is preserved when the permanode says
  blake3 OR when the permanode isn't found (synthetic/test rows
  fall back to blake3).
- `infra/retire.py::_hash_file` — now a thin wrapper over
  `hash_file_by_algo` (collapses the duplicate algo dispatch
  added in v0.3.8).

### Friction note (F10 fully closed)

Steward's verify paths are now consistent in their algo handling.
The hash ladder (xxh3_128 → blake3 promote per ADR-0005)
remains the producer-side policy; this change makes the
consumer-side (verify-before-apply) match what the producer
actually stored.

## [0.3.9] — 2026-05-17

Adds **`steward apply --skip-verify`** for retire_direct rows at
FP-tier scale (F11). On cloud-FP-backed tiers like
DropboxStorage, per-file content verification requires cloud
hydration of every retire candidate — at ~4 s/file via the
Dropbox sync agent, a 30K-file retire takes 33 hours of verify
time before any rm happens. The new mode trades that
verification for the inventory's recorded hash + the cooling-off
recovery (cloud trash). 427 tests passing (was 425 at v0.3.8).

### Added

- **`steward apply --skip-verify`** CLI flag. CLI prints a
  conspicuous warning when set. The flag propagates only to
  `retire_direct` rows; `stash` and `promote` ignore it.
- `infra/retire.py::retire_direct(..., verify: bool = True)` —
  when `verify=False`, the size + hash checks are skipped but the
  existence + regular-file checks still run.
- Audit row records `verified: bool` and `verify_algo` (None on
  skip), so forensics can distinguish skip-verified retires from
  verified ones.
- 2 new integration tests: skip-verify lets mismatched hash/size
  rm proceed; skip-verify still refuses missing source.
- ADR-0014 updated with the skip-verify mode + when-to-use
  guidance.

### Operator-side: F12 also landed (no Steward release)

- `~/sprawl-audit/scripts/build_sbc_steward_manifest.py` now
  `GROUP BY sp.sbc_path` to dedupe — collapses the raw 84,293
  SBC plan rows to 29,038 unique source paths (57.53 GiB net
  after the 100-row execute earlier in the session). 18 prior
  "source not found" errors at execute were all
  duplicate-row artifacts that F12 eliminates.

### Use case

Operator can now run the full 29K-row SBC dedup retire in
minutes rather than days. Trade-off documented in ADR-0014
"Skip-verify mode" section.

## [0.3.8] — 2026-05-17

Hot-patch on v0.3.7: `retire_direct`'s algo-aware hash verifier
now handles **sha256** in addition to xxh3_128 + blake3.
Surfaced immediately by real-world dogfooding: permanodes
imported from sprawl-audit's legacy `unified-hash.db` carry
`canonical_hash_algo='sha256'`, and the v0.3.7 verifier fell
back to blake3 for unrecognised algos → would have failed the
verification on every legacy-imported file. 425 tests passing
(was 424 at v0.3.7; +1 sha256 verification test).

### Added

- `infra/retire.py::_hash_file` — supports `sha256` (stdlib
  hashlib) alongside the existing `xxh3_128` and `blake3`
  branches.
- Test: `test_retire_direct_sha256_algo_for_legacy_imported_permanode`
  exercises the legacy-import path explicitly by inserting a
  permanode with `canonical_hash_algo='sha256'`, scanning a
  matching file, retiring via apply, and confirming the rm
  happened.

### Friction note (logged as F10)

Steward's algo-aware verification needs to be CONSISTENT across
every code path that hashes-for-verify. v0.3.7 fixed it in
`retire_direct`. Other paths (`promote_with_verify`,
`stash_cmd::verify`) currently hard-assume blake3; they will
fail on legacy-imported permanodes. Tracking for a v0.3.9
"hash-verify universalize" sprint.

## [0.3.7] — 2026-05-17

Adds the **`retire_direct` manifest action** (ADR-0014) — the
right semantic for cloud-FP-backed tiers like DropboxStorage and
iCloudDrive, where same-FS stash rename is wrong (the FP agent
sees both the source delete and the new file in the stash dir and
propagates both to cloud). Surfaced by real-world operator work on
Dropbox SBC cleanup. 424 tests passing (was 415 at v0.3.6).

### Added

**ADR-0014** — `retire_direct` action design + per-tier
"external cooling-off" model (Dropbox 30-day cloud trash, iCloud
Drive 30-day deleted items, etc.). New action name (vs. flag on
stash) preserves manifest readability and audit-log clarity.

**`retire_direct` manifest action**
(`core/model/manifest.py` + `infra/db/apply.py` +
`infra/retire.py`):

- Verifies source exists, size matches, blake3/xxh3-128 hash
  matches expected (algo determined by looking up the
  permanode's `canonical_hash_algo`; falls back to blake3 for
  synthetic manifest rows).
- Surfaces "last-copy warning" in the audit payload when no
  other current claims exist for the permanode — operator
  knowledge trumps the heuristic (no refusal).
- Direct `Path.unlink()` on the source; no same-FS rename, no
  stash dir, no in-Steward cooling-off.
- Audit row uses action `retire_direct_executed` with payload
  including `cooling_off_mechanism` (operator-supplied string
  via the manifest row's `destination_tier` field; e.g.
  `"dropbox-cloud-trash-30d"`).
- Updates `claims.is_current → 0` for the affected claim.
- Cross-machine pre-flight (ADR-0013) applies unchanged —
  retire_direct rows referencing attached-only permanodes are
  refused before any rm.

### Architecture

- New module `infra/retire.py` carries the pure function.
- Uses the same algo-aware hash convention as the rest of the
  infra (xxh3_128 for small files per the scanner ladder, blake3
  for archive grade).
- 9 new integration tests covering: happy-path execute, dry-run
  no-writes, missing-source refusal, size-mismatch refusal,
  hash-mismatch refusal, last-copy warning, claim-is_current
  flip, idempotency on re-apply, cross-machine pre-flight
  composition.

### Notes

- Drove by real operator work: a Dropbox cleanup of 84K SBC
  pairs / ~86 GiB was queued behind a missing Steward action.
  v0.3.7 unblocks that.
- Manifest writers that target DropboxStorage should set
  `action: retire_direct` and `destination_tier:
  dropbox-cloud-trash-30d` (or similar) to surface the
  cooling-off mechanism in the audit chain.

## [0.3.6] — 2026-05-17

**Completes ADR-0013.** v0.3.5 shipped read-side fan-out for
`machines`; v0.3.6 extends the same pattern across the remaining
four read surfaces in one bundle:

1. **`steward inspect --include-imports`** — resolves a hash that
   exists only in an attached inventory; merges claims + audit
   rows from every attached schema. Each row tagged with its
   `source` (local / attached).
2. **`steward stats --include-imports`** — all six aggregators
   (overview / by-tier / by-domain / extensions / classifications
   / duplicates) honour the flag; UNION ALL across attached
   schemas. Cross-machine duplicates surface naturally.
3. **Dashboard `?include_imports=1`** — URL parameter on `/` and
   `/status.json`. The rendered page shows a scope toggle between
   "local only" and "all machines"; JSON includes the flag value
   in the payload.
4. **MCP read tools** — `inventory_stats`, `list_machines`,
   `get_machine`, `get_permanode` all accept `include_imports`.
   Each returned record carries a `source` field when the flag
   is set. LLM clients (Claude Desktop, etc.) can now query
   cross-machine state.

415 tests passing (was 400 at v0.3.5).

### Added

**`infra/db/inspect.py::inspect(target, include_imports=True)`** —
fan-out resolution path that tries local first then each attached
schema. Result carries `source` and `resolution_schema` fields.
Claims fan-out collects from all schemas; audit-row fan-out
merges per-schema lists and keeps the most-recent `audit_limit`
across all of them. Each row gets a `source` tag.

**`infra/stats.py`** — 6 aggregators all gain
`include_imports: bool = False`. Two helpers
(`_claims_source_clause` / `_permanodes_source_clause`) build
UNION ALL subqueries; `_run_with_sources` is a single point of
substitution so the fan-out path lives in one place. Fast path
(no flag) preserves the v0.2.13 query plan verbatim.

**`infra/status.py::collect_status(include_imports=True)`** —
machine count honours the flag (other counts stay local — those
describe THIS machine's pipeline).

**Dashboard server** — `?include_imports=1/true/yes/on` query
parameter on `/` and `/status.json`. Renderer surfaces a scope
toggle in the header: "scope: local only · include attached"
or "scope: all machines · switch to local only". JSON payload
includes the flag value.

**MCP server** — four read tools (`inventory_stats`,
`get_permanode`, `list_machines`, `get_machine`) gain an
`include_imports: bool = False` argument. Each returned dict's
relevant entries carry a `source` field tagging local vs
attached origin.

### Architecture

- Every fan-out surface reuses `attach_imports` from v0.3.5 — the
  shared primitive opened by v0.3.5 has now amortized across all
  five read surfaces.
- SQL identifiers (schema aliases) are derived from validated UUIDs
  per ADR-0013; no user input ever interpolates into the query.
- 15 new integration tests
  (`tests/integration/test_v035_readside_fanout.py`) covering each
  surface's no-flag default + with-flag behaviour. Local-only
  default explicitly tested for every aggregator to lock in the
  v0.2.x backwards-compatibility contract.

### Notes

- ADR-0013 is now structurally complete. The v0.3 cross-machine
  arc spans: ADR + export + import + list/detach + verify + apply
  pre-flight + read-side fan-out (machines, inspect, stats,
  dashboard, MCP).
- Next: open conversation about v0.4 — possible directions
  include MCP write surface extensions, additional adapter ports
  (cloud archive tier), or stack bump (Python 3.13 + uv) in
  lockstep with Cerid.

## [0.3.5] — 2026-05-17

First read-side fan-out: `steward machines list / show` now
supports `--include-imports` to surface attached inventories'
machine_ids alongside local ones (ADR-0013). Same pattern will
extend to `inspect` / `stats` / `dashboard` / MCP read tools in
subsequent sprints. 400 tests passing (was 392 at v0.3.4).

### Added

**Reusable read-only ATTACH helper**
(`infra/sync/attach.py::attach_imports`):

- Context manager that opens the local DB and `ATTACH DATABASE
  'file:...?mode=ro' AS m_<short_id>` for every
  `attached_inventories` row.
- Yields an `AttachContext` with `aliases` (for UNION ALL query
  building) and `attached` (per-schema metadata).
- Guarantees DETACH on exit even if the body raises.
- Attach failures (missing payload, corrupted .db) are swallowed
  via `log_swallowed_error` — those inventories are silently
  skipped; operators see them as MISSING in
  `steward db imports list`.

**Cross-machine `machines list` + `show`**
(`infra/machines.py` + `cli/machines_cmd.py`):

- New `include_imports: bool = False` parameter on
  `list_machines`, `count_machines`, `get_machine`. Default is
  local-only — v0.2.9 surface preserved.
- With `include_imports=True`, the aggregator UNION-ALLs across
  local + every attached schema. Machine_ids from
  `attached_inventories` are included even if the imported
  inventory has zero rows (so a freshly-imported peer surfaces
  with `claim_count=0`).
- `MachineSummary` gains a `source` field: `"local"` or
  `"attached"`. Single-machine installs see only `"local"`.
- `get_machine` with `include_imports=True` resolves a foreign
  machine_id and pulls recent scan_runs + audit from the
  attached schema (read-only).
- CLI: `--include-imports` flag on `machines list` and `show`.
  When set, the list adds a `source` column; `show` adds a
  `source` row to the header.

### Architecture

- `infra/sync/attach.py` is the shared primitive every future
  read-side fan-out (inspect, stats, dashboard, MCP) will use.
- Schema aliases are derived from validated UUIDs so the SQL
  identifier is always safe — no user input ever interpolates
  into the query.
- The fan-out aggregator query is a CTE chain:
  `claims_all / scan_runs_all / audit_all` UNION-ALL across
  schemas, then existing left-join roll-up on top. Single-schema
  callers (no `include_imports`) bypass the
  `attached_inventories` UNION to keep the v0.2.9 query plan
  identical.
- 8 new integration tests
  (`tests/integration/test_machines_fanout.py`) covering:
  default-is-local-only / `include_imports` surfaces attached /
  `get_machine` doesn't route attached without flag /
  `get_machine` resolves attached with flag / local-id still
  works with flag / multiple attached inventories all visible /
  detach removes from fan-out view / count_machines symmetry.

### Notes

- Next under ADR-0013: v0.3.6 `inspect --include-imports`,
  v0.3.7 `stats --include-imports`, v0.3.8 dashboard toggle,
  v0.3.9 MCP read tools. Each reuses the `attach_imports`
  helper.

## [0.3.4] — 2026-05-16

Structural enforcement of ADR-0013's pull-don't-push invariant.
`steward apply` now runs a cross-machine pre-flight before any
row work: if a manifest references a `permanode_id` that exists
only in an attached (imported) inventory and not in the local
claims, the apply is refused — for both `--dry-run` and
`--execute`. 392 tests passing (was 386 at v0.3.3).

### Added

**Apply pre-flight check** (`infra/sync/apply_preflight.py`):

- Opportunistic — fires only when at least one
  `attached_inventories` row exists. Single-machine installs see
  no overhead (v0.1 / v0.2 apply path unchanged).
- For each `attached_inventories` row, attaches the payload .db
  via `ATTACH DATABASE 'file:...?mode=ro' AS m_<short_id>` —
  read-only at the OS level. Errors from missing payload files
  are caught and the inventory is skipped (the operator should
  `imports detach` the stale row).
- For every manifest row, classifies the `permanode_id`:
  - **Local hit** (current claim with local machine_id): cleared.
  - **Local miss + attached hit**: REFUSED — this is a
    foreign-claim row that ADR-0013 forbids applying.
  - **Local miss + attached miss**: cleared. The pre-flight only
    refuses on KNOWN-foreign-only permanodes; apply's downstream
    path-existence + hash-verification will catch other errors.
- Returns an `ApplyPreflightReport` with a list of `RejectedRow`s.

**`ApplyRefused` exception** (`infra/db/apply.py`):

- Raised by `apply_manifest` when the pre-flight rejects any row.
- Carries an `ApplyResult` whose `rejected_imported_claims` field
  describes every rejection in operator-readable form.
- Each rejection appends one `apply_rejected_imported_claim`
  audit row to the local chain (own transaction; the apply
  transaction never opens).

**CLI surface** (`cli/apply_cmd.py`):

- Catches `ApplyRefused`, prints each rejection, hints at
  `steward db imports list` / `detach`, exits 2.

### Architecture

- The pre-flight uses its own connection so an ATTACH never leaks
  into the apply transaction.
- Aliases for attached schemas are derived from the imported
  machine_id (validated UUID) so the schema name is always a safe
  identifier.
- 6 new integration tests
  (`tests/integration/test_apply_preflight.py`) covering: no-op
  when no imports / local-permanode allowed when imports present /
  foreign-only permanode refused (both `--dry-run` and
  `--execute`) / unknown-everywhere permanode allowed / missing
  attached payload gracefully skipped / audit row appended on
  refusal.

### Notes

- Per ADR-0013 the manifest model carries `permanode_id`, not
  `claim_id`. The check is "does this permanode have a current
  local claim?" rather than literal `claim_id` lookup — the
  semantic intent ADR-0013 specifies.
- Next under ADR-0013: v0.3.5+ — read-side fan-out across
  `inspect`, `machines`, `stats`, `dashboard`, MCP read tools.

## [0.3.3] — 2026-05-16

Adds `--imports` to `steward db verify` — walks every attached
inventory's audit chain independently and updates
`chain_verified_at` on the row. Operators can now attest the
integrity of all cross-machine imports in one command. 386 tests
passing (was 379 at v0.3.2).

### Added

**`steward db verify --imports`** (`cli/db_cmd.py` +
`infra/sync/imports_admin.py::verify_imports`):

- Runs the local-chain verify first; exits non-zero on local
  break without touching imports.
- For each `attached_inventories` row:
  - If the payload .db is missing: flag MISSING, do NOT update
    `chain_verified_at` (the prior good timestamp is preserved as
    the last-known-good signal).
  - If the payload exists: open it read-only, run
    `verify_chain`, and on success update
    `chain_verified_at` to the current ISO-8601 instant. On
    failure, leave the column unchanged.
- Rich table output: machine_id, status (ok / BROKEN / MISSING),
  rows checked, payload state, error detail.
- Roll-up exit code: 0 if local chain ok AND every attached
  chain ok; 1 otherwise.

### Architecture

- New value objects in `infra/sync/imports_admin.py`:
  `ImportVerification` (per-import result) and
  `VerifyImportsReport` (roll-up with `all_ok` / `broken_count` /
  `missing_count` properties).
- `verify_imports` is a read-side attestation — it does NOT
  append to the LOCAL audit chain. Forcing a chain row per verify
  call would clutter the log; the per-row `chain_verified_at`
  timestamp is the right granularity for "when was this last
  attested."
- Wraps payload-open errors via `log_swallowed_error` so a
  corrupted file surfaces as a `(False, 0, error)` tuple rather
  than a stack trace.
- 7 new integration tests
  (`tests/integration/test_sync_verify_imports.py`) covering
  empty-list / one-healthy / chain_verified_at persistence /
  missing-payload / tampered-payload / mixed-healthy-and-broken
  / local-chain untouched.

### Notes

- Next under ADR-0013: v0.3.4 `apply` pre-flight check (refuse
  manifests with non-local claim_ids).

## [0.3.2] — 2026-05-16

Operator surface for the cross-machine inventory mount table:
`steward db imports list` and `steward db imports detach`. Lets
operators see what's attached and remove a stale attachment
without poking SQLite by hand. 379 tests passing (was 367 at
v0.3.1).

### Added

**`steward db imports list`** (`cli/db_cmd.py` +
`infra/sync/imports_admin.py::list_imports`):

- Rich table of every `attached_inventories` row: machine_id,
  hostname, exporter version, imported_at, audit_rows, payload
  status (ok / MISSING).
- Detects when the payload .db has been removed out-of-band
  (e.g. the imports volume disconnected) and surfaces it so the
  operator can clean up.

**`steward db imports detach <machine_id_prefix>`**
(`cli/db_cmd.py` + `infra/sync/imports_admin.py::detach_import`):

- Removes the row from `attached_inventories`, unlinks the
  payload .db file, and appends one `inventory_detached` audit
  row to the LOCAL chain.
- Accepts the full machine_id OR any unique prefix (mirrors how
  `inspect` resolves UUID prefixes in v0.2.12).
- Destructive — requires `--dry-run` or `--execute` per ADR-0002.
  `--dry-run` shows what would be detached; `--execute` does the
  removal.
- Best-effort cleanup of the now-empty
  `<imports>/<machine_id>/` directory.
- Handles the missing-payload edge case (operator deleted .db
  manually): row removal still proceeds, audit row records
  `payload_existed: False`.

### Architecture

- `infra/sync/imports_admin.py` carries pure functions
  (`list_imports`, `get_import`, `detach_import`) +
  `AttachedInventoryRow` / `DetachResult` dataclasses.
- `get_import` raises `ImportsAdminError` on missing or ambiguous
  prefix — both refusal paths are operator-facing.
- The CLI `imports` group is a nested typer subgroup under `db`:
  `steward db imports {list, detach}`.
- 12 new integration tests
  (`tests/integration/test_sync_imports_admin.py`) covering
  empty-list / one-row / payload-missing / full-id lookup /
  prefix lookup / missing-prefix refusal / ambiguous-prefix
  refusal / detach happy path / detach audit row / detach
  local-chain-intact / detach when payload missing / detach by
  prefix.

### Notes

- Next under ADR-0013: v0.3.3 `db verify --imports` — walks each
  attached inventory's audit chain + updates
  `attached_inventories.chain_verified_at`.

## [0.3.1] — 2026-05-16

Lands the **receiver side** of the cross-machine wire format:
`steward db import`. Unpacks a `tar.xz` envelope produced by
`steward db export`, blake3-verifies the payload + manifest,
verifies the imported audit chain, copies the payload .db into
`<data_dir>/imports/<exporter_machine_id>/<iso>.db`, and upserts
the row that future `db imports list / detach` and
`db verify --imports` will read. 367 tests passing (was 356 at
v0.3.0).

### Added

**`steward db import <envelope>`** (`cli/db_cmd.py` +
`infra/sync/importer.py`):

- Unpacks the envelope, parses + validates `manifest.json` against
  the v1 schema (`WIRE_FORMAT_VERSION`).
- Refuses if the wire-format-version is newer than this Steward
  supports.
- Same-machine refusal — operator cannot import an envelope that
  was exported from their own `meta.machine_id`.
- Re-extracts the payload to a staging path, blake3-verifies it
  against `manifest.payload.blake3`, and blake3-verifies the
  manifest bytes against the hash recorded in `checksums.txt`.
- Walks the payload's `audit_log` and verifies its hash chain
  independently. Refuses on any break.
- Cross-checks the payload's actual audit row count against the
  count in the manifest.
- Atomic move into
  `<data_dir>/imports/<exporter_machine_id>/<iso>.db`.
- Upserts the `attached_inventories` row (replace on second import
  from the same exporter machine_id; recorded via
  `replaced_existing=True`).
- Appends one `inventory_attached` audit row to the LOCAL chain
  with the exporter machine_id, payload path, blake3, and row
  counts.

### Architecture

- `infra/sync/importer.py` is symmetric to `exporter.py` — both
  share the manifest schema in `infra/sync/manifest.py`.
- `imports_dir()` helper in `infra/db/settings.py` resolves
  `<data_dir>/imports` (the data_dir is the existing
  STEWARD_DATA_DIR / platformdirs default).
- The payload file is NEVER opened writeable by Steward after
  import. Read-side fan-out (v0.3.5+) will open it via
  `ATTACH DATABASE … ?mode=ro` per ADR-0013.
- 11 new integration tests
  (`tests/integration/test_sync_importer.py`) covering happy path,
  same-machine refusal, payload tampering, manifest tampering,
  future wire-format-version, re-import semantics, local audit
  chain integrity, and payload-chain independent verification.

### Notes

- An ImportError class lives at `infra/sync/importer.py` as
  `ImportError_` (trailing underscore to avoid shadowing the
  builtin) but is re-exported from `steward.infra.sync` as
  `ImportError`. The CLI catches the package alias.
- Next under ADR-0013: v0.3.2 `db imports {list, detach}`.

## [0.3.0] — 2026-05-16

Opens the **v0.3 cross-machine sync** track. Lands ADR-0013
(cross-machine inventory wire format) and the first piece of the
write surface: `steward db export`. Reads the local inventory.db and
produces a portable tar.xz envelope another Steward instance can
attach read-only. 356 tests passing (was 338 at v0.2.14).

### Added

**ADR-0013 — Cross-machine inventory wire format**
(`docs/adr/0013-cross-machine-inventory-wire-format.md`):

- Wire format: SQLite payload + JSON manifest + blake3 checksums
  packaged in a tar.xz envelope.
- Import side: payload .db lives at
  `~/.local/share/steward/imports/<machine_id>/<iso>.db`, mounted
  read-only via `ATTACH DATABASE` with the `?mode=ro` URI flag.
- Audit chain integrity: each machine's chain verifies
  independently, never spliced into the local chain.
- Pull-don't-push (ADR-0009): `apply --execute` structurally cannot
  touch imported claims.
- Excludes embeddings / tiers / legacy_import_log /
  attached_inventories from the payload by default.

**Schema migration `0002_attached_inventories.py`** — adds the
`attached_inventories` table that v0.3.x import bookkeeping uses.
`schema_version` meta key now reads `0002_attached_inventories`.

**`steward db export`** (`cli/db_cmd.py` + `infra/sync/`):

- `steward db export` — writes a portable envelope to
  `<inventory_dir>/exports/inventory-<short_id>-<iso8601>.tar.xz`
  (default) or to `--out <path>`.
- `--with-embeddings` — keeps the embeddings tables in the payload.
  Default excludes them (large, model-version coupled).
- `--overwrite` — replace an existing envelope at the target path.
- Wraps `db backup` (v0.2.14) for the consistent snapshot, then
  strips excluded tables + `VACUUM`s, computes blake3, builds the
  manifest, and tars the three files into a tar.xz envelope.
- Each export appends a `db_export_created` audit row on the LIVE
  DB (target + envelope size + blake3 + row counts + duration).

### Architecture

- New `infra/sync/` namespace — cross-machine sync code (exporter
  today; importer / detach / verify in v0.3.1–v0.3.3).
- `infra/sync/manifest.py` carries the pydantic models that govern
  `manifest.json`. `WIRE_FORMAT_VERSION = 1`; bumping it requires
  an ADR.
- The exporter loads sqlite-vec on the snapshot connection only
  long enough to `DELETE FROM embeddings_vec` — the resulting file
  doesn't depend on the extension at attach time.
- Tar + LZMA (xz) is stdlib-only — no new runtime dependency.
- 12 new integration tests (`tests/integration/test_sync_exporter.py`)
  + 6 new unit tests (`tests/unit/infra/test_sync_manifest.py`).

### Notes

- The roadmap's v0.3 sprint sequence (in `docs/ROADMAP.md`) lays
  out v0.3.1–v0.3.5+ — import, imports list/detach, verify
  --imports, apply pre-flight, read-side fan-out.
- Per ADR-0013, the exported inventory NEVER drives
  `apply --execute` on the importing machine — query surface only.

## [0.2.14] — 2026-05-16

Adds `steward db backup` — one-shot, fully-consistent snapshot of the
inventory.db via SQLite's online-backup API. Defensive operator
tooling: take a snapshot before a risky `apply --execute` or a
manual policy edit. 338 tests passing (was 330 at v0.2.13).

### Added

**`steward db backup`** (`cli/db_cmd.py` + `infra/db/backup.py`):

- `steward db backup` — writes a snapshot to
  `<inventory_dir>/snapshots/inventory-<iso8601>.db` (default) or to
  a user-supplied `--out <path>`.
- `--overwrite` — replace an existing target. Default refuses.
- Uses `sqlite3.Connection.backup` — the SQLite-native online-backup
  API. Unlike `cp`, this is safe with concurrent writers:
  - WAL contents are merged into the snapshot.
  - Writers are not blocked.
  - The snapshot is a fully consistent point-in-time copy.
- Each snapshot appends a `db_backup_created` audit row on the
  SOURCE database (target path + bytes + duration). The snapshot's
  own audit chain is a prefix of the source's chain up to the
  backup instant.

### Architecture

- `infra/db/backup.py` carries the pure function (`backup_inventory_db`)
  with three explicit failure modes wrapped as `BackupError`:
  source-missing, target-exists-without-overwrite,
  parent-directory-missing. No auto-mkdir of the target's parent —
  operator decides where snapshots live.
- The function uses the project's `connect()` helper for the source
  (so PRAGMAs match the rest of the codebase) and stock
  `sqlite3.connect` for the target (sqlite-vec isn't auto-loaded
  into snapshots).

### Tests (+8 since v0.2.13)

`tests/integration/test_db_backup.py`:

- Happy path: snapshot opens independently with stock sqlite3 and
  has the same `permanodes` / `current claims` counts as the source.
- The `db_backup_created` audit row lands on the source's chain with
  the target path in its payload.
- `verify_chain` returns ok for BOTH source (after the new row lands)
  AND snapshot (which is a prefix of the chain). Snapshot row count
  is `≤ source - 1`.
- Refuse-to-overwrite raises `BackupError` and leaves the existing
  target file untouched.
- `--overwrite` replaces an existing target cleanly.
- Source-missing and parent-directory-missing both surface as
  `BackupError` with friendly messages.
- **Concurrent-writes safety**: a writer thread hammers the source
  with 50 audit appends while the backup runs at `pages_per_step=1`
  (so the backup yields between writes). The snapshot is still
  valid + verifies cleanly — this is the entire reason we picked
  `Connection.backup` over `shutil.copy`.

### Why now

The v0.2.3 replicate adapter (rclone) can copy inventory.db
off-machine, but it's policy-configured + scheduled. An operator
wants an ad-hoc "snapshot right now" before doing something risky.
`steward db backup` is that one-liner. Combined with
`steward db verify` (the gate that confirms a snapshot is valid),
this is the smallest correct "safety net" before
`steward apply --execute`.

## [0.2.13] — 2026-05-16

Adds `steward stats` — read-only aggregations over the inventory.
Six subcommands cover the queries operators have been typing into
`sqlite3` by hand. Every subcommand supports `--json`. 330 tests
passing (was 313 at v0.2.12).

### Added

**`steward stats`** (`cli/stats_cmd.py` + `infra/stats.py`):

- `steward stats` (no args) — overview: headline counts +
  permanode-with-most-duplicates count + largest permanode + top 5
  tiers + top 5 domains.
- `steward stats by-tier` — one row per tier with claim count,
  permanode count, and total bytes. Sorted by bytes DESC.
- `steward stats by-domain` — same shape, keyed on `claims.domain`.
  NULLs grouped together.
- `steward stats extensions [--limit N]` — top N file extensions
  by total bytes.
- `steward stats classifications [--limit N]` — top N classification
  labels by claim count.
- `steward stats duplicates [--limit N] [--min-claims K]` —
  permanodes with the most current claims. The dedup-candidate list.

All subcommands accept `--json` for scripted consumers (cron alerts,
the `tier-auditor` sub-agent, custom dashboards).

### Tests (+17 since v0.2.12)

- `tests/integration/test_stats.py` exercises every aggregator:
  by-tier ordering, by-domain NULL grouping, by-extension limit caps,
  by-classification surfacing synthetic labels, duplicate_permanodes
  filtering by min_claims, overview headline aggregation, empty-
  inventory zero behaviour for every entry point.
- CLI smoke tests on `stats_root` and `by_tier_cmd` confirm JSON
  output round-trips through the aggregators correctly.

### Why now

The v0.2.x adapter wave shipped 19 subcommands but the operator
still had to drop to `sqlite3` for "which tier has the most bytes?"
or "what are my top-N duplicates?" Bundling these as first-class
CLI surface saves typing AND gives sub-agents (per ADR-0012,
read-side only) a structured aggregation surface.

20 CLI subcommands now exposed; pre-flight (`make gates`) clean.

## [0.2.12] — 2026-05-16

Polish + closure release. Adds `steward inspect --json` / `--machine`
for scriptable / multi-machine inspection, and ships two new ADRs
documenting the v0.2.5 + v0.2.11 design decisions. 313 tests passing
(was 307 at v0.2.11).

### Added

**`steward inspect --json` + `--machine`** (`cli/inspect_cmd.py`):

- `--json` emits a single JSON document on stdout (with a `found`
  discriminator so absent permanodes are machine-distinguishable from
  ones without claims). Works with the rest of the existing `inspect`
  surface — hash, permanode id, or any claim's file_path.
- `--machine <id-or-prefix>` filters the claims list to a single
  machine_id. UUID prefix resolution matches `steward machines show`.
  Returns a distinct error (in JSON mode: `{"found": false, "error":
  "no machine_id matches ..."}`) when the prefix is wrong, so callers
  can tell "permanode not found" from "no machine matched."
- Together: `steward inspect <target> --json --machine <prefix>` is
  the read-only surface a sub-agent uses for permanode-by-machine
  introspection.

**ADR-0011 — MCP write surface design**
([`docs/adr/0011-mcp-write-surface.md`](adr/0011-mcp-write-surface.md)):

Captures the decision behind v0.2.5: use the MCP protocol's
`destructiveHint=True` annotation rather than token-based confirmation
or `confirm: True` parameters. Pairs the annotation with a
non-bypassable `mcp_write_invoked` audit row so MCP-driven mutations
are forensically distinct from CLI-driven ones. Explains why the
other two options were rejected.

**ADR-0012 — sub-agent scope: read-side, never `--execute`**
([`docs/adr/0012-sub-agent-scope.md`](adr/0012-sub-agent-scope.md)):

Captures the policy behind v0.2.11's bundled agents: they propose,
the operator executes. No bundled sub-agent invokes
`apply --execute`, `stash finalize`, `archive init`, or any MCP
destructive tool — agents may be spawned from inside other tasks
without the operator anticipating downstream actions, and they run
with full `Bash` (no MCP-style confirmation layer). Documents the
hand-off pattern + how new agents wanting write capability must
ship a separate ADR.

### Tests (+6 since v0.2.11)

`tests/integration/test_inspect_cli.py`:

- `--json` produces a parseable JSON document with the full
  `InspectResult` shape.
- `--json` on a missing target emits `{"found": false, ...}` with
  exit code 1.
- `--json` includes the audit_rows field as a list.
- `--machine` with a matching prefix keeps matching claims; the
  result still parses as JSON.
- `--machine` with a non-matching prefix emits a distinct error
  and exits 1.
- Default (non-JSON) plaintext output still exits 0 — the new flags
  are additive.

### Surface unchanged

19 CLI subcommands (unchanged from v0.2.11). 15 MCP tools (8 read +
7 write, unchanged from v0.2.5). 4 bundled sub-agents (unchanged
from v0.2.11). This release adds two new flags + two ADRs.

### Why now

The v0.2.x adapter wave is feature-complete. v0.2.7 added the docs
guards; v0.2.9 added the local bandit gate; v0.2.11 added the
sub-agents. The two ADRs are the missing closure documentation that
makes v0.2.5 + v0.2.11's design intent legible to a future
maintainer (or to the v0.3 cross-machine work). The `--json` flag
on `inspect` is the smallest concrete CLI improvement remaining
before v0.3.

## [0.2.11] — 2026-05-16

Bundles four Claude Code sub-agents under `.claude/agents/` —
`tier-auditor`, `promotion-planner`, `retire-decider`, `verifier`.
Each carries a focused system prompt that walks the operator (or an
LLM driving Steward) through a specific workflow without re-explaining
the system. Plus a CI gate that catches the most-common
agent-authoring mistakes. 307 tests passing (was 288 at v0.2.10).

### Added

**Sub-agents** (`.claude/agents/*.md`):

- **`tier-auditor`** — broad read-only health sweep across every v0.2.x
  adapter. Flags audit-chain breaks, scan errors, replicate/archive
  failures, stash entries past cooling-off, and surprising machine
  counts. Stops at the first critical finding; never mutates.
- **`promotion-planner`** — walks the operator through a Backup →
  live-tier promotion. Reads `promotion.yml`, generates the plan TSV,
  runs `apply --dry-run`, recommends an `--execute` batch size. Hands
  off the actual `--execute` call to the operator (ADR-0002).
- **`retire-decider`** — plans dedup-retire under `retention.yml`,
  spot-checks canonical safety on 3–5 representative rows, recommends
  a cooling-off window. Refuses to recommend `apply --execute` if any
  retiring claim lacks a surviving canonical elsewhere. Never calls
  `stash finalize`.
- **`verifier`** — the verification gauntlet: audit-chain check,
  SQLite integrity, content spot-check, stash verify, status alignment.
  Stops at the first critical failure. Produces a yes/no verdict with
  cited evidence.

Each agent has a YAML-frontmatter system prompt (~3–6 KB) with
explicit IS / IS NOT scope, a numbered checklist, an anomalies table,
and a structured output format. The bodies cite real Steward commands
from the v0.2.x surface so they stay current with the CLI.

**`docs/AGENTS.md`** — operator-facing index. One row per agent with
when-to-use / what-it-does / what-it-never-does, invocation snippets
via `Task(subagent_type=...)`, and the alignment story with ADR-0002
and ADR-0009.

### Tests (+19 since v0.2.10)

`tests/test_agents_consistency.py` — CI gate parametrized over every
`.claude/agents/*.md` file. Asserts:

- The agents directory exists.
- A hard-coded floor of required agents (the four shipped here) is
  present.
- Every agent file has parseable YAML frontmatter with a string
  `name` and `description`.
- The `name` matches the filename stem (Claude Code uses the stem
  for `Task(subagent_type=...)` dispatch).
- The body after the frontmatter is at least 500 characters — guards
  against stub agents shipping by accident.
- Any `tools:` list references real Claude Code tools (no typos).
- `docs/AGENTS.md` mentions every required agent.

### Why now

The v0.2.5 MCP write surface gave LLMs the ability to drive Steward,
and v0.2.10 added the dashboard, but neither bundled higher-level
expertise. An operator asking Claude "audit my inventory" or "plan a
photos promotion" still had to teach Claude the system every time.
The four bundled agents are the curated answer — focused, narrow,
read-side-by-default, and explicitly never bypassing operator-in-the-
loop on mutations.

No CLI surface change; the existing 19 subcommands are unchanged.
Sub-agents are an authoring + ergonomics layer on top.

## [0.2.10] — 2026-05-16

Adds `steward dashboard` — single-page HTML status dashboard served
over loopback HTTP. Renders the same data `steward status` prints to
the terminal, with auto-refresh + a `/status.json` endpoint. 288 tests
passing (was 279 at v0.2.9).

### Added

**HTML dashboard** (`steward dashboard`):

- `infra/dashboard/render.py` — pure `StatusReport → str` renderer.
  Single self-contained HTML document: inline CSS, no external assets,
  no JavaScript framework, no build step. Card-style layout, one
  section per `steward status` group. Adversarial input is HTML-escaped.
- `infra/dashboard/server.py` — stdlib `http.server.BaseHTTPRequestHandler`
  subclass. Three endpoints:
  - `GET /` → rendered HTML with optional `<meta http-equiv="refresh">`
  - `GET /status.json` → same shape as `steward status --json`
  - `GET /healthz` → `200 ok` (cheap liveness check)
  All other paths return 404. Custom `log_message` no-op so per-request
  logs don't pollute stdout under auto-refresh.
- `cli/dashboard_cmd.py` — `steward dashboard [--host H] [--port P]
  [--refresh-seconds N] [--open]`. Bind defaults to `127.0.0.1`.
  `--open` pops the URL in the default browser via stdlib `webbrowser`.

### Design

Why stdlib `http.server` rather than FastAPI / Starlette? The
dashboard's job is "show the operator what they'd see in `steward
status`, in a browser, with auto-refresh." Anything more — auth,
sessions, write surface — belongs in a v0.3 build with proper auth.
Stdlib keeps zero runtime deps and matches the project's
"infrastructure when necessary, not by default" posture.

Per ADR-0009 (pull-don't-push), the dashboard is **read-only**.
Mutations stay in CLI + MCP-write paths where the operator's
confirmation is structural.

### Tests (+9 since v0.2.9)

- Renderer (7):
  - Emits a complete `<!DOCTYPE html>` document.
  - Includes / omits `<meta http-equiv="refresh">` per `refresh_seconds`.
  - Surfaces all six sections (inventory, latest scan, stash,
    last replicate, last archive, audit chain).
  - Renders "broken" banner + BROKEN section when audit chain corrupt.
  - HTML-escapes adversarial strings (db.path, run.timestamp,
    run.policy_name) — raw tags never leak.
  - Renders adapter payload counters with binary-unit formatting.
- Server (2):
  - End-to-end: spins up `DashboardServer` on an ephemeral port,
    requests `/`, `/status.json`, `/healthz`, `/nonsense`. Asserts
    status codes + content types + rendered title + sections + refresh.
  - Synthesized `replicate_end` / `archive_end` audit rows flow into
    the rendered HTML on the next request.

### CLI surface (19 subcommands)

`db`, `import`, `scan`, `policy`, `inspect`, `apply`, `stash`,
`classify`, `watch`, `embed`, `search`, `mcp`, `photos`, `replicate`,
`archive`, `status`, `schedule`, `machines`, **`dashboard`** (new).

## [0.2.9] — 2026-05-16

Activates the `machine_id` column that every claim / scan_run / audit
row has carried since v0.1.0. ADR-0008 deferred the activation to
v0.3+; v0.2.9 ships the first read-only surface so the foundation is
operator-visible before any cross-machine work lands.

Plus a small DX fix: bandit is now part of the local `make gates`
target, closing the gap that caused v0.2.8's CI failure. 279 tests
passing (was 273 at v0.2.8).

### Added

**Machine awareness** (`steward machines`):

- `infra/machines.py` — pure SQL aggregator over claims / scan_runs /
  audit_log keyed on `machine_id`. Three entry points:
  - `list_machines(db_path)` → `[MachineSummary]` with counts +
    first/last seen + an `is_current` flag against `meta.machine_id`.
  - `get_machine(db_path, machine_id)` → `MachineDetails` (summary
    + recent scan_runs + recent audit) or `None`.
  - `count_machines(db_path)` → cheap distinct-count for the status
    report.
- `cli/machines_cmd.py` — `steward machines list` and
  `steward machines show <id-or-prefix>`. UUID prefix resolution
  for operator convenience.
- MCP read tools added to the existing server:
  - `list_machines()` → returns the JSON-friendly list.
  - `get_machine(machine_id)` → returns the details dict
    (or `{"found": false, ...}` for unknown).
- `status` report now includes a "machines" count in the inventory
  section (the JSON shape gains `inventory.machines`).

**Local-gates fix** (`Makefile`):

- New `make bandit` target — runs CI's exact bandit invocation
  (`-r src/steward/ -ll --skip B101 -x tests`).
- New `make gates` target — chains every CI gate
  (`lint typecheck imports silent-catch test bandit`) so
  pre-flight catches what v0.2.8's bandit failure caught only in CI.

### Tests (+6 since v0.2.8)

Integration tests under `tests/integration/test_machines.py`:

- Fresh inventory → zero machines.
- After a scan_run → one machine, marked `is_current=True`, with the
  expected claim / scan_run / audit counts.
- Synthetic foreign audit row (via `repo_audit.append` with a UUID
  that isn't ours) → two machines, sorted by last_seen DESC, with
  the foreign one having zero claims.
- `get_machine` returns `None` for unknown ids; returns details +
  recent activity for known ids.
- `count_machines` matches `len(list_machines)`.
- Status report's `inventory.machines` count tracks the aggregator.

### Why now

Every claim, scan_run, and audit row has had a `machine_id` column
for nine patch releases — written but unread. Surfacing it now (a)
catches any single-machine-only assumptions before v0.3 cross-machine
work lands, and (b) gives the operator a sanity-check ("yes, my
machine_id is the one I think it is") with zero schema changes.

The DX side fix turns the "always run bandit before push" lesson
from v0.2.8 into local infrastructure.

## [0.2.8] — 2026-05-16

Adds `steward schedule` — bundled launchd plists + a CLI that
materializes, installs, and manages them through `launchctl`. Closes
the "how do I make these adapters run on a schedule" gap that every
v0.2.x release left open. 273 tests passing (was 257 at v0.2.7).

### Added

**Bundled launchd templates** (`src/steward/launchd/`):

- `nightly-archive.plist` — daily restic snapshot at 02:15.
- `nightly-replicate.plist` — daily rclone replicate at 03:00.
- `weekly-verify.plist` — Sunday 04:00 `steward db verify`.

Templates carry three placeholders: `{HOME}`, `{STEWARD_BIN}`,
`{LOG_DIR}` — substituted at install time.

**Schedule module** (`infra/schedule/`):

- `templates.py` — discovery + `<Label>` parsing + materialization +
  `write_resolved_plist` (with mode 0644 + parent dir creation).
- `launchctl.py` — subprocess wrapper for `bootstrap` / `bootout` /
  `print`. Targets `gui/<uid>` domain. Raises
  `LaunchctlNotInstalledError` on non-macOS hosts.

**CLI** (`cli/schedule_cmd.py`):

- `steward schedule list` — bundled templates + installed status.
- `steward schedule show <name> [--home / --steward-bin / --log-dir]`
  — preview the materialized plist.
- `steward schedule install <name> --execute` — write resolved plist
  to `~/Library/LaunchAgents/` + `launchctl bootstrap`. Default
  rejects without `--execute` (per ADR-0002).
- `steward schedule uninstall <name> --execute` — `bootout` + remove
  the plist file.
- `steward schedule status <name>` — `launchctl print` output.

### Tests (+16 since v0.2.7)

Unit tests under `tests/unit/infra/`:

- **Templates** (10):
  - Bundled set includes the expected three names.
  - Every plist parses as valid XML + has a `<plist>` root + a top-level
    `<dict>` + a `Label` in the `com.cerid.steward.*` domain.
  - Substitution replaces all three placeholders; result still parses.
  - Substitution with no overrides uses `$HOME`.
  - Unknown template name raises `TemplateNotFoundError` listing
    available names.
  - `write_resolved_plist` writes file, sets mode 0644, creates parent
    directories, returns the correct label.
  - `installed_plist_path` lands at `~/Library/LaunchAgents/<label>.plist`.
- **launchctl wrapper** (6):
  - `launchctl_available` reflects `shutil.which`.
  - Missing-binary path raises `LaunchctlNotInstalledError` for
    bootstrap / bootout / print.
  - Argv construction uses `gui/<uid>` domain with the plist path or
    label as appropriate.

Tests run cross-platform — Linux CI has no launchctl; the subprocess
wrapper is mocked.

### Why now

After the v0.2.0–v0.2.7 adapter wave, every adapter has a scheduling
use case but no Steward-side support. The QUICKSTART previously hand-
rolled a single combined launchd plist as illustration; v0.2.8 ships
three focused plists + the CLI to manage them, removing the
hand-rolling step.

Per ADR-0009 (pull-don't-push), `steward schedule install` is a
system mutation — it requires `--execute` like every other
destructive subcommand.

## [0.2.7] — 2026-05-16

Comprehensive operator-facing documentation. README rewrite that
catches up with seven shipped patch releases, plus a new
`docs/QUICKSTART.md` walkthrough and `docs/ROADMAP.md`. 257 tests
passing (was 249 at v0.2.6).

### Changed

- **`README.md`** — full rewrite. The previous version still claimed
  "v0.1 in flight" while the codebase was at v0.2.6. New layout
  covers the why, the 15-row CLI surface table, a 3-minute quick
  start, the five-plane architecture, the actual current repo
  layout, and import-linter contracts.
- **`steward mcp` help text** — was "Read-only MCP server"; updated
  to acknowledge the v0.2.5 write surface ("MCP server exposing
  Steward's inventory (read tools + write tools with destructive
  hints).").

### Added

- **`docs/QUICKSTART.md`** — end-to-end operator walkthrough covering
  every shipped subcommand. Sections: setup, scan + classify +
  dedup-retire, promotion, Photos.app bulk import, local search,
  replication, archive, continuous incremental scan, MCP integration
  (with a Claude Desktop config snippet), operator dashboard, sample
  launchd schedule.
- **`docs/ROADMAP.md`** — per-release inventory for v0.1 / v0.2,
  next-up v0.3 milestones, and the deferred-no-commitment list.
- **`tests/test_docs_consistency.py`** — guards that prevent README /
  QUICKSTART drift from CLI growth:
  - Every typer-registered subcommand must appear in README.md.
  - Every typer-registered subcommand must appear in
    `docs/QUICKSTART.md`.
  - README forbids "Status: v0.1 in flight" and similar stale
    markers.
  - `CHANGELOG.md`'s first `## [version]` header must match
    `_version.__version__`.
  - `docs/ROADMAP.md` exists, is non-trivial, and acknowledges every
    milestone release.

### Why

After seven focused patch releases (v0.2.0–v0.2.6) the operator
surface grew from 8 to 16 subcommands; the README missed every one
of them. The doc consistency tests are cheap insurance against the
same drift recurring as v0.3 lands.

## [0.2.6] — 2026-05-16

Adds `steward status` — single-pane operator dashboard. Read-only,
queries existing tables, complements the MCP read tools with a CLI
summary the operator can `grep` or pipe through `jq`. 249 tests
passing (was 243 at v0.2.5).

### Added

**Status aggregator** (`infra/status.py`):

- `collect_status(db_path)` returns a :class:`StatusReport` with six
  sections: DB file info, inventory counts, latest scan_run, stash
  summary, last replicate/archive runs, audit-chain status.
- Each aggregator is its own pure function — easy to test in isolation.
- The stash summary is computed from the audit_log itself (no separate
  stash-state table). An entry is "in flight" iff a ``stash`` audit row
  exists for it without a matching ``stash_finalized`` / ``stash_restored``
  row for the same ``(manifest_run_id, destination_path)``.
- The "last replicate / archive" sections walk back through
  ``audit_log`` for the most-recent ``replicate_end`` / ``archive_end``
  row and parse its payload — no separate run-history table needed.
- `status_to_dict(report)` → JSON-friendly representation used by
  the ``--json`` CLI flag.

**CLI** (`cli/status_cmd.py`):

- `steward status` — Rich tables per section.
- `steward status --json` — single JSON object on stdout (suitable for
  `jq` / scheduled-job consumers).
- Exits non-zero when the audit chain check fails — CI/cron alerts
  trigger without needing to parse output.

### Tests (+6 since v0.2.5)

- Empty inventory reports zeroes everywhere + audit_chain ok.
- Populated inventory reflects the latest scan AND synthetic
  replicate_end / archive_end audit rows (with payload values
  flowing into the report).
- JSON round-trip via `json.dumps(status_to_dict(...))` /
  `json.loads(...)` preserves structure.
- Broken audit chain (manually corrupted row_hash, with the
  append-only trigger dropped for the test) surfaces as
  `audit_chain.ok = False` with a populated `error` string.
- Format helper for byte sizes: KiB / MiB / GiB / TiB plus the
  sub-KiB raw-byte path.

### Operator workflow

```bash
# Quick health check.
steward status

# JSON for cron / alerting.
steward status --json | jq '.audit_chain'
# {"rows_checked": 1234, "ok": true, "error": null}

# Non-zero exit on broken chain → use in launchd error handler.
steward status >/dev/null || /usr/bin/say "Steward audit chain broken"
```

## [0.2.5] — 2026-05-16

Adds the **MCP write surface** — seven new MCP tools that wrap the
existing CLI orchestrators (apply, replicate, archive snapshot/init,
stash finalize/restore) with `destructiveHint=True` annotations so
MCP clients (Claude Desktop, etc.) can surface confirmation UI
before invocation. 243 tests passing (was 235 at v0.2.4).

### Added

**MCP write handlers** (`infra/mcp/write_handlers.py`):

- `replicate_dry_run(policy)` / `replicate_execute(policy)` — wrap
  `run_replicate`; dry_run flag propagates to rclone.
- `archive_snapshot_dry_run(policy)` / `archive_snapshot_execute(policy)`
  — wrap `run_archive_snapshot`; dry_run flag propagates to restic.
- `archive_init_execute(policy)` — wrap `run_archive_init`; creates
  encrypted repositories.
- `stash_finalize_execute(run_id, cooling_off_days, force)` — wrap
  `finalize_stash`; deletes stashed files after cooling-off.
- `stash_restore_execute(run_id)` — wrap `restore_stash`; moves files
  back to their original locations.

Each handler appends one `mcp_write_invoked` audit row (actor =
`steward-mcp`) before delegating to the orchestrator. The
orchestrator's own audit chain continues unchanged — downstream
queries can distinguish MCP-invoked runs from CLI-invoked ones by
looking for the wrapping marker.

**FastMCP annotations** (`infra/mcp/server.py`):

- Dry-run tools carry `ToolAnnotations(readOnlyHint=True,
  destructiveHint=False)`. MCP clients can call them freely.
- Execute / init / stash tools carry `ToolAnnotations(readOnlyHint=False,
  destructiveHint=True, idempotentHint=False)`. Real clients surface
  these with a confirmation UI per the MCP protocol's tool-annotation
  contract.

Tool annotations are **hints**, not enforcement — the operator stays
in the loop per ADR-0002 because:

1. The MCP client (Claude Desktop, etc.) is expected to honour the
   destructive hint with a confirmation prompt.
2. The destructive tools delegate to orchestrators that already
   require `--dry-run` or `--execute` semantics; there's no separate
   "execute-without-flag" path.
3. The wrapping `mcp_write_invoked` audit row provides a
   tamper-evident record of every MCP-driven mutation.

### Tests (+8 since v0.2.4)

- Replicate dry-run records the right `mcp_write_invoked` payload AND
  the orchestrator's `replicate_start` / `replicate_end` chain still
  lands.
- Replicate execute passes `dry_run=False` through to the orchestrator
  (verified by capturing the underlying rclone call).
- Archive snapshot dry-run round-trips through the orchestrator with
  the captured `snapshot_id` + `data_added` surfaced.
- Archive init dedupes repositories from the policy and invokes
  `restic init` once per unique repo.
- Stash finalize / restore are no-ops for unknown `run_id` (no
  exceptions, no audit pollution beyond the `mcp_write_invoked` row).
- The FastMCP server lists every expected write tool with the
  documented hints — five `destructiveHint=True` + two
  `readOnlyHint=True` dry-run tools.
- The read surface from v0.2.0 is unchanged (all eight tools still
  registered).

### Operator workflow (with Claude Desktop or equivalent MCP client)

```
1. Claude calls `inventory_stats` (read; safe)
2. Claude proposes a replication run, calls `replicate_dry_run`
   (read-side; rclone --dry-run; LLM sees what would change)
3. Claude calls `replicate_execute` — client surfaces
   "this is destructive, confirm?" — operator approves
4. Replication runs; `mcp_write_invoked` + `replicate_*` audit rows
   land
```

## [0.2.4] — 2026-05-16

Adds restic-backed encrypted archive — `steward archive snapshot`
creates content-addressable, deduplicated, encrypted backups of
selected tiers. Complements v0.2.3's rclone replication (plaintext
mirror) with an archive tier suitable for long-retention storage.
235 tests passing (was 209 at v0.2.3).

### Added

**Archive adapter** (`steward archive`):

- `core/policy/schema.py` — new `ArchivePolicy`, `ArchiveDefaults`,
  `ArchiveSource` pydantic classes. Loader + kind-dispatch updated
  to recognise the new policy type. Password handling via
  `password_command` (preferred — Keychain-backed on macOS) or
  `password_file`. Steward never reads or echoes the actual password.
- `infra/archive/restic.py` — subprocess wrapper. Three operations:
  `init`, `backup`, `snapshots`. Composes argv with `--json` always.
  Parses the final `message_type=summary` line for backup operations
  (snapshot_id, files_new, data_added, total_bytes_processed). Parses
  the JSON array for `restic snapshots --json`. Hard timeout per
  subprocess (default 2 h, configurable per policy).
  `ResticNotInstalledError` with a friendly install hint when the
  binary isn't on PATH.
- `infra/archive/runner.py` — top-level policy runners:
  - `run_archive_snapshot` — iterates enabled sources, invokes `restic
    backup` per source, brackets the work with `archive_start` /
    `archive_source` (per source) / `archive_end` audit entries.
  - `run_archive_list` — `restic snapshots` per UNIQUE repository
    (dedup'd in policy-declaration order); merges results with a
    synthetic `_repository` tag on each snapshot for cross-repo
    listings. Audit pair: `archive_list_start` / `archive_list_end`.
  - `run_archive_init` — `restic init` per unique repository.
    `archive_init_start` / `archive_init_repo` (per repo) /
    `archive_init_end`.
- `cli/archive_cmd.py` — four subcommands:
  - `snapshot --policy <yml> {--dry-run|--execute}` (default rejects,
    per ADR-0002).
  - `list --policy <yml>` (read-only; renders Rich table).
  - `init --policy <yml> --execute` (creates encrypted repos;
    `--execute` required since repo creation is structural).
  - `show --policy <yml>` (renders policy YAML).
- `policies/archive.yml` — bundled default targeting
  `/Volumes/Backup/_steward-archive` with macOS Keychain-backed
  password command (`security find-generic-password -s steward-restic
  -w`).

### Tests (+26 since v0.2.3)

- 7 unit on the loader (minimal/full YAML, forbid-extra, invalid
  fields, timeout floor, defaults round-trip, required source fields,
  kind discriminator).
- 12 unit on the restic wrapper (env construction with each password
  variant + parent-env passthrough, backup-summary parser variants
  including last-summary-wins / no-summary / mixed-non-JSON-lines,
  snapshots-list parser including empty / non-array / non-dict-entry
  edge cases, init-summary parser).
- 7 integration on the runners (snapshot audit-per-source bracketing,
  disabled-source skipping, failure aggregation, list dedupes repos
  + tags each snapshot with `_repository`, list records per-repo
  failures, init dedupes repos + brackets with the right audit chain).

### Operator workflow

```bash
# One-time setup: stash password in keychain, then init the repo.
security add-generic-password -a "$USER" -s steward-restic \
  -w "<your-restic-password>"
steward archive init --policy archive.yml --execute

# Then snapshot on a schedule (launchd, cron, manual):
steward archive snapshot --policy archive.yml --execute

# Inspect what's been backed up:
steward archive list
```

This pairs with `steward replicate run` (v0.2.3): replicate keeps a
plaintext mirror in step with operator changes; archive keeps a
deduplicated, encrypted snapshot history for point-in-time recovery.

## [0.2.3] — 2026-05-16

Adds rclone-backed replication — `steward replicate run` mirrors
inventory.db + tier roots to off-machine destinations (NAS / cloud)
through a YAML policy. First step toward closing operator-pending
item #4 (CCC config) by replacing it with a tool-agnostic Steward
command. 209 tests passing (was 190 at v0.2.2).

### Added

**Replication adapter** (`steward replicate`):

- `core/policy/schema.py` — new `ReplicationPolicy`,
  `ReplicationDefaults`, `ReplicationSource` pydantic classes. Loader
  + `kind` dispatch updated to recognise the new policy type.
- `infra/replicate/rclone.py` — thin subprocess wrapper around the
  `rclone` CLI. Builds argv with `--use-json-log` + per-minute
  one-line stats, captures stderr tail (4 KiB), parses the final
  `stats` block from the JSON log lines, and enforces a per-call
  timeout (default 1 h, configurable per policy). Friendly
  `RcloneNotInstalledError` when the binary isn't on `PATH`.
- `infra/replicate/runner.py` — top-level `run_replication`. Iterates
  enabled sources, invokes rclone once per source, brackets the work
  with `replicate_start` / `replicate_source` / `replicate_end`
  audit-log entries. Disabled sources (policy `enabled: false`)
  produce no audit entry and no rclone invocation.
- `infra/replicate/orchestrate.py` — connect/commit facade for the
  CLI. Resolves `--policy` either as a full path or a bundled
  filename under `src/steward/policies/`.
- `cli/replicate_cmd.py` — `steward replicate run --policy <yml>
  {--dry-run|--execute}` (default rejects, per ADR-0002) and
  `steward replicate show --policy <yml>`. Exits non-zero if any
  source failed (CI/cron-friendly).
- `policies/replication.yml` — bundled default: mirror inventory.db
  + user-data dir to `/Volumes/Backup/_steward-mirror/`.

### Tests (+19 since v0.2.2)

- 7 unit on `ReplicationPolicy` loader (minimal/full YAML round-trip,
  forbid-extra, invalid mode, timeout floor, defaults match between
  pydantic + bundled YAML, required-source fields).
- 7 unit on the rclone command-builder + JSON-log stats parser
  (copy default, dry-run flag passthrough, sync mode override,
  includes-before-excludes ordering, extra_args appended,
  last-stats-block-wins, non-numeric-fields-dropped, empty-stderr
  returns {}).
- 5 integration on the runner (audit bracketing per source, disabled
  sources skipped without invocation, failure aggregation across
  sources, end-audit payload carries aggregate counters).

### Operator note

Replacement for the "configure CCC" runbook item:

```bash
steward replicate run --policy replication.yml --dry-run
# verify the plan, then:
steward replicate run --policy replication.yml --execute
```

The bundled `replication.yml` targets `/Volumes/Backup/_steward-mirror/`
out of the box. Operators copy it to
`~/.config/steward/policies.d/replication.yml` to add per-host
remotes (B2, S3, sftp via rclone backends).

## [0.2.2] — 2026-05-16

Completes the Photos.app bulk-import workflow that v0.2.1 started.
Adds `steward photos plan` — given a staging dir and a Photos library
(both already inventoried), it groups staging files by parent dir,
classifies each as "new to library" vs "already in library" vs
"unknown" (not yet scanned), and emits the exact `osxphotos import`
command the operator runs per group. 190 tests passing (was 182 at
v0.2.1).

### Added

**Photos import plan** (`steward photos plan`):

- `infra/photos/plan.py` — `plan_photos_import(con, source_root,
  library_path)` returns a :class:`PhotosImportPlan` (groups by parent
  dir; each group lists new files, already-in-library files, and
  unknown files plus a rendered osxphotos command). `write_plan_tsv`
  emits one row per group.
- Hash-based "already in library" lookup: a staging claim's
  `canonical_hash` is checked against the set of permanode hashes
  currently classified as `photos-app:%` — no re-hashing required.
  The plan runs in milliseconds even on large inventories.
- Unknown bucket surfaces staging files that have no claim yet — the
  operator sees them in the report and knows to run `steward scan
  --root <source>` before re-running plan. Silent omissions are
  impossible.
- Suggested osxphotos command matches the recipe from
  `analyses/import-workflow.md`: `osxphotos import --library <p>
  --walk --skip-dups --album '{filepath.parent.name}' <parent_dir>`.
  Embedded double quotes in paths are escaped.

**CLI**: `steward photos plan --source <dir> --library <p> [--out <tsv>]`.
Prints group counts + totals; writes TSV (parent_dir, new_count,
already_count, unknown_count, new_bytes, osxphotos_command) when
`--out` is given.

### Tests

190 total (was 182 at v0.2.1). +8 photos plan integration tests:

- empty staging → zero groups,
- unscanned staging files surface as `unknown`,
- hash-match between staging file + library asset routes to
  `already_in_library`; non-match routes to `new`,
- grouping is by parent_dir, sorted ascending,
- plan is scoped to source_root (claims outside don't leak in),
- osxphotos command includes `--walk` / `--skip-dups` / album template,
- double quotes in paths get escaped,
- TSV emission has the documented columns.

### Operator note

The full Photos workflow is now a three-step sequence:

```
steward scan --root <staging>
steward photos inventory --library <p>
steward photos plan --source <staging> --library <p> --out plan.tsv
```

The operator then iterates the TSV and runs each `osxphotos_command`
column entry. Per ADR-0009 (pull-don't-push), Steward never invokes
`osxphotos import` itself — the operator stays in the loop on every
mutation.

## [0.2.1] — 2026-05-16

Adds the Photos.app library inventory adapter — closes operator-pending
item #3 from the sprawl-audit handoff (Bulk app imports). One new
subcommand, one new optional dep, no schema changes. 182 tests passing
(was 175 at v0.2.0).

### Added

**Photos.app library inventory** (`steward photos inventory`):

- `infra/photos/inventory.py` — walks a `.photoslibrary` via
  `osxphotos.PhotosDB`. For each asset whose `path` is on disk: hashes
  via the standard `HashLadder` (xxh3 fast / blake3 archive), upserts
  a permanode, inserts a claim with:
  - `domain = "photos"`
  - `classification = "photos-app:<uuid>"` (the stable Photos asset UUID)
- iCloud-only assets (where `osxphotos` reports `path=None` because the
  original isn't downloaded) are counted in `assets_skipped_missing_path`
  and never produce a claim — Steward only inventories files it can
  actually hash.
- Single-pass walker: the iCloud-skipped count and the on-disk inventory
  share one `PhotosDB.photos()` iteration. Avoids paying the multi-second
  PhotosDB open twice on real 50k-asset libraries.
- `PhotosAsset` value object carries `uuid`, `path`, `original_filename`,
  `size_bytes`, `is_movie`, `is_edited` — enough for downstream policies
  to classify by media type.
- Friendly `OsxphotosNotInstalledError` when the optional dep isn't
  installed; CLI converts it into a one-liner hint.

**CLI**: `steward photos inventory --library <p> [--limit N]`. The
`--limit` flag caps how many assets are processed (useful for
smoke-testing a giant library) while still completing the iCloud-skipped
tally over the full asset set.

### Dependencies

- `osxphotos>=0.69,<1` is **not** added to required deps — the adapter
  lazy-imports it via the same pattern as the ONNX embedder. Operators
  install once with `pip install osxphotos` or `brew install osxphotos`.

### Tests

182 total (was 175 at v0.2.0). +7 photos integration tests using a
synthetic `osxphotos` module:

- friendly error when osxphotos is missing,
- `iter_photos_assets` filters iCloud-only entries,
- `inventory_photos_library` writes one claim per on-disk asset with
  the right `domain`/`classification`,
- scan_start/scan_end audit pair brackets the work,
- `--limit` caps assets but still tallies skipped iCloud entries,
- missing-on-disk path is counted in `assets_errored`, not a crash,
- macOS-gated real-library smoke at `--limit 5` against the operator's
  Photos library (skipped on Linux CI / hosts without the library).

### Note for v0.3

The complementary `steward photos plan` (compare a staging dir against
a Photos library to emit an `osxphotos import` manifest) lands in v0.3.
v0.2.1 ships only the inventory side; that alone closes the inventory
half of the operator's pending Photos workflow.

## [0.2.0] — 2026-05-16

The v0.2 milestone slice. Four new feature areas land in one release:
fsevents watcher, disk-image/7z/RAR container handlers, local
embeddings + semantic search, and a read-only MCP server. 175 tests
passing (was 112 at v0.1.2). No breaking changes to v0.1.x CLI
subcommands; everything new is additive.

### Added

**fsevents watcher** (`steward watch`):
- `core/scanner/watcher.py` — pure `WatcherProtocol` + `FileEvent` /
  `EventBatch` value objects. Was a stub in the original plan; now
  the canonical interface.
- `infra/scanner/fsevents_watcher.py` — watchdog-backed adapter.
  FSEvents on macOS / inotify on Linux. Applies the scanner skiplist,
  debounces bursts (default 750 ms quiet period), thread-safe drain.
- `infra/scanner/incremental.py` — `scan_paths()` runs a scan_run
  over an explicit list of files (no tree walk). Reuses
  `walker._process_file` so resume cache + hash ladder + claim writes
  are identical to a full scan.
- `cli/watch_cmd.py` — `steward watch --root <p> [--once]
  [--debounce-ms N] [--idle-seconds N]`. Per ADR-0009 (pull-don't-
  push), the watcher refreshes inventory only — it never auto-applies.

**Disk-image + 7z/RAR container handlers** (closes v0.1.1 gap):
- `.dmg / .sparseimage / .iso / .img / .cdr` — mounted via `hdiutil
  attach -nobrowse -readonly`, walked, then `hdiutil detach -force`
  in a finally block.
- `.7z / .rar` — extracted via `unar -force-overwrite -no-recursion`
  into a tempdir, walked, then `rmtree`.
- Tool-missing path (Linux CI without hdiutil/unar) → `containers_skipped`
  with a structured log entry. Behaviour is unchanged for those archives.
- 10-min hdiutil timeout / 30-min unar timeout guard against hung
  subprocess.

**Local embeddings + semantic search** (`steward embed`, `steward search`):
- `core/embed.py` — pure protocol: `Embedding`, `EmbedderInfo`,
  `EmbedRequest`, `EmbedderProtocol`, `to_blob` / `from_blob`,
  `build_permanode_text`. 384-dim, matching the schema 0001 vec0 slot.
- `infra/embed/stub.py` — deterministic `StubEmbedder`. blake2b-seeded,
  L2-normalised. No model download required; used by CI.
- `infra/embed/onnx.py` — `OnnxE5Embedder`. Lazy-loads onnxruntime +
  tokenizers; expects `model.onnx` + `tokenizer.json` under
  `~/.cache/steward/models/<model>/`. Raises a friendly
  `OnnxModelNotFoundError` with a `huggingface-cli download …` hint
  when the model isn't installed.
- `infra/embed/writer.py` — `write_embedding` keeps `embeddings` and
  `embeddings_vec` in lockstep. vec0 doesn't support `INSERT OR
  REPLACE` on its PK so the writer `DELETE`s then `INSERT`s in one
  transaction. `embed_permanodes_batch` walks current claims and
  skips permanodes already embedded for the configured model.
- `infra/embed/search.py` — `semantic_search` runs a vec0 KNN, scoped
  to the embedder's `(model_name, model_version)` so cross-model
  drift returns 0 results rather than meaningless ones.
- `cli/embed_cmd.py` / `cli/search_cmd.py` — `--backend {stub,onnx}`,
  `--model-name`, `--model-dir`. Search output is a Rich table.

**Read-only MCP server** (`steward mcp`):
- `infra/mcp/handlers.py` — eight read-only handlers, each opens its
  own `connect(..., read_only=True)`. Per ADR-0002, no write surface
  exists at all.
  - `inventory_stats`, `find_permanode_by_path`, `find_permanode_by_hash`,
    `get_permanode`, `list_policies`, `show_policy`, `recent_scan_runs`,
    `tail_audit_log`.
- `infra/mcp/server.py` — FastMCP wiring. Each tool is a thin shim that
  calls one handler.
- `cli/mcp_cmd.py` — `steward mcp [--transport stdio|http] [--host H]
  [--port P]`. Defaults to stdio so desktop LLM clients pick it up
  natively.

### Dependencies

- Added `watchdog>=4.0,<5` (fsevents/inotify adapter).
- Added `mcp>=1.0,<2` (Anthropic MCP SDK).
- `onnxruntime`, `tokenizers`, `huggingface-hub`, `numpy` are NOT
  added — the ONNX embedder lazy-imports them so the install is
  operator-driven (only the user who runs `--backend onnx` pays
  the ~100 MiB).

### Tests

175 passing (was 112 at v0.1.2). Distribution:

- +20 watcher (8 unit on `FileEvent` / `EventBatch` / `WatcherProtocol`
  + 12 integration on fsevents observation + `scan_paths` flow).
- +5 container handlers (skip-when-tool-missing for hdiutil + unar,
  bogus-DMG error path, `_walk_extracted_tree` records relative
  member paths, partition invariant) + 1 real-DMG smoke gated on
  macOS.
- +22 embeddings (10 unit on value objects + stub determinism +
  protocol runtime check, 12 integration on writer idempotence,
  batch skip/limit/reembed-all semantics, semantic search ordering
  + model-version filter + empty-state).
- +16 MCP handlers (one per tool, plus a FastMCP-registration smoke
  test asserting all eight tools are surfaced to the LLM client).

### Schema

No migrations. v0.2 reuses the schema 0001 `embeddings` and
`embeddings_vec` slots that were defined in v0.1 but not yet written
to. The "machine_id ready for v0.3" assertion from ADR-0008 still
holds.

## [0.1.2] — 2026-05-16

v0.1.x burn-down complete. Two additional sprawl-audit script ports +
the migration-backlog doc that accounts for every script in
`~/sprawl-audit/scripts/`. No schema changes, no breaking CLI changes.

### Added

**Sprawl-audit script ports (v0.1.x burn-down):**
- `verify_stash_dedup.py` → `steward stash verify --run-id <id>`. Per-entry
  status (`ok` / `dst-missing` / `src-still-present` / `no-canonical-elsewhere`
  / `no-permanode`). Exits 1 if any entry is non-ok. Optional `--out <tsv>`
  report; `--also-exclude <prefix>` for verifying multiple related stash
  groups as a unit.
- `recovered_retire.py` → bundled `policies/recovered.yml` + new
  `RetentionPolicy.dedup_retire.recovered_substrings` field. When the
  field is set and a permanode group has BOTH recovered and
  non-recovered claims, the reconciler picks a non-recovered keeper
  regardless of tier priority. When all claims are recovered, tier
  priority decides as usual. Default (empty list) preserves v0.1.0
  behaviour exactly.
- `StashEntry.manifest_permanode_id` — carries the original manifest
  permanode id when the audit FK was NULLed (because the permanode
  wasn't in the DB at apply time). The new `effective_permanode_id`
  property prefers the FK-safe value, falls back to the manifest id —
  `verify_stash` uses this to chase a canonical-elsewhere claim.
- `docs/MIGRATION_BACKLOG.md` — port status for every script in
  `~/sprawl-audit/scripts/`: ported / deferred / archived / dropped.

### Tests

- 112 passing (was 103 at v0.1.1): 5 new integration tests for
  `stash verify` (ok / no-canonical / dst-missing / src-still-present /
  unknown-run-id) + 4 new unit tests for the recovered-bias reconciler
  logic (bias-forces-non-recovered-keeper, all-recovered-uses-tier-priority,
  empty-substrings-preserves-v0.1.0-behavior, bias-against-NAS-tier).

## [0.1.1] — 2026-05-16

Fast-follower release: the v0.1.x backlog items defined in v0.1.0 are
delivered. No schema changes, no breaking CLI changes. 4 consecutive
green CI runs on `main` between v0.1.0 and v0.1.1.

### Added

**Subtree-disjoint parallel walker:**
- `steward scan --workers N` (N >= 2) partitions ``root`` by top-level
  subdir and dispatches each subtree to its own ``ProcessPoolExecutor``
  worker. Each worker opens its own DB connection (WAL + sqlite_vec) and
  walks its subtree. Loose files directly under root are picked up by
  the parent after workers complete.
- Audit chain stays linear: only the parent writes ``scan_start`` and
  ``scan_end`` rows. Workers write claims and permanodes (the latter
  via ``INSERT ... ON CONFLICT DO UPDATE`` so races on identical content
  are atomic). The scan_run row records the actual worker count.
- ``walker.scan_root`` refactored into ``_process_file`` (per-file core),
  ``_walk_serial`` (single-process walk), and ``_walk_parallel`` (parent
  + worker pool). The serial path is unchanged from a behaviour
  standpoint — only the structure moved.
- When ``root`` has no subdirs, parallel mode falls through to a serial
  walk on the parent connection (no worker spawn).

**Container walker:**
- `infra/scanner/container_walker.py` — opens .zip / .tar / .tar.gz /
  .tar.bz2 / .tar.xz archives and records per-member claims with
  `container_path` and `container_sha256` populated. Members reuse the
  permanode model (xxh3-128 content hash) and the existing skiplist
  (`__MACOSX/` and AppleDouble entries are pruned).
- `steward scan --include-containers` no longer prints a "v0.2 feature"
  notice — it now drives real container ingestion.
- Disk images (.dmg / .sparseimage / .iso) and .7z / .rar are recognised
  by `is_container_path` but explicitly skipped (`containers_skipped`
  counter ticks up); their handlers depend on `hdiutil` / `unar` and
  ship with the v0.2 adapter wave.
- `ScanStats` gains `containers_walked`, `containers_skipped`,
  `containers_errored`, `container_members_walked`,
  `container_members_errored`. Surfaced in CLI output and the `scan_end`
  audit payload.
- Bad / unreadable archives bump `containers_errored` and append to the
  swallowed-error log; the surrounding scan continues uninterrupted.

**`--resume` scan flag:**
- `steward scan --resume` reuses the prior finished scan_run's permanode_id
  for any path whose `(size_bytes, mtime_iso)` is unchanged. Files that
  changed or didn't exist before fall through to the normal hash path.
- `infra/scanner/walker.py::scan_root` gains `resume_from_run_id` param;
  `infra/scanner/orchestrate.py::find_latest_finished_run` resolves the most
  recent eligible prior run for `(root, machine_id)`.
- `scan_runs.resumed_from` (already in the v0.1 schema) is now populated.
- New `ScanStats.files_reused` counter; surfaced in CLI output when
  `--resume` is set.
- Crash protection: only FINISHED prior scans are eligible to resume from
  (`finished_at IS NOT NULL`) — an abandoned scan won't poison the cache.

**Mirror-path resolver for promote:**
- `PromotionPhase.mirror_from` — optional sentinel substring; when set,
  destination preserves everything in the (translated) source path after the
  LAST occurrence of the sentinel. Anchors mirror at a phase-specific level
  (e.g. `Photos/` so `/Volumes/Backup/Clones/Mac/Photos/2024/IMG_001.jpg` →
  `<destination_root>/2024/IMG_001.jpg`).
- `PromotionDefaults.mirror_strip_prefix` — policy-wide default. When a phase
  doesn't set `mirror_from`, strip this prefix from the translated source and
  mirror everything below. Bundled `promotion.yml` sets it to
  `/Volumes/Backup/` so subdir structure is preserved by default.
- `core/policy/reconciler.py::_resolve_destination` — resolution priority:
  phase `mirror_from` (sentinel) > policy `mirror_strip_prefix` > basename
  (back-compat). Prevents destination-path collisions when two source files
  share a basename under different ancestors.

### Tests

- 103 passing (was 77): 8 unit tests for the mirror-path resolver, 4
  integration tests for `--resume`, 9 integration tests for the
  container walker, and 5 integration tests for the parallel walker
  (serial-equivalence, workers-recorded, audit-chain-intact, loose-
  files-covered, no-subdirs-falls-back-to-serial).

### Updated

**Deferred → shipped:** mirror-path resolver, `--resume`, container
walker (zip / tar), and subtree-disjoint parallelism. All four were
listed in v0.1.0 as deferred to v0.1.x.

## [0.1.0] — 2026-05-16

First installable. Replaces the sprawl-audit prototype tooling with a real
package + schema + operator-in-the-loop plan/apply lifecycle.

### Added

**Scaffold (M1):**
- Repo bootstrap inheriting Cerid AI family conventions (Python 3.12,
  ruff 0.15.4, mypy strict, import-linter, pytest with `preservation` marker).
- `src/steward/` package: `core/` (pure domain), `infra/` (I/O boundary),
  `cli/` (typer entry points), `policies/` (bundled YAML defaults).
- Observability lifts from `cerid-ai-internal`: `sentry_init.py` (FastAPI/
  Starlette/Redis integrations stripped), `swallowed.py` (Redis counter
  path dropped), `request_id_filter.py` (adapted to run-id contextvar).
- CI: `lint`, `typecheck`, `test`, `preservation`, `security`, `lock-sync`,
  `lint-silent-catch`. Frontend / docker / preservation-stack jobs from
  Cerid stripped.
- Scripts: `regen-lock.sh` (pip-compile in `python:3.12-slim`),
  `validate-env.sh` (probes for inventory.db / NFS mounts / sqlite_vec
  / blake3 / xxhash), `lint-no-silent-catch.py` (verbatim from Cerid),
  `new-adr.sh`, `weekly-run.sh` (end-to-end acceptance gate).
- `.claude/`: `pythonlint.sh` (PostToolUse Write/Edit) and `safety-check.sh`
  (PreToolUse Bash; blocks `rm -rf` outside `_cooling-off-stash`).

**Schema + legacy import (M2):**
- `0001_initial.py` alembic migration with permanodes + claims + hashes
  + tiers + embeddings (+ `embeddings_vec` sqlite-vec virtual table,
  `float[384]`) + scan_runs + audit_log (with append-only BEFORE
  UPDATE/DELETE triggers) + legacy_import_log + meta.
- `core/audit.py`: `compute_row_hash` + `canonical_payload` + the
  `GENESIS_PREV_HASH` constant (ADR-0003).
- `core/ids.py`: deterministic `permanode_id = blake3(hash || ":" ||
  size)[:32]`.
- `core/tiers.py`: `classify_tier` ported verbatim from sprawl-audit;
  `TIER_PRIORITY`, `LIVE_TIERS`, `NAS_READONLY_TIERS` constants.
- `infra/db/connect.py`: `connect()` applies WAL + foreign_keys +
  cache_size + sqlite_vec.
- `infra/db/admin.py`: operator-facing facade (`migrate`, `verify_chain`,
  `integrity_check`, `resolve_machine_id`) so `cli/*` never imports
  `infra.db.connect` directly (import-linter contract).
- `infra/importer/legacy_unified.py`: read-only on source, batched
  upserts, noise-path filter, audit-log brackets, dry-run rollback.
- CLI: `steward db migrate | verify | integrity`,
  `steward import legacy --source <path> [--dry-run] [--limit N]`.

**Scanner (M3):**
- `core/hashing.py`: `HashLadder.fast()` (xxh3-128, 8 MiB chunks),
  `.archive()` (blake3), `.should_promote()` (size threshold +
  suspected-dup hook).
- `infra/scanner/skiplist.py`: `DEFAULT_SKIP_DIRS`,
  `SKIP_FILE_PREFIXES`, `SKIP_FILE_EXACT`.
- `infra/scanner/walker.py`: single-process walk with in-place dir
  filter, upserts permanodes, inserts claims, records hashes,
  audits with `scan_start` / `scan_end`.
- CLI: `steward scan --root <path> [--workers N] [--include-containers]`
  (`--workers` and `--include-containers` are reserved for v0.2 and
  emit a notice rather than error).

**Policy + operator surface (M4):**
- `core/policy/schema.py`: pydantic models for `RetentionPolicy`,
  `PromotionPolicy`, `ClassificationPolicy` (version: 1, kind discriminator,
  `extra=forbid`).
- `core/policy/loader.py`: yaml.safe_load + dispatch by kind +
  `PolicyError` on bad input.
- `core/policy/matchers.py`: `is_noise`, substring / prefix / exact
  helpers (no I/O).
- `policies/retention.yml`: bundled default with noise lists + dedup-
  retire matrix.
- `core/manifest_io.py`: read / write TSV-with-comment-header plan
  manifest (`# steward-manifest-v1`).
- `infra/stash.py`: `same_fs_rename_to_stash` (same-FS device check,
  no-overwrite guard, audit-row append; resolves manifest permanode_id
  against DB, NULLs the FK if absent and carries the original id in
  `payload_json`).
- `infra/db/apply.py`: `apply_manifest` reads the manifest, brackets
  work with `apply_start` / `apply_end` audit rows, dispatches by
  action.action, rolls back on dry_run / commits on execute.
- `infra/db/inspect.py`: resolve target → permanode + claims + audit.
- CLI:
  - `steward policy lint <yaml>` / `steward policy show <name>`
  - `steward inspect <hash | permanode_id | claim path>`
  - `steward apply --manifest <tsv> {--dry-run | --execute} [--max-files N]`
    (no flag → exit 2; mutually exclusive; ADR-0002 enforcement)

**Architecture decisions (M6):**
- ADR-0001: Permanode + claim model
- ADR-0002: Operator-in-the-loop on destructive operations
- ADR-0003: Append-only audit log with hash chain
- ADR-0004: YAML policy with Python evaluator
- ADR-0005: Hash ladder — xxh3-128 fast / blake3 archive
- ADR-0006: Single database file (`inventory.db`)
- ADR-0007: Cooling-off stash pattern
- ADR-0008: Machine-id claim attribution from day one
- ADR-0009: Pull-don't-push for inventory
- ADR-0010: Classification deferred from ingest

### Tests

- 51 passing: 32 unit (ids / audit / tiers / hashing) + 13 integration
  (legacy import / walker) + 6 preservation (apply lifecycle).
- Preservation suite covers: dry-run-zero-writes, apply-execute-rename,
  audit-chain-intact, idempotent re-apply, destructive-requires-flag,
  noise-paths-never-acted-on.

**Policy reconciler + plan CLI (M5.1-2):**
- `core/policy/reconciler.py` — `reconcile_dedup_retire(policy, claims)`
  groups current claims by permanode, keeps the highest-priority tier
  copy, emits `stash` / `nas_manifest` rows for the rest. Noise +
  root-prefix filters applied before grouping.
- `infra/db/plan.py` — `plan_dedup_retire` facade.
- CLI: `steward policy plan --policy <yaml> --out <tsv> [--root <prefix>]`.

**Stash lifecycle (M5.3):**
- `infra/db/stash_cmd.py` — `list_stashes` walks audit_log for in-flight
  `stash_committed` rows; `finalize_stash` unlinks destinations after
  cooling-off (default 7d, `--force` overrides); `restore_stash` renames
  destinations back to source paths.
- CLI: `steward stash list | finalize --run-id <id> | restore --run-id <id>`.

**Promote action (M5.4):**
- `infra/promote.py` — `promote_with_verify`: copy to `<dst>.inflight`,
  fsync, blake3 verify, rename to final. Idempotent on size+hash match;
  refuses to overwrite mismatched destinations.
- `policies/promotion.yml` — port of sprawl-audit `promote_execute.PHASES`.
- `infra/db/apply.py` dispatches `promote` rows alongside `stash`.

**Classify pass (M5.5):**
- `core/policy/classification.py` — `Classifier` (compiled cluster regexes;
  case-insensitive substring domain match; first-rule-wins).
- `policies/classification.yml` — port of `domain_hint()` + the
  Media-Recovered-From-Trash + Work-Cannon-AFB clusters.
- `infra/db/classify.py` — `classify_claims` updates `domain` +
  `classification`; default is NULL-only, `--reclassify-all` overwrites.
- CLI: `steward classify [--policy <name>] [--reclassify-all]`.

**PromotionPolicy reconciler + CLI dispatch (M5.6):**
- `core/policy/reconciler.py::reconcile_promote` — filters to
  permanodes that exist only on `policy.defaults.source_tier`, matches
  against `phases[*].match` (domain / path_substring), emits one
  `promote` row per matched permanode with path-translations applied.
- `infra/db/plan.py::plan` — dispatch by `policy.kind`:
  `RetentionPolicy` → `plan_dedup_retire`; `PromotionPolicy` →
  `plan_promote`.
- CLI: `steward policy plan` now accepts `--phase` + `--limit`
  (PromotionPolicy) alongside `--root` (RetentionPolicy). End-to-end
  smoke-tested: 2 Backup-only permanodes → 2 promote rows with correct
  destination paths from the bundled `promotion.yml`.

### Tests

- **77 passing**: 47 unit (ids / audit / tiers / hashing / classification
  / dedup-reconciler / promote-reconciler) + 24 integration (legacy
  import / walker / stash lifecycle / promote / classify) +
  6 preservation (apply lifecycle).
- Preservation suite covers: dry-run-zero-writes, apply-execute-rename,
  audit-chain-intact, idempotent re-apply, destructive-requires-flag,
  noise-paths-never-acted-on.

### Deferred from v0.1.0 to v0.1.x / v0.2

- **Disk-image and 7z/RAR container handlers** (require `hdiutil` / `unar`;
  land in v0.2 with the osxphotos / rclone adapters).
- **PST family / sbc_* / dropbox_fix_*** sprawl-audit scripts in
  `MIGRATION_BACKLOG.md`.

  *Mirror-path resolver, `--resume`, ZIP / tar container walker, and
  subtree-disjoint parallelism all shipped in `[Unreleased]` above.*

### v0.1.0 ship criteria (per ROADMAP.md)

- [x] All 10 ADRs committed
- [x] `make lint typecheck imports test preservation` green (77 tests)
- [x] `scripts/weekly-run.sh` runs end-to-end on a tmp DB (real
      `/Volumes/Backup` scan opt-in via `STEWARD_WEEKLY_SCAN_ROOT`)
- [x] Reconciler + promote action + classify pass ported (both
      dedup-retire AND promote are YAML-driven)
- [x] CI green on `main` for 3 consecutive runs (d80c4f7, a72b284,
      e380cd9 — confirmed green 2026-05-16)
- [x] `git tag v0.1.0` cut 2026-05-16
