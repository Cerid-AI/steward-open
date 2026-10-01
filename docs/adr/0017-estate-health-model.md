# ADR 0017: Estate Health Model

**Status:** Accepted  
**Date:** 2026-08-05  
**Accepted:** 2026-08-07 (shipped through v0.3.23–0.3.24)  
**Related:** ADR-0002 (operator-in-the-loop), ADR-0003 (append-only audit),
ADR-0006 (single inventory.db), ADR-0009/0013 (pull-don't-push / attached
RO imports), ADR-0015 (FP path policy), ADR-0016 (MCP modes), OPEN_DEVELOPMENT
P1 multi-GB operability

## Context

Operator surfaces today are **fragmented**:

| Surface | What it covers | Gap |
|---|---|---|
| `steward status` | DB counts, single latest scan, stash CTE, adapter ends, full audit walk | One scan root only; no per-tier freshness; no mounts; no FP; no schedule; `--include-imports` not on CLI |
| `steward fp status` | Dropbox layout + `FPHealthVerdict` | Dropbox-first; not rolled into a single estate pane |
| `steward stats` | Tier/domain/ext rollups | Capacity/tracking, not health thresholds |
| `steward machines` | machine_id fan-out | No freshness of attached imports |
| `steward schedule status` | One launchd label dump | Not composed into status |
| Dashboard `/healthz` | Process liveness only | Not inventory/estate posture |
| MCP `status_snapshot` / `fp_status` | Parallel fragments | Agents re-compose by hand |

Live inventory is multi-GB (~9 GiB). Full audit-chain walks and stash CTEs
are correctly gated by `status --quick` / rollup cache, but there is still
**no first-class answer** to:

> Is this estate healthy enough to plan, retire, or sleep?

Gaps called out in open development:

- No unified estate-health surface (tiers, volumes, machines, scan
  freshness, FP verdict, adapter health).
- No host free-space / mount capacity probe beyond advisory text.
- No per-root / per-tier scan staleness (only latest finished `scan_run`).
- Audit-log shrink / chain-archive remains open (separate ADR; see
  § Related work).
- No always-on estate daemon without a new ADR (launchd templates only).

v0.4 foundation needs a **composable health model**, a CLI that can
gate automation (`--fail-on`), snapshot series for sparklines, and
thin wires into dashboard + MCP — without inventing a daemon or
weakening operator-in-the-loop / append-only invariants.

## Decision

### 1. `EstateHealthReport` is the composite contract

One frozen report object is the source of truth for CLI, MCP, and
dashboard. It **composes** existing collectors; it does not replace
`StatusReport` or `FPStatusReport`.

```
EstateHealthReport
├── generated_at          ISO-8601 UTC
├── machine_id            local identity
├── overall               HealthLevel: ok | warn | fail | unknown
├── inventory             InventoryIntegrity
├── scan_freshness        list[RootScanFreshness]   # per root (+ tier label)
├── stash                 StashHealth
├── adapters              AdapterFreshness          # replicate / archive
├── schedule              ScheduleHealth | None     # macOS / module present
├── fp                    FPSection                 # wraps FPHealthVerdict
├── attached_imports      list[AttachedImportHealth]
├── mounts                list[MountProbe]          # present / capacity / latency
├── rollups               RollupInfo | None         # from status path
├── checks                list[HealthCheckResult]   # named, fail-on targets
└── notes                 tuple[str, ...]
```

**Section meanings**

| Section | Ready when | Fail / warn signals |
|---|---|---|
| **Inventory integrity** | DB opens; counts available (live or rollup cache) | `broken_audit` (chain not ok); empty inventory on a host that expects scans |
| **Scan freshness** | Latest *finished* `scan_run` per `root_path` (and optional tier classification of root) | `stale_scan` when age > threshold; unfinished long-running scan noted as warn |
| **Stash backlog** | In-flight stash summary (or meta cache) | `stash_overdue` when oldest entry older than cooling-off window + grace |
| **Adapter freshness** | Latest `replicate_end` / `archive_end` audit rows | Soft `warn` when older than adapter threshold (not hard-fail by default) |
| **Schedule status** | Bundled launchd templates listed + optional `launchctl print` when cheap | Missing/not-loaded templates → `warn` when schedule module present; `unknown` on Linux / open-core strip |
| **FP verdict** | `collect_fp_status` / `evaluate_fp_health` | `fp_not_ready` when cloud-retire intent is assumed and `cloud_retire_ready` is false |
| **Attached-import freshness** | `attached_inventories` rows | Stale `imported_at` / never `chain_verified_at` → `warn`; missing payload path → `fail` |
| **Mount / tier live probes** | Best-effort `stat` + free/total + sample latency on known volume roots | Missing critical mount → `warn`/`fail` for FP tiers; capacity low → `warn` |

