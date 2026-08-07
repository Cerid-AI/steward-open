# ADR 0021: Multi-machine fleet health matrix

**Status:** Accepted  
**Date:** 2026-08-05  
**Related:** ADR-0002 (operator-in-the-loop), ADR-0003 (append-only audit),
ADR-0006 (single inventory.db), ADR-0008 (machine_id from day one),
ADR-0009 / ADR-0013 (pull-don't-push / wire format + ATTACH RO),
ADR-0017 (estate health model), ADR-0018 (audit chain-archive),
ADR-0019 (schedule reliability), OPEN_CORE / steward-fs extract

## Context

ADR-0013 shipped a complete cross-machine **wire path**: export
envelope → import → `attached_inventories` → `ATTACH DATABASE ?mode=ro`
→ read-side fan-out (`machines`, `stats`, `inspect`, dashboard, MCP).
`apply` is structurally barred from imported claims.

What still does not exist is a **fleet posture** answer:

> Across every machine I know about (local + attached imports), is
> each host's inventory fresh enough, is its audit chain still
> trusted, and is the envelope sync SLA holding?

Today the pieces are fragmented:

| Surface | What it covers | Gap |
|---|---|---|
| `steward machines list/show --include-imports` | Counts + last_seen + recent activity | No last **finished** scan, no chain age, no envelope age, no SLA levels |
| `steward db imports list` | `imported_at`, `chain_verified_at`, payload exists | Not joined to scan freshness or claim counts; not a matrix |
| `steward status` | Local pane only | CLI lacks `--include-imports` despite `collect_status` support; no per-machine rows |
| ADR-0017 `attached_imports` | Thin list on `EstateHealthReport` | Designed as one estate section, not a fleet matrix + SLA |
| `db_export_created` audit + `weekly-inventory-export` | Local export signal | No consumer that treats export age as a **sync SLA** |
| Dashboard / MCP machines | Same as list/show | No health columns |

Live multi-machine dogfood (and any future multi-host estate) needs:

1. **A)** A machines **health matrix** with `--include-imports`: last
   finished scan, claim counts, audit-chain age / verify signal,
   envelope age.
2. **B)** Envelope **sync SLA** signals composed into estate health
   (ADR-0017), so automation can `--fail-on` stale fleet sync without
   scraping Rich tables.
3. **C)** Open-core portability: pure types + pure SLA evaluation in
   `steward.core`; no host secrets; docs noting PyPI `steward-fs`
   readiness (matrix works on any host that can attach RO payloads).

### Constraints (non-negotiable)

1. No FS mutation of inventory tiers without apply
   `--dry-run|--execute` (ADR-0002). Fleet matrix is **read-only**
   (optional snapshot write under data dir only, same family as
   ADR-0017 health JSONL).
2. Append-only blake3 `audit_log`; never UPDATE/DELETE audit rows
   (ADR-0003). Chain verify on attached payloads may update
   `attached_inventories.chain_verified_at` only via the existing
   `db verify --imports` path — matrix **reads** that column; it
   does not re-verify on the cheap path.
3. `steward.core` must not import `infra` or `cli` (import-linter).
4. No always-on daemon without a new ADR; CLI / MCP / dashboard
   loopback + launchd templates only.
5. Pull-don't-push (ADR-0009/0013): matrix never selects attached
   claims for apply; ATTACH stays `?mode=ro`.
6. macOS-first dogfood; portable core for open-core / `steward-fs`.
7. Every `except Exception` → `log_swallowed_error`; mypy strict.
8. Commit policy: human-authored only; no AI attribution in commits
   or committed docs.

### Relationship to ADR-0017 / 0019

| ADR | Question | Fleet role |
|---|---|---|
| **0017** Estate health | Is **this** estate safe to plan / retire / sleep? | Composes a **fleet** section fed by this ADR's matrix + SLA |
| **0019** Schedule reliability | Did launchd / cadence jobs fire? | Export schedule overdue is a **cause** of envelope SLA fail; not the matrix itself |
| **0021** (this) | Per-machine freshness + envelope SLA across the fleet | Source of truth for multi-host rows |

