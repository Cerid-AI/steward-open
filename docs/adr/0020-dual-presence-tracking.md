# ADR 0020: Dual-presence / cloud-truth tracking

**Status:** Accepted  
**Date:** 2026-08-05  
**Related:** ADR-0002 (operator-in-the-loop), ADR-0014 (`retire_direct`),
ADR-0015 (verify==unlink / mount prefer), ADR-0017 (estate health model),
ADR-0019 (plan backlog `dual_presence_unfiltered`), field notes
2026-07-13 / 2026-07-28, OPEN_DEVELOPMENT phase 7 bulk cloud retire

## Context

Cloud-FP retires are still **blocked at operator scale** for the right
reason: inventory is overwhelmingly **store-path** claims, store and
mount can be **forked materializations** (different `st_dev`, dual-write
isolation confirmed), and cloud-propagating `retire_direct` must
**verify==unlink on the mount** (ADR-0015). Unlinking a store-only
object does not equal a cloud delete.

Today the pieces are fragmented:

| Surface | What it does | Gap |
|---|---|---|
| `steward.core.fp_paths` | Pure store ↔ mount mapping | No existence / dual-presence kind |
| `steward.infra.fp_status` | Layout health + fixed `DualSample` rels | Sample is layout smoke only (~5 paths); not plan-scale |
| `scripts/filter-plan-dual-presence.py` | Offline TSV bucketer | Not importable library; not wired to CLI/MCP/health/plans |
| ADR-0017 `EstateHealthReport.fp` | Wraps `FPHealthVerdict` | Explicit non-goal: bulk dual-presence tracking |
| ADR-0019 blocked reason | `dual_presence_unfiltered` token | No producer that clears it with a real filter artifact |
| Bulk path rewrite | Tempting “fix inventory to mount” | **Forbidden** — stale import / dual index risk (field notes) |

Operators and agents cannot answer, as a **first-class surface**:

> Of this plan (or this sample of Dropbox claims), how many paths are
> dual-present vs store-only vs conflict-named, and which bucket is safe
> for cloud-propagating `retire_direct`?

Field reality (Mac Pro dogfood):

- ~221k `retire_direct` plan; prefix split ≠ per-file dual-presence.
- External-drive FP (`external_drive_fp`) is **supported healthy**.
- Selective Sync Conflict basename forks break store→mount mapping.
- Mount full-tree rescan often times out; store remains inventory
  authority while dual-presence FS checks gate **cloud intent**.

### Constraints (non-negotiable)

1. No FS mutation without apply `--dry-run|--execute` (ADR-0002). Dual-
   presence **probe and filter** only read the FS and rewrite **plan
   artefacts under data dir / operator `--out`**, never tier trees and
   never `UPDATE claims`.
2. Append-only `audit_log` (ADR-0003).
3. `steward.core` must not import `infra` or `cli`.
4. No always-on dual-presence daemon.
5. **ADR-0015 laws:** `verify_path == unlink_path` always on apply;
   default cloud mode = mount for both; local reclaim =
   `--allow-store-path-unlink`. Dual-presence does not invent split
   verify/unlink.
6. **No bulk claim path rewrite** (field notes / OPEN_DEVELOPMENT).
7. External-drive FP (forked `st_dev`, residual Domains.plist unlinked
   with dual roots) remains **supported** — dual-presence is about
   **object** presence, not equal devices.
8. Every `except Exception` → `log_swallowed_error`; mypy strict.
9. Commit policy: human-authored only.

## Decision

### 1. Dual-presence is a pure classification + bounded I/O library

Introduce a reusable library with a clean core/infra split.

#### Core (`steward.core.dual_presence`)

I/O-free types and path policy (open-core portable):

```text
PresenceKind = Literal[
  "dual",                 # store + mount both exist (safe cloud candidate*)
  "store_only",           # store exists, mount does not
  "mount_only",           # mount exists, store does not
  "missing_store",        # claim/store path does not exist
  "conflict_name_path",   # relative path contains Selective Sync Conflict segment
  "outside_store_root",   # cannot map under configured store root
  "mount_error",          # mount side raised OSError / timeout class
  "unknown",              # not probed / skipped
]

DualPresenceClass
  kind: PresenceKind
  relative: str | None
  store_path: str | None
  mount_path: str | None
  notes: tuple[str, ...]

# Pure:
classify_presence_kind(
  *, store_exists: bool | None, mount_exists: bool | None,
  relative: str | None, conflict_suffix: str = " (Selective Sync Conflict)",
  store_error: bool = False, mount_error: bool = False,
) -> PresenceKind

map_claim_to_pair(claim_path: str, *, store_root: str | None = None,
                  mount_root: str | None = None) -> DualPresenceClass
  # uses steward.core.fp_paths; no os.stat
```