`HealthCheckResult` is the stable gate unit:

```text
name: stale_scan | broken_audit | stash_overdue | fp_not_ready | rollup_stale | ...
level: ok | warn | fail | skipped | unknown
message: str
details: dict  # structured, JSON-stable
```

Named checks map 1:1 to `--fail-on` tokens (see §3).

### 2. Module placement (core vs infra)

| Symbol / concern | Module | Rationale |
|---|---|---|
| `HealthLevel`, `HealthCheckName`, pure threshold evaluation, report dataclasses that are I/O-free | `steward.core.health` (new) | Portable open-core types; import-linter safe; unit-testable without SQLite |
| Default thresholds constants | `steward.core.health.thresholds` | Policy numbers, not host I/O |
| `collect_estate_health`, snapshot IO, mount probes, composition of status/fp/schedule | `steward.infra.health` (new package) | DB + FS + optional launchctl |
| CLI | `steward.cli.health_cmd` | Typer group `steward health` |
| MCP handlers | `steward.infra.mcp.handlers` | `estate_health` / `estate_health_check` |
| Dashboard | `api.build_health_payload`, `render` posture banner, `GET /api/health` | Reuse collector |

**Hard rules**

- `steward.core` must not import `infra` or `cli`.
- `infra.health` may import `infra.status`, `infra.fp_status`,
  `infra.fp_preflight`, `infra.db.*`, and optionally
  `infra.schedule` via the same soft import pattern dashboard already uses.
- Do **not** move `StatusReport` or `FPHealthVerdict` into core in this
  slice; core holds only the **estate** types + pure
  `evaluate_fail_on(report, fail_on, thresholds) -> list[HealthCheckResult]`.

### 3. CLI: `steward health show` / `steward health check`

```text
steward health show
  [--json]
  [--quick | --full]
  [--include-imports]
  [--probes / --no-probes]     # live mount free/total + latency (default: on for show)
  [--write-snapshot / --no-write-snapshot]
  [--db PATH]

steward health check
  [--json]
  [--quick | --full]           # default: --quick
  [--include-imports]
  [--probes / --no-probes]     # default: off for check (cheap automation)
  [--fail-on NAME[,NAME...]]   # repeatable or comma-separated
  [--scan-max-age-hours N]     # default 168 (7d)
  [--stash-grace-hours N]      # default 24 beyond cooling_off_days
  [--adapter-max-age-hours N]  # default 168; soft unless listed in fail-on
  [--rollup-max-age-hours N]   # default 24
  [--attached-max-age-days N]  # default 30
  [--write-snapshot]
  [--db PATH]
```

**`--fail-on` tokens (v1)**

| Token | Fails when |
|---|---|
| `stale_scan` | Any tracked root's latest finished scan older than `--scan-max-age-hours`, or no finished scan exists for a configured/known root |
| `broken_audit` | Audit chain not ok (requires `--full` or non-skipped chain) |
| `stash_overdue` | Oldest in-flight stash older than cooling-off + grace |
| `fp_not_ready` | FP section present and `cloud_retire_ready` is false (or hard problems non-empty) |
| `rollup_stale` | Inventory counts not from a fresh rollup cache and recount was skipped / cache missing beyond max age |
| `dual_presence_poor` | Dual-presence sample ratio below threshold (ADR-0020; **opt-in**) |
| `fleet_stale_scan` / `fleet_chain_stale` / `envelope_sla` / `attached_missing` | Fleet / attached-import SLAs (ADR-0021; **opt-in**) |

Unknown tokens → CLI exit 2 with error message.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | No selected `--fail-on` check at `fail` (or no fail-on and overall ≠ fail) |
| 1 | At least one selected check failed (or overall `fail` when fail-on omitted defaults to all hard checks) |
| 2 | Usage / missing DB / collector hard error |

`show` is human-oriented (Rich sections + overall banner). `check` is
automation-oriented (exit code + compact JSON). Both share one collector.

**Default fail-on for `check` without flags:**  
`stale_scan,broken_audit,stash_overdue,rollup_stale`  