Do **not** fold the matrix into `StatusReport` or turn
`EstateHealthReport` into a god object. Estate health remains the
local composite gate; fleet matrix is the multi-machine table that
estate health **summarizes**.

## Decision

### 1. `FleetHealthMatrix` is the multi-machine contract

One frozen report object powers CLI, MCP, and dashboard.

```text
FleetHealthMatrix
├── generated_at              ISO-8601 UTC
├── local_machine_id          meta.machine_id of the querying host
├── overall                   HealthLevel: ok | warn | fail | unknown
├── thresholds                FleetThresholds (effective values used)
├── rows                      list[MachineHealthRow]   # local first, then attached
├── envelope_sla              EnvelopeSlaSummary       # estate-level rollup
├── checks                    list[HealthCheckResult]  # named fail-on targets
└── notes                     tuple[str, ...]
```

#### `MachineHealthRow` (one row per machine_id)

```text
machine_id: str
hostname: str | None          # attached: exporter_hostname; local: socket or meta
source: "local" | "attached"
is_current: bool              # True only for meta.machine_id

# Claims (cheap aggregates; local from main, attached from RO schema)
claim_count: int
current_claim_count: int

# Scan freshness (latest FINISHED scan_run for that machine's schema)
last_scan_finished_at: str | None
last_scan_root: str | None
last_scan_errors: int | None
scan_age_hours: float | None
scan_level: HealthLevel       # vs scan_max_age_hours

# Audit chain signal
# local: optional quick skip → unknown; full path uses verify_chain
# attached: chain_verified_at age + payload_exists (no full walk on quick)
chain_verified_at: str | None
chain_age_hours: float | None
chain_level: HealthLevel      # missing payload → fail; never verified → warn; stale verify → warn
payload_exists: bool | None   # None for local (always "main")

# Envelope / import age
# local: last db_export_created audit timestamp (+ optional exports/ mtime)
# attached: imported_at (envelope ingest time on this host)
envelope_at: str | None
envelope_age_hours: float | None
envelope_level: HealthLevel   # vs envelope_max_age_hours / attached_max_age_days

# Optional bookkeeping
audit_entry_count: int
exporter_version: str | None  # attached only
payload_blake3: str | None    # attached only
level: HealthLevel            # row rollup: worst of scan/chain/envelope
```

#### `EnvelopeSlaSummary`

```text
local_export_at: str | None
local_export_age_hours: float | None
local_export_level: HealthLevel
attached_count: int
attached_stale_count: int     # envelope_level in {warn, fail}
attached_missing_payload: int
attached_never_verified: int
level: HealthLevel            # worst across local + attached envelope signals
```

Named checks (stable `--fail-on` tokens, fleet slice):

| Token | Fails when |
|---|---|
| `fleet_stale_scan` | Any matrix row `scan_level == fail` |
| `fleet_chain_stale` | Any row `chain_level == fail` (missing payload, or full verify broken when run) |
| `envelope_sla` | `envelope_sla.level == fail` (local export too old **or** any attached import too old / missing payload) |
| `attached_missing` | Any attached row with `payload_exists is False` |

Reuse ADR-0017 `HealthLevel` / `HealthCheckResult` shapes from
`steward.core.health` when that package exists; if fleet lands first,
define minimal aliases in `steward.core.fleet` and migrate to shared
types without changing JSON field names.

### 2. Module placement (core vs infra)