\* **Safe cloud candidate** means: dual-present **and** not conflict-named
**and** operator intent is cloud-propagating. Apply still enforces
verify==unlink + optional `--require-fp-healthy`.

**Conflict paths are never `dual` for cloud bulk filters**, even if both
sides happen to exist under conflict renames — mapping is unreliable.

#### Infra (`steward.infra.dual_presence`)

FS probes + plan/inventory consumers:

```text
probe_pair(store_path: Path, mount_path: Path, *, timeout_s: float | None)
  -> PresenceProbe   # kind, sizes optional, errors, latency_ms

DualPresenceStats
  counted: int
  dual: int
  store_only: int
  mount_only: int
  missing_store: int
  conflict_name_path: int
  outside_store_root: int
  mount_error: int
  unknown: int
  dual_bytes: int | None          # when sizes available
  store_only_bytes: int | None
  sample_limit: int | None
  truncated: bool
  store_root: str
  mount_root: str
  intent: Literal["cloud_retire", "local_reclaim", "observe"]

filter_plan_rows(
  rows: Sequence[Mapping[str, str]],
  *,
  path_col: str = "source_path",
  store_root: Path | None = None,
  mount_root: Path | None = None,
  limit: int = 0,
  progress: Callable | None = None,
) -> FilterResult
  # buckets: dict[PresenceKind, list[row]]
  # stats: DualPresenceStats
  # does not mutate inventory.db

write_filtered_plans(
  result: FilterResult,
  *,
  out_dir: Path,
  comments: Sequence[str] = (),
  fieldnames: Sequence[str],
) -> FilterArtifacts
  # plan-dual.tsv, plan-store_only.tsv, … + filter-stats.json
  # data-dir or operator --out only
```

**Performance defaults (multi-GB / 221k plans):**

| Mode | Behaviour |
|---|---|
| Plan filter | Sequential `Path.exists` / cheap `stat`; optional per-path timeout; no recursive walks; no `fileproviderctl dump` |
| Health sample | Cap **N** paths (default 32–64) from current DropboxStorage claims **or** fixed rel samples when DB omitted |
| Inventory full count | **Not** a default — O(claims)×mount latency is unsafe; stats are sample- or plan-scoped |
| Mount congestion | `mount_error` / timeout → bucket `mount_error`; never silent promote to dual |

Promote the existing script to a **thin CLI wrapper** over the library
(`scripts/filter-plan-dual-presence.py` remains for offline use; body
calls `infra.dual_presence`).

### 2. Intent-aware bucket safety (no bulk path rewrite)

| Intent | Rows that may proceed toward apply | Notes |
|---|---|---|
| **cloud_retire** (default) | `dual` only | Mount verify==unlink; filter out store_only, conflict, mount_error |
| **local_reclaim** | `dual` ∪ `store_only` (operator explicit) | Apply must use `--allow-store-path-unlink`; audit records local reclaim |
| **observe** | all kinds | Stats / health only; no “safe for execute” claim |

**Hard rules**

1. Library **never** rewrites `claims.file_path` or invents dual-index
   rows. Optional dual-index remains a **future ADR** if needed after
   rematerialized mount claims (OPEN_DEVELOPMENT phase 8).
2. Filter output is **child plan TSVs** (and optional ADR-0019 plan
   backlog child registration), not in-place inventory mutation.
3. Clearing ADR-0019 `dual_presence_unfiltered` requires an attached
   filter artefact: `filter-stats.json` (and preferably `plan-dual.tsv`)
   registered beside the plan under `plans/by-id/<id>/` or referenced
   by path on the backlog record.
4. Apply path is **unchanged** for path resolution: still
   `resolve_fp_paths` + verify==unlink. Dual-presence is a **pre-apply
   plan hygiene** layer, not a second path policy.

### 3. Inventory-level dual-presence stats on estate health

Extend ADR-0017 composition (do not replace `FPHealthVerdict`):

```text
EstateHealthReport
  …
  fp                    FPSection              # layout + cloud_retire_ready
  dual_presence         DualPresenceSection | None   # NEW (this ADR)
  …

DualPresenceSection
  stats: DualPresenceStats          # bounded sample
  cloud_safe_sample_ratio: float | None   # dual / (dual+store_only+…) among probed
  layout: LayoutKind | None         # from fp_status when composed
  ready_for_cloud_filter: bool      # mount present + not store-only samples only
  checks: list[HealthCheckResult]   # optional named: dual_presence_poor
  notes: tuple[str, ...]
```