**Opt-in only** (must pass explicitly via `--fail-on`):

- `fp_not_ready` — non-Dropbox hosts must not false-red; apply keeps `--require-fp-healthy`
- `dual_presence_poor` — ADR-0020 cloud-truth sample (bulk cloud retire readiness)
- `fleet_stale_scan`, `fleet_chain_stale`, `envelope_sla`, `attached_missing` — ADR-0021 fleet

### 4. Snapshot persistence: data-dir JSON series (not inventory table)

**Choice:** append-only **JSONL series under the data dir**, plus a
small **sidecar pointer** (and optional `meta` mirror).

```text
<STEWARD_DATA_DIR>/
  inventory.db                 # unchanged authority for inventory/audit
  health/
    snapshots.jsonl            # one EstateHealthReport dict per line (compact)
    LATEST                     # path or ISO id of last written snapshot
    # optional rotation: snapshots-YYYYMM.jsonl
```

**Rationale**

1. Live `inventory.db` is already multi-GB; sparkline history must not
   bloat the same file (ADR-0006 still holds for *inventory* JOINs).
2. Health is **telemetry**, not forensic claim truth — does not need
   append-only triggers or claim FKs.
3. Writers must not fight long scans for the inventory write lock
   (field notes: audit writes blocked while scan holds the DB).
4. Matches existing data-dir sidecar patterns (MCP plan tokens under
   `runs/`, imports under `imports/`).
5. Sparklines need only recent compact points (counts, ages, levels,
   free bytes) — strip bulky FP dual-sample lists on write via
   `estate_health_to_snapshot_dict(report, *, compact=True)`.

**Retention (defaults)**

- Keep last **500** lines or **90 days** (whichever first), pruned on
  write (rewrite tail file or rotate monthly shards).
- Prune is best-effort; failures call `log_swallowed_error` and leave
  series intact.

**Who writes snapshots**

| Trigger | Default write? |
|---|---|
| `steward health check` | Yes when `--write-snapshot` **or** env `STEWARD_HEALTH_SNAPSHOT=1` |
| `steward health show` | Only with `--write-snapshot` |
| `steward status --refresh` | Writes rollups (existing); **also** appends one health snapshot (compact, quick path) so rollup refresh stays the operator cadence |
| Schedule hooks | Weekly-verify / inventory-export templates may invoke `health check --write-snapshot` (plist change is follow-on; not a daemon) |
| Dashboard action `refresh_health` | Writes snapshot when action succeeds |

Optional `meta` key `health_snapshot_latest` may store the ISO timestamp
for operators grepping the DB — **not** the full series.

### 5. Cheap-default rules (multi-GB)

| Path | Behaviour |
|---|---|
| Default `health check` | `--quick`: skip full audit walk; stash summary uses **meta stash cache** if present else mark `stash` checks `skipped`/`unknown` (cannot `fail` `stash_overdue` unless `--full` or cache) |
| Default `health show` | `--quick` equivalent for audit; still shows available sections; mount probes on by default but capped (≤ N roots, timeout per root) |
| `--full` | Full `verify_chain`, full stash CTE, optional deeper FP samples |
| Scan freshness | Single SQL: latest finished `scan_runs` grouped by `root_path` (indexed path; no claim table scan) |
| Inventory counts | Prefer rollup meta cache (`status_inventory_rollups`); never `COUNT(*)` multi-million claim table on quick path when cache is fresh |
| Mount probes | `os.stat` / `shutil.disk_usage` / timed existence only — no recursive walks; no `fileproviderctl dump` |
| FP | Reuse existing lightweight `collect_fp_status` |
| Schedule | List templates + file existence; skip `launchctl print` on quick path (print is optional `--full`) |
| Attached imports | Read `attached_inventories` only (no opening remote payload chains on quick path) |
| Snapshot compact | Drop dual_samples, large payloads; keep levels, ages, free bytes, check names |

Target: **`health check --quick` completes in seconds** on multi-GB DB
(same order as `status --quick`), independent of claim row count.

### 6. Tier / mount live probes

New infra helper (shared by health + future stats-by-volume):

```text
MountProbe
  root: str                 # volume or tier path prefix
  tier: str | None
  present: bool
  free_bytes: int | None
  total_bytes: int | None
  sample_latency_ms: float | None
  error: str | None
```