| Symbol / concern | Module | Rationale |
|---|---|---|
| `HealthLevel` (shared), pure SLA age math, threshold defaults, fail-on evaluation | `steward.core.health` (preferred) or `steward.core.fleet` if 0017 types not yet present | Open-core portable; no SQLite |
| `FleetThresholds` constants | same core package | Policy numbers, not host I/O |
| `collect_fleet_health`, SQL fan-out, export-audit lookup, payload exists | `steward.infra.fleet` (new) | Composes machines + imports_admin + audit |
| Optional composition into estate report | `steward.infra.health` (0017) | `fleet: FleetSection` summarizing matrix overall + envelope_sla |
| CLI | `steward.cli.machines_cmd` subcommand `health` | Keep under existing `machines` group; no new top-level verb required |
| MCP | `steward.infra.mcp.handlers` | `fleet_health` / `fleet_health_check` |
| Dashboard | `api.build_fleet_payload`, `GET /api/fleet`, matrix table pane | Read-only |

**Hard rules**

- `steward.core` must not import `infra` / `cli`.
- `infra.fleet` may import `infra.machines`, `infra.sync.attach`,
  `infra.sync.imports_admin`, `infra.db.*`, and may soft-import
  `infra.health` only for composition — never the reverse cycle
  (prefer fleet → pure report; health imports fleet collector).
- No host secrets, host paths in **core** types, or Cerid-private
  field notes in public matrix payloads. Hostname from exporter
  metadata is fine; absolute private paths may appear in
  `file_path` for operators but must be strip-able for open-core
  snapshot compact mode.

### 3. CLI: `steward machines health`

```text
steward machines health
  [--json]
  [--include-imports / --local-only]   # default: --include-imports for this command
  [--quick | --full]                   # default: --quick
  [--fail-on NAME[,NAME...]]           # optional gate (like health check)
  [--scan-max-age-hours N]             # default 168 (7d)
  [--envelope-max-age-hours N]         # default 192 (8d; weekly export + 1d grace)
  [--attached-max-age-days N]          # default 30 (import freshness)
  [--chain-verify-max-age-days N]      # default 30 (attached chain_verified_at)
  [--db PATH]
```

**Defaults rationale**

- `machines health` defaults to **include imports** — the whole
  point of the matrix. Contrast `machines list` which stays
  local-default for backward compatibility.
- Weekly export plist is Monday 05:30; default envelope SLA **8 days**
  avoids false-red on a single missed hour after the weekly window.
- Attached import max age **30 days** matches ADR-0017's
  `--attached-max-age-days` sketch so estate health and fleet share
  one operator mental model.

**Exit codes** (when `--fail-on` present, or with default hard checks
if `--fail-on` omitted and `--check` style invoked):

| Code | Meaning |
|---|---|
| 0 | No selected check at `fail` |
| 1 | At least one selected check failed |
| 2 | Usage / missing DB / collector hard error |

For interactive matrix without gate, exit 0 always (like `machines
list`); add explicit:

```text
steward machines health --check [--fail-on ...]
```

so automation has a clear verb. `--check` implies compact JSON-friendly
summary + exit codes; default human mode is Rich table.

**Rich table columns (human)**

```text
machine | src | claims | last_scan | scan | chain | envelope | level
```

Ages rendered as human durations (`3d4h`, `never`); levels colorized.

**Cheap follow-on (same release if free):** expose
`steward status --include-imports` already supported by
`collect_status` (gap from baseline) — not required for matrix but
closes the status CLI hole called out in open gaps.

### 4. How each column is collected (cheap defaults)

| Column | Local | Attached (quick) | Attached (full) |
|---|---|---|---|
| Claims | `COUNT` on `main.claims` for `machine_id` | `COUNT` on `m_*.claims` via attach context | same |
| Last scan | Latest finished `scan_runs` for local machine_id | Latest finished in attached schema | same |
| Chain | `--quick`: `unknown`/`skipped` unless recent status cache; `--full`: `verify_chain` | Read `chain_verified_at` + `payload_exists` only | Optionally re-run verify (operator should prefer `db verify --imports`) |
| Envelope | Latest `audit_log` where `action='db_export_created'` timestamp; if none, best-effort newest file under `<data_dir>/exports/` (mtime) | `imported_at` | same |

**Never on quick path:** full claim-table scans for "integrity",
recursive export dir walks beyond a single directory listing,
`fileproviderctl`, network.