**Collection rules (cheap-default)**

- Default `health check --quick`: either skip dual_presence (`unknown`)
  **or** reuse fp_status fixed samples only (no claim table scan).
- `health show` / `--probes` / `--full`: optional SQL sample of
  `claims` where `tier='DropboxStorage' AND is_current=1` with
  `ORDER BY id` + `LIMIT N` (or random via pre-chosen ids) — **never**
  full table walk for health.
- Snapshot compact form keeps counts only (drop per-path lists).

**Named check token (opt-in fail-on)**

| Token | Fail when |
|---|---|
| `dual_presence_poor` | Among probed non-error paths, `dual / (dual+store_only) < threshold` (default 0.5) **and** mount exists — signals bulk cloud retire still unsafe |

Default `health check` fail-on set does **not** include
`dual_presence_poor` (same rationale as `fp_not_ready`: non-FP hosts).

### 4. CLI / MCP / dashboard surfaces

#### CLI

```text
# Plan hygiene (primary bulk path)
steward plans filter-dual-presence
  --manifest PATH
  --out-dir PATH
  [--store-root PATH] [--mount-root PATH]
  [--limit N] [--path-col source_path]
  [--intent cloud_retire|local_reclaim|observe]
  [--json]
  [--register-with PLAN_ID]   # attach filter artefact to backlog when 0019 shipped

# Observe / sample without a plan
steward fp dual-presence
  [--sample N]                # default 32; from inventory if --db
  [--db PATH]
  [--rels a,b,c]              # fixed relatives (fp_status style)
  [--json]

# Script compatibility
python scripts/filter-plan-dual-presence.py …   # thin wrapper
```

`steward fp status` continues to show layout dual **samples**; it may
link to `fp dual-presence` for larger samples without growing the
status default path.

#### MCP (read / plan modes)

| Tool | Mode | Behaviour |
|---|---|---|
| `dual_presence_sample` | read | Bounded sample stats (+ optional rel list) |
| `filter_plan_dual_presence` | plan | Run filter; write under data-dir `runs/` or `plans/by-id/…`; return stats + paths; **no** apply execute |

No MCP tool unlinks files or rewrites claims.

#### Dashboard

- FP / health pane: dual-presence **counts** chip (from
  `DualPresenceSection` or last filter-stats sidecar).
- Plan detail (when 0019 Queues pane exists): blocked chip clears when
  filter artefact present and dual bucket non-empty for cloud intent.

### 5. Relationship to existing ADRs

| ADR | Interaction |
|---|---|
| **0014** | Unchanged: `retire_direct` is the action; dual-presence only selects **which rows** are plan-safe for cloud intent |
| **0015** | Unchanged path law; dual-presence **requires** mount twin for cloud filter default |
| **0017** | Adds `dual_presence` section; does not collapse into `fp` alone |
| **0019** | Supplies concrete meaning for `dual_presence_unfiltered` + child plan registration |
| **0018** | Orthogonal (audit shrink) |

### 6. Explicit non-goals

- Bulk `UPDATE claims` store→mount or dual-index table in inventory.db.
- Provider HTTP API cloud-trash confirmation (still manual / browser).
- Always-on continuous dual-presence scanner daemon.
- Replacing `--require-fp-healthy` (layout gate) with dual-presence
  alone — both remain: layout readiness **and** per-object dual filter
  for bulk cloud.
- Full-claim dual-presence census as a default health or stats command.
- Changing skip-verify or apply transaction semantics.

## Consequences

**Positive**

- First-class, testable dual-presence kinds shared by health, plans,
  scripts, MCP, and dashboard.
- Bulk cloud retire unblocked **by process**: filter → dual TSV →
  dry-run → execute with existing ADR-0015 apply path.
- ADR-0019 blocked-reason becomes actionable.
- External-drive FP remains supported; forked devices do not false-fail
  dual classification.
- Inventory stays honest (store authority until mount rescan); no silent
  path rewrite.

**Negative / residual**

- Filter cost is O(plan rows)×mount latency; 221k plans need batching /
  limits / caffeinate — document in runbook, not hide.
- Sample-based health stats can disagree with full plan filter (by
  design; health is posture, filter is execute hygiene).
- `mount_error` under congestion may under-count dual; operators re-run
  filter when FP settles.
- Two plan files (full vs dual) increase operator discipline; mitigated
  by backlog registration + blocked chips.