- Reuse `PathProbe`-style error handling from `fp_status`.
- Roots sourced from: tiers table `path_prefixes`, known Dropbox
  store/mount, and optional machine.toml when present.
- Free-space **warn** threshold default: &lt; 5% free or &lt; 10 GiB free
  (configurable later; not a `--fail-on` token in v1 unless we add
  `mount_low` — deferred).

  **Amended 2026-08-16 — free space gains a `fail` band.** As written above,
  `free_space_level` could return no worse than `warn`, so a volume in the act
  of taking the machine down graded identically to one merely getting full. On
  2026-08-13 the Mac Pro boot volume reached 100%: a CI runner crashed writing
  its own log, all three self-hosted runners went offline, and Docker Desktop
  reset its VM. Three days after that cleanup the same volume was back under
  21 GiB free, burning ~20 GB/day. Revised defaults:

  | Band | Absolute | Ratio |
  |---|---|---|
  | warn | < 25 GiB free | < 8% free |
  | fail | < 10 GiB free | < 3% free |

  The two axes do different jobs and must not be conflated: the absolute floor
  means "too little space for anything to work, whatever the disk size" and so
  stays small — raising it to volume-sized numbers makes every small volume
  permanently red. The ratio floor does the proportional work and is what
  catches a large disk filling. They combine with OR, and each `fail` floor
  must stay strictly tighter than its `warn` counterpart (asserted by
  `test_free_space_fail_floors_are_tighter_than_warn`).

  **`mount_low` is now a real `--fail-on` token, and is default-on.** The
  deferral above was the load-bearing half of the gap: even with a fail band
  and `--probes`, capacity could not fail the gate, so `steward health check`
  exited 0 with the boot volume grading `fail`. `mount_low` and its sibling
  `mount_missing` are in `KNOWN_FAIL_ON_TOKENS`, and `mount_low` is in
  `DEFAULT_CHECK_FAIL_ON`.

  Default-on is safe here in a way the FP and fleet tokens are not: every host
  has disks, so there is no host shape it can false-red on, and because
  `health check` defaults to `--no-probes` the check reports **`skipped`**
  until capacity is actually measured. "Not measured" must never be
  indistinguishable from "every volume is fine" — that property is what makes
  the default admissible, and it is asserted by
  `test_no_probes_is_skipped_not_ok`.

  `mount_missing` was also corrected in the same change. It collected every
  probe at level `fail`, which was correct only while `fail` could mean
  nothing but "critical mount absent". Once free space gained a fail band, a
  merely FULL disk landed there and was reported as "critical mount(s)
  missing" — a present, mounted, working volume described as gone. Presence,
  not level, is now the discriminator.

  **Second amendment, same day — two sibling checks, born of the same
  incident's second act.** Even with capacity gating, the boot volume's
  filling was invisible for 46 days because the consumer wrote into a
  DataVault: kernel-protected storage that no `du` may enter and whose
  contents are therefore absent from every parent total. The writer was a
  File Provider domain in a sync-error retry loop (`fileproviderctl` showed
  `error generation: 628`), staging transfer data through nsurlsessiond on
  every failed cycle.

  - **`fp_sync_stuck`** (collector: `--fp-domains`) grades each File
    Provider domain by fileproviderd's own failed-cycle counter: warn ≥ 10,
    fail ≥ 100. Healthy domains sit at 0–2.
  - **`unaccounted_space`** (collector: `--unaccounted <volume-root>`)
    measures the statfs-used vs `du -x`-reachable gap. Absolute floors
    (warn 64 GiB / fail 160 GiB): the legitimate gap — DataVaults plus
    filesystem metadata — is a roughly fixed cost, not proportional to disk
    size. Deliberately minutes-slow and therefore weekly-scheduled, not part
    of the interactive gate.

  Both are in `KNOWN_FAIL_ON_TOKENS` **and** `DEFAULT_CHECK_FAIL_ON`, safe
  by the same rule as `mount_low`: when the collector did not run the check
  reports `skipped`, never `ok` — and an empty parse of a dump that DID run
  reports `unknown`, because a parser that silently stopped matching a
  drifted format must not read as a healthy estate. Full narrative:
  `docs/field-notes-2026-08-16-boot-volume-hidden-consumption.md`.

  Two further corrections in the same change, both cases of a probe reporting
  health about a question it never asked:

  - **An impossible reading is `unknown`, never `ok`.** `shutil.disk_usage`
    returns more free bytes than total on the NFS tiers here
    (`/Volumes/Backup`, `/Volumes/Level_3a` compute to -2388% used). The huge
    bogus `free` cleared both floors, so two network tiers reported `ok` while
    nothing about them was being measured.
  - **Symlinked and read-only roots are `skipped`, not graded.**
    `/Volumes/Level 00` is a symlink to `/`, which scored the boot volume a
    second time under another name; a mounted read-only DMG sits permanently
    at 0 bytes free and warned forever. Neither is actionable from this host,
    and both train operators to ignore the report.