Target: **matrix --quick completes in seconds** on multi-GB local DB
with N≤10 attached files (same class as `machines list
--include-imports`).

### 5. Envelope sync SLA on estate health

When ADR-0017 `collect_estate_health` is present, compose:

```text
EstateHealthReport.fleet:
  overall: HealthLevel
  machine_count: int
  attached_count: int
  envelope_sla: EnvelopeSlaSummary   # same object as matrix
  stale_machine_ids: list[str]       # compact, not full rows
```

Add fail-on tokens to estate `health check` (same names as §1) so one
automation surface can gate local + fleet:

- Default estate fail-on set remains **local-only** (0017 defaults).
- `envelope_sla` and `fleet_*` are **opt-in** so single-machine
  installs without weekly export do not false-red.

Snapshot compact dict (0017 JSONL series) stores only:
`overall`, counts, envelope ages/levels, check names — **not** full
per-machine claim tables.

### 6. MCP + dashboard

**MCP (read / plan modes)**

| Tool | Behaviour |
|---|---|
| `fleet_health` | `collect_fleet_health(...); fleet_health_to_dict` — default quick + include_imports |
| `fleet_health_check` | Same + evaluate fail-on kwargs → `{ok, failed, matrix}` |

No write tools. Snapshot write from MCP not in v1 (avoid agent spam).

**Dashboard**

- `GET /api/fleet?quick=1` → matrix payload.
- HTML: "Fleet" table under machines / estate banner when
  `include_imports` or always with a toggle.
- Action: none required (read-only); optional `refresh` reuses
  collect.

### 7. Open-core / `steward-fs` readiness notes

Public extract rules (docs/OPEN_CORE.md):

1. Core fleet types + pure `evaluate_fleet_fail_on` ship in open-core.
2. Infra collector ships (ATTACH + SQL are portable SQLite).
3. No Cerid host paths, Dropbox field notes, or secrets in matrix
   code paths.
4. Launchd weekly-export template may stay private or ship as
   optional sample — matrix only **reads** export audit / exports
   dir; it does not require launchd.