## Alternatives rejected

- **Only grow `fp_status` DualSample list** — still layout smoke; not
  plan-reusable; couples health probe to bulk filter.
- **Keep script-only forever** — agents/MCP/health cannot share types;
  tests drift from dogfood script.
- **Dual-index claims now** — high risk while inventory is legacy store
  import + conflict renames; field notes defer to post-rematerialization.
- **Silent store→mount path rewrite on filter** — reintroduces gap #1 /
  wrong cloud semantics; forbidden.
- **Treat different `st_dev` as dual-failure** — contradicts supported
  `external_drive_fp` layout (ADR-0015 field correction v0.3.19).
- **Put dual-presence census table in inventory.db** — multi-GB bloat +
  write lock vs scans; use plan sidecars + health samples instead.

## Implementation notes

### Public API (target)

```python
# steward.core.dual_presence
PresenceKind = Literal[...]
@dataclass(frozen=True, slots=True)
class DualPresenceClass: ...
def classify_presence_kind(...) -> PresenceKind: ...
def map_claim_to_pair(claim_path: str, ...) -> DualPresenceClass: ...
def is_conflict_relative(relative: str, *, suffix: str = ...) -> bool: ...
CLOUD_SAFE_KINDS: frozenset[PresenceKind]  # frozenset({"dual"})
LOCAL_RECLAIM_KINDS: frozenset[PresenceKind]  # dual | store_only

# steward.infra.dual_presence
@dataclass(frozen=True, slots=True)
class PresenceProbe: ...
@dataclass(frozen=True, slots=True)
class DualPresenceStats: ...
@dataclass(frozen=True, slots=True)
class FilterResult: ...
def probe_pair(...) -> PresenceProbe: ...
def sample_claim_paths(con, *, tier: str = "DropboxStorage", limit: int = 32) -> list[str]: ...
def collect_dual_presence_stats(paths: Sequence[str], *, intent: str = "observe", ...) -> DualPresenceStats: ...
def filter_plan_rows(...) -> FilterResult: ...
def write_filtered_plans(...) -> FilterArtifacts: ...
def dual_presence_stats_to_dict(stats: DualPresenceStats) -> dict[str, Any]: ...
```

### Integration touch points

- `infra.fp_status`: optional reuse of `probe_pair` / shared
  `DualSample` construction; avoid circular imports (fp_status may keep
  private `_sample_pair` or call dual_presence).
- `infra.health` (0017): compose `DualPresenceSection` when module
  present.
- `cli/fp_cmd.py`, `cli` plans command (0019) or interim `fp_cmd` /
  `plans` subgroup.
- `infra/mcp/handlers.py` + `server.py`.
- `docs/runbooks/cloud-fp-retire.md` + `dropbox-rectification.md`:
  prefer library CLI over raw script.
- Thin `scripts/filter-plan-dual-presence.py`.

### Tests

- **Unit (core):** conflict detection; map store/mount/claim forms;
  classify matrix (exists flags × errors); cloud_safe vs local sets.
- **Unit (infra):** tmp store/mount trees → probe + filter buckets;
  outside_root; mount_error injection; stats counts.
- **Integration:** write mini plan TSV → filter → dual bucket only
  contains dual paths; script wrapper exit 0; MCP tool shape when
  registered.
- **Preservation:** existing `test_fp_retire_laws` unchanged (verify==
  unlink); add assertion that filter does not open apply / unlink.
- **No** multi-GB full census in CI.

### Files expected to touch

- `docs/adr/0020-dual-presence-tracking.md` (this file)
- `src/steward/core/dual_presence.py` (or `core/dual_presence/`)
- `src/steward/infra/dual_presence.py` (or package)
- `src/steward/cli/fp_cmd.py` and/or plans CLI
- `src/steward/cli/main.py` (register)
- `src/steward/infra/fp_status.py` (optional share probe)
- `src/steward/infra/health/*` when 0017 lands (DualPresenceSection)
- `src/steward/infra/mcp/{handlers,server}.py`
- `scripts/filter-plan-dual-presence.py` (thin)
- `tests/unit/core/test_dual_presence.py`
- `tests/unit/infra/test_dual_presence.py`
- `tests/integration/test_dual_presence_filter.py`
- Runbooks + `OPEN_DEVELOPMENT.md` + `CHANGELOG.md` on ship

## Status progression

- **Accepted** — dual-presence library + CLI/MCP/health section shipped;
  no claim rewrite path; unit + integration tests green.
- Supersedes nothing; fills ADR-0017 non-goal and ADR-0019 filter gap.