- Sample latency: wall time of one `Path.exists()` / `stat`; surface as
  warn if &gt; 2000 ms (FP congestion signal; aligns with field notes).

### 7. Integration points

**MCP (read mode)**

| Tool | Behaviour |
|---|---|
| `estate_health` | `collect_estate_health(...); estate_health_to_dict` — default quick |
| `estate_health_check` | Same + evaluate `--fail-on` equivalent kwargs; returns `{ok, failed: [...], report}` |

No new write-mode tools. Snapshot write from MCP is **not** exposed in
v1 (avoid agent spam); CLI/schedule/dashboard only.

**Dashboard**

- `GET /api/health` → `build_health_payload` (quick default; `?full=1`).
- `GET /api/health/series?limit=48` → last N compact snapshot points for
  sparklines.
- Posture **banner** above KPI strip: overall level + first fail/warn
  messages (extends existing `audit-banner` pattern).
- Action catalog: `refresh_health` (collect + optional snapshot write;
  non-destructive).

**Status relationship**

- `steward status` remains the lighter inventory pane.
- `steward health` is the estate gate + composite.
- Shared: rollup refresh, audit helper, stash summary.
- Small follow-on (same release if cheap): expose
  `status --include-imports` already supported by `collect_status`.

**Schedule / agents**

- `tier-auditor` agent docs: prefer `steward health check --quick`
  then escalate with `--full` / `fp status`.
- No always-on daemon; launchd may call check periodically.

### 8. Explicit non-goals (this ADR)

- Always-on continuous monitor process (needs separate daemon ADR).
- Bulk dual-presence cloud-retire tracking as a first-class table
  (still plan/filter concern; health only surfaces FP readiness).
- Shrinking `audit_log` (ADR-0018 outline only).
- Per-tier health *panes* as full dashboard rewrite — banner + API is
  enough for foundation; richer panes may follow without new ADR if
  they only consume `EstateHealthReport`.
- Changing apply / stash / retire_direct semantics.

## Consequences

**Positive**

- One contract for operators, agents, and UI.
- Automation can gate on explicit thresholds without scraping Rich
  tables.
- Snapshot series enable sparklines without growing inventory.db.
- Cheap defaults protect multi-GB dogfood hosts.
- Core purity preserved; open-core can ship types + pure evaluation.

**Negative / residual**

- Two “dashboard” CLIs (`status` vs `health`) — documented split:
  status = inventory pane; health = estate gate.
- Quick path cannot hard-fail `broken_audit` or `stash_overdue` without
  cache/`--full` — by design; automation that cares must opt into
  cost.
- Mount free-space is host-local and can disagree with cloud quota
  (already true for store-path reclaim).
- Schedule section is macOS-first; Linux reports `unknown` without
  failing default checks.

## Alternatives rejected

- **Only extend `StatusReport`** — becomes a god object; couples audit
  stash CTE to FP and mounts; harder open-core split.
- **`health_snapshots` table inside inventory.db** — easy SQL, but
  worsens multi-GB size and write-lock contention; telemetry is not
  claim-adjacent.
- **Always-on daemon polling health** — violates no-daemon invariant;
  launchd + CLI is enough for v0.4 foundation.
- **Fail closed on `fp_not_ready` by default** — punishes non-FP estates;
  apply already has `--require-fp-healthy`.
- **Full claim scans for “integrity”** — O(claims) is not a health
  default; use rollups + chain verify.

## Implementation notes

### Public API (target)