5. PyPI package name remains **`steward-fs`**; CLI entry `steward`.
   Document in OPEN_CORE / PUBLIC_README that multi-machine matrix
   requires operators to copy envelopes out-of-band (ADR-0013
   pull-don't-push) — Steward does not push inventories.
6. Linux CI: matrix tests use temp DBs + synthetic attached files;
   no macOS-only APIs in `infra.fleet`.

### 8. Explicit non-goals

- Always-on multi-host monitor / push sync (needs daemon + transport
  ADR; Alternative C of 0013 still rejected as core path).
- Merging attached audit chains into local (ADR-0013 Alternative B).
- Bulk dual-presence / cloud-retire tracking (ADR-0020).
- Audit-log shrink (ADR-0018).
- Cross-machine semantic search / embeddings on wire (still out).
- Changing apply pre-flight or imported-claim immutability.

## Consequences

**Positive**

- Operators and agents get one matrix for fleet freshness.
- Envelope SLA becomes a first-class gate without scraping CLI.
- Reuses ATTACH fan-out and `attached_inventories` — no schema
  migration required for v1.
- Core purity + open-core portability preserved.
- Complements 0017 without replacing status / machines list.

**Negative / residual**

- Two multi-machine CLIs (`machines list` vs `machines health`) —
  documented: list = identity/counts; health = SLA matrix.
- Attached `chain_level` on quick path is **last-known-good**, not
  live verify — operators who need live chain must run
  `db verify --imports` (existing).
- Local envelope age depends on export actually running (schedule
  0019); matrix reports staleness, it does not fix cadence.
- Fan-out cost grows with N attached DBs (0013 already notes N≤10).

## Alternatives rejected

- **Only extend `MachineSummary`** — bolting ages/levels onto list
  conflates identity listing with health gates and breaks stable
  MCP list shape.
- **Only deepen 0017 `attached_imports`** — insufficient for local
  export SLA + per-machine scan columns as a first-class table;
  estate report would become the matrix UI by accident.
- **New `fleet_snapshots` table in inventory.db** — same multi-GB /
  lock rationale as 0017; use optional data-dir JSONL if history
  needed (defer; matrix is point-in-time first).
- **Push-based sync health (agents phone home)** — violates
  pull-don't-push and no-daemon posture.
- **Require full chain verify on every matrix call** — too expensive
  for multi-GB dogfood; quick/full split is mandatory.

## Implementation notes

### Public API (target)

```python
# steward.core.fleet (or steward.core.health extensions)
FleetThresholds  # scan_max_age_hours, envelope_max_age_hours, ...
MachineHealthRow
EnvelopeSlaSummary
FleetHealthMatrix

def age_hours(now: datetime, iso_ts: str | None) -> float | None: ...
def level_for_age(
    age_hours: float | None,
    *,
    max_hours: float,
    missing: HealthLevel = "fail",
) -> HealthLevel: ...
def evaluate_fleet_fail_on(
    matrix: FleetHealthMatrix,
    fail_on: frozenset[str],
) -> list[HealthCheckResult]: ...

# steward.infra.fleet
def collect_fleet_health(
    *,
    db_path: Path,
    include_imports: bool = True,
    quick: bool = True,
    thresholds: FleetThresholds | None = None,
    data_dir: Path | None = None,  # for exports/ mtime fallback
) -> FleetHealthMatrix: ...

def fleet_health_to_dict(matrix: FleetHealthMatrix) -> dict[str, Any]: ...
```

### Schema

**No migration for v1.** All inputs already exist:

- `meta.machine_id`
- `claims` / `scan_runs` / `audit_log` (+ ATTACH schemas)
- `attached_inventories` (`imported_at`, `chain_verified_at`,
  `file_path`, `exporter_hostname`, …)
- audit action `db_export_created`

Revisit a migration only if we need exporter-side `last_exported_at`
in `meta` for hosts that prune audit aggressively (0018) — out of
scope until chain-archive lands.

### Tests

- **Unit:** age → level matrix; fail-on token evaluation; missing
  timestamps → fail/warn mapping; pure dict shape.
- **Integration:** local DB with finished scan + `db_export_created`
  row → local row ok; attach synthetic import with old `imported_at`
  → envelope warn/fail; missing payload file → `attached_missing`;
  CLI `--check --fail-on envelope_sla` exit 1; MCP tool returns rows;
  dashboard `/api/fleet` 200.
- **Preservation:** none (read-only; no FS tier mutation).
- Confirm import-linter: core.fleet has no infra imports.

### Files expected to touch (implementation slice)

- `docs/adr/0021-fleet-health-matrix.md` (this file)
- `src/steward/core/fleet/` or `src/steward/core/health/` (types + evaluate)
- `src/steward/infra/fleet/` (collect, to_dict)
- `src/steward/cli/machines_cmd.py` (`health` / `health --check`)
- `src/steward/infra/mcp/handlers.py`, `server.py`
- `src/steward/infra/dashboard/{api,server,render}.py`
- `src/steward/infra/health/` when 0017 lands (compose `fleet` section)
- Optional: `src/steward/cli/status_cmd.py` `--include-imports`
- Tests: `tests/unit/core/test_fleet_*.py`,
  `tests/integration/test_fleet_health.py`, MCP/dashboard hooks
- Docs: `OPEN_DEVELOPMENT.md`, `OPEN_CORE.md` (steward-fs note),
  `QUICKSTART.md`, `CHANGELOG.md` when shipping

## Status progression

- **Accepted** — implementation landed with unit + integration tests (core.fleet, infra.fleet, machines health CLI, MCP, dashboard /api/fleet, estate health composition).
- Supersedes nothing; deepens multi-machine operator surfaces and
  feeds ADR-0017 estate health with envelope SLA signals.