```python
# steward.core.health
HealthLevel = Literal["ok", "warn", "fail", "unknown", "skipped"]
# dataclasses: EstateHealthReport, HealthCheckResult, ...
def evaluate_fail_on(
    report: EstateHealthReport,
    fail_on: frozenset[str],
    *,
    thresholds: HealthThresholds | None = None,
) -> list[HealthCheckResult]: ...

# steward.infra.health
def collect_estate_health(
    *,
    db_path: Path,
    quick: bool = True,
    include_imports: bool = False,
    probes: bool = True,
    refresh_rollups: bool = False,
    thresholds: HealthThresholds | None = None,
) -> EstateHealthReport: ...

def write_health_snapshot(report: EstateHealthReport, *, data_dir: Path) -> Path: ...
def read_health_series(*, data_dir: Path, limit: int = 48) -> list[dict[str, Any]]: ...
def estate_health_to_dict(report: EstateHealthReport) -> dict[str, Any]: ...
```

### Tests

- **Unit:** pure `evaluate_fail_on` matrix; threshold edge ages; compact
  snapshot dict shape.
- **Integration:** temp DB with scan_runs / stash audit rows / attached
  row → `collect_estate_health` + CLI exit codes; `--quick` does not
  open full chain; snapshot JSONL append + LATEST; dashboard
  `/api/health` 200.
- **Preservation:** none required for pure health (no FS mutation).
- Keep multi-GB dogfood path on `--quick` (no accidental COUNT on
  claims).

### Files expected to touch (implementation slice)

- `docs/adr/0017-estate-health-model.md` (this file)
- `src/steward/core/health/` (types + evaluate)
- `src/steward/infra/health/` (collect, probes, snapshots)
- `src/steward/cli/health_cmd.py` + register in `cli/main.py`
- `src/steward/infra/mcp/handlers.py`, `server.py`
- `src/steward/infra/dashboard/{api,server,render}.py`
- `src/steward/infra/status.py` (optional stash rollup meta; status
  `--refresh` snapshot hook)
- Tests under `tests/unit/core/`, `tests/unit/infra/`,
  `tests/integration/`
- Docs: `OPEN_DEVELOPMENT.md`, `QUICKSTART.md`, `CHANGELOG.md` when
  shipping

## Related work — ADR-0018 outline (audit chain-archive / shrink)

**Not implemented in the estate-health slice.** Open development still
lists audit shrink as P1 needing its own ADR. Design must exist so
health does not pretend the multi-GB audit table will shrink itself.

### Problem

- ADR-0003: `audit_log` is append-only with SQLite triggers forbidding
  `UPDATE`/`DELETE`.
- `db audit-export` (v0.3.17) is cold **export only** — does not shrink.
- Multi-year / multi-GB inventories make full-chain verify expensive
  (hence `status --quick` / health quick path).

### Proposed direction (for ADR-0018)

1. **Never DELETE in place** against a live chain (triggers + forensics).
2. **Segment archive:** export a closed id range `[1, N]` to an external
   sealed artifact (JSONL or SQLite) including first/last `row_hash` and
   a blake3 of the artifact.
3. **Live DB rebuild (copy-forward):** create a new `inventory.db` that
   retains operational tables + audit rows `(N+1)…` re-chained from a
   new genesis **or** a single `audit_chain_seal` synthetic genesis that
   embeds `prior_archive_blake3` + `prior_last_row_hash` so
   `db verify` remains defined.
4. **Operator-in-the-loop:** `steward db audit-archive --dry-run|--execute`
   only; dual-file swap with backup; no ambient daemon.
5. **Health integration later:** `broken_audit` / archive presence can
   surface seal metadata; out of scope for ADR-0017 implementation.

### Alternatives (0018)

- Drop triggers and DELETE — **rejected** (destroys ADR-0003).
- Separate audit.db always — large migration; revisit only if rebuild
  strategy fails.
- External WORM only — export already covers cold copy; shrink needs
  live size relief.

Full ADR-0018 should be written before any execute path lands.

## Status progression

- **Proposed** — design for v0.4 Estate Health foundation.
- **Accepted** — when implementation PR lands with tests green.
- Supersedes nothing; extends operator surfaces only.

## Amendment

**Amended 2026-10-01 — estate-aware health (ADR-0023).** With an estate file,
mount probes cover this host's declared mounts (skipping `ignore`, `forbid`
and `probe_skip`) and grade a missing one by its `criticality`, replacing
the hardcoded critical-FP-tier rule; cloud File Provider sections are
`skipped` on a host without a `cloud-fp` volume. Two default `--fail-on`
checks appear only with an estate: `foreign_attach` and `estate_binding`.
On the primary, the opt-in `remote_capacity` grades each client's pulled
`health/latest.json`; every snapshot now also writes that file. Without an
estate file the report is as described above.
