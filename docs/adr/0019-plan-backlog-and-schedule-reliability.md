# ADR 0019: Plan backlog and schedule reliability

**Status:** Accepted  
**Date:** 2026-08-05  
**Related:** ADR-0002 (operator-in-the-loop), ADR-0004 (YAML policy),
ADR-0006 (single inventory.db), ADR-0011/0016 (MCP plan/write + plan_token),
ADR-0015 (FP path policy), ADR-0017 (estate health model + snapshot series),
OPEN_DEVELOPMENT continuous stewardship / bulk plan operability

## Context

Continuous stewardship produces **plans** and depends on **schedules**,
but neither is a first-class tracking surface today.

### Plan backlog gap

| Surface today | What it stores | Gap |
|---|---|---|
| `steward policy plan --out <tsv>` | TSV under operator path / `runs/` | Summary is console-only (`PlanSummary`); no durable backlog index |
| MCP `policy_plan` | Writes default `runs/mcp-plan.tsv` | Overwrites a single path; no list/show of prior plans |
| MCP `plan_token` (ADR-0016) | `<data_dir>/runs/mcp-plan-tokens/*.json` | Binds one dry-run for execute; not a plan catalogue |
| Dashboard Policies pane | Bundled policy names + Plan action | No “what plans exist, size, blocked?” queue |
| Field bulk Dropbox retire | 221k-row TSV + offline filter script | Blocked reasons live in runbooks/notes, not structured objects |

Operators and agents cannot answer:

> What open plans exist, how large are they (rows + bytes), which
> actions dominate, and what blocks safe execute?

`manifest_run_id` already ties apply/audit rows to one plan, but
**generation** does not register that id as a backlog object with
action counts, estimated bytes, or blocked reasons.

### Schedule reliability gap

| Surface today | What it covers | Gap |
|---|---|---|
| `steward schedule list` | Bundled templates + installed plist path exists? | No loaded state, last exit, cadence, overdue |
| `steward schedule status <name>` | Raw `launchctl print` dump | Operator must parse; not JSON/automation-friendly |
| Dashboard schedules table | name / label / installed | Same thin signal as list |
| ADR-0017 `ScheduleHealth` | Template presence on cheap path; optional print on full | Explicitly thin; no overdue-vs-cadence model |

macOS dogfood expects launchd jobs (nightly archive/replicate, weekly
verify/export) to **run on cadence**. Missing install, non-zero last
exit, or a calendar window that has not fired are reliability
failures — not estate inventory integrity failures (ADR-0017), but
they belong on the continuous-tracking surface.

### Relationship to ADR-0017

ADR-0017 defines **estate health** (is the estate safe to plan/retire?)
and a **compact health snapshot series** for sparklines under
`<data_dir>/health/`. This ADR defines **operator work tracking**:

1. **Plan backlog** — first-class plan records (not only TSV files).
2. **Schedule reliability** — installed / last exit / overdue vs cadence.
3. **Dashboard Queues pane** + history charts that **consume** health
   snapshots (0017) and plan/schedule collectors (this ADR).
4. **MCP list/show** for the plan backlog (read/plan modes).

Do **not** fold this into ADR-0017: 0017 is already a large composite
health contract; plan backlog is a separate product object with its
own persistence, lifecycle, and MCP tools. Schedule reliability
**deepens** the 0017 schedule section via a shared collector rather
than duplicating health levels.

### Constraints (non-negotiable)

1. No FS mutation without apply `--dry-run|--execute` (ADR-0002). Plan
   **registration** only writes under the data dir (TSV + index), not
   inventory tiers.
2. Append-only `audit_log`; never UPDATE/DELETE audit rows (ADR-0003).
3. `steward.core` must not import `infra` or `cli`.
4. No always-on daemon without a new ADR; launchd templates + CLI/MCP
   /dashboard loopback only.
5. Prefer not growing multi-GB `inventory.db` with telemetry/backlog
   series (same rationale as ADR-0017 health JSONL).
6. Every `except Exception` → `log_swallowed_error`; mypy strict.
7. macOS-first schedule probes; portable core types for open-core.

## Decision

### 1. Plan backlog is a data-dir registry, not an inventory table

**Choice:** persist plans under the Steward data directory beside
inventory (same family as health snapshots and MCP plan tokens).

```text
<STEWARD_DATA_DIR>/
  inventory.db
  plans/
    index.jsonl                 # one compact PlanBacklogRecord per line
    LATEST                      # plan_id of most recently registered plan
    by-id/<plan_id>/
      summary.json              # full record (pretty JSON)
      plan.tsv                  # manifest bytes (or hardlink/copy of --out)
      dry_run.json              # optional latest apply_dry_run summary
```

**Rationale**

1. Live inventory is multi-GB; backlog index must not add write-lock
   contention during long scans (field notes + ADR-0017).
2. Plans are **operator artefacts**, not claim truth — no FK into
   permanodes/claims required for list/show.
3. `manifest_run_id` remains the forensic join key into `audit_log`
   when apply runs; the backlog record **mirrors** that id as
   `plan_id` by default.
4. Matches existing data-dir patterns (`runs/`, `health/`,
   `mcp-plan-tokens/`).

**Rejected:** `plan_backlog` STRICT table inside `inventory.db` for
v1 (easy SQL, wrong place for multi-GB + scan locks). Revisit only if
cross-machine export of open plans becomes a product requirement
(would then ride ADR-0013 wire format, not this ADR).

### 2. `PlanBacklogRecord` contract

```text
PlanBacklogRecord
├── plan_id                 str   # == manifest_run_id (default)
├── created_at              ISO-8601 UTC
├── machine_id              str
├── policy                  # name + resolved path + kind
│   ├── name                str   # e.g. retention.yml
│   ├── path                str
│   └── kind                str   # RetentionPolicy | PromotionPolicy | …
├── filters
│   ├── root_prefix         str | None
│   ├── phase_name          str | None
│   └── max_files           int | None
├── action_counts           dict[str, int]  # stash, retire_direct, nas_manifest, promote, …
├── rows_total              int
├── estimated_bytes         int             # sum(size_bytes) of rows
├── blocked_reasons         tuple[str, ...] # stable tokens (see §2.1)
├── status                  PlanStatus      # see §2.2
├── manifest_path           str
├── manifest_sha256         str | None
├── dry_run                 DryRunDigest | None
└── notes                   tuple[str, ...]
```

#### 2.1 Blocked-reason tokens (v1)

| Token | When set |
|---|---|
| `empty_plan` | `rows_total == 0` |
| `fp_not_ready` | Plan has `retire_direct` (or other FP-gated actions) and current FP verdict is not cloud-retire ready (probe optional at register time) |
| `dual_presence_unfiltered` | Heuristic: large `retire_direct` plan on DropboxStorage paths without an attached dual-presence filter sidecar / note (operator/field 221k class) |
| `oversize_for_mcp` | `rows_total` > `STEWARD_MCP_MAX_FILES_CAP` (informational; does not block CLI apply) |
| `dry_run_errors` | Latest dry-run reported errors > 0 |
| `manifest_missing` | Registered path no longer exists on disk |
| `stale_inventory` | Optional: inventory rollup/scan older than threshold at register time (soft) |

Blocked reasons are **advisory labels** for queues and agents. They do
not replace apply preflight or ADR-0002 execute gates.

#### 2.2 Status lifecycle

```text
registered → dry_run_ok | dry_run_failed | blocked
dry_run_ok → applied | partially_applied | superseded | expired
```

- **registered** — plan TSV written + index append.
- **blocked** — one or more hard blocked_reasons present (operator
  still can apply via CLI after fixing filters; status is tracking).
- **dry_run_*** — updated when `apply --dry-run` / MCP `apply_dry_run`
  is pointed at this plan (best-effort hook).
- **applied / partially_applied** — derived from audit rows for
  `manifest_run_id` when operator runs refresh, **or** updated by
  apply path after execute (best-effort; audit remains authority).
- **superseded** — newer plan for same policy+filters registered.
- **expired** — retention prune of very old plans (index keeps tombstone
  compact line optional; default delete by-id dir after N days).

Status updates rewrite `summary.json` and append a new index line
(last-writer-wins by `plan_id` when listing). Index is **append-friendly
JSONL**, not an append-only forensic chain (unlike `audit_log`).

### 3. Registration path (when plans become backlog objects)

| Trigger | Register? |
|---|---|
| `steward policy plan` | **Yes** by default; `--no-register` opt-out; still writes `--out` |
| MCP `policy_plan` | **Yes** — write under `plans/by-id/<id>/plan.tsv` (stable path) in addition to optional `out_path` |
| Dashboard action `policy_plan` | Same as MCP handler |
| External/hand-edited TSV | `steward plans register --manifest PATH` (explicit) |

Registration is **not** a destructive tier mutation; no `--execute`
required. It may create directories under the data dir only.

`PlanSummary` (infra) gains: `plan_id`, `estimated_bytes`,
`blocked_reasons`, `registered_path` so CLI prints the backlog id.

### 4. CLI / MCP / dashboard surfaces

#### CLI

```text
steward plans list [--json] [--status STATUS] [--policy NAME] [--limit N]
steward plans show <plan_id> [--json]
steward plans register --manifest PATH [--policy NAME]
steward plans refresh <plan_id>   # recompute status from audit + dry_run sidecar; no FS tier mutation
steward plans prune [--older-than-days N] [--dry-run|--execute]  # data-dir only
```

`steward policy plan` remains the generator; `steward plans *` is the
backlog browser. Schedule stays under `steward schedule`.

#### MCP

| Tool | Mode | Behaviour |
|---|---|---|
| `plan_backlog_list` | read | Compact records from index (filters optional) |
| `plan_backlog_show` | read | Full `summary.json` + optional head of action counts |
| `policy_plan` | plan | Existing + auto-register; return `plan_id` + blocked_reasons |

No MCP tool deletes tier files. Prune of data-dir plan artefacts is
CLI-only with ADR-0002 flags if it removes files.

#### Dashboard

- **Queues pane:** open plans (status, rows, estimated_bytes, blocked
  chips) + in-flight stash summary (from status) + schedule overdue
  chips.
- **History charts:** sparklines from ADR-0017
  `GET /api/health/series` (overall level, scan age, free bytes, stash
  count). Optional thin series of plan backlog counts later without
  new ADR if stored as compact points beside health or in plans index
  stats.
- **Schedules table upgrade:** columns installed / loaded / last_exit /
  overdue / cadence (from §5).
- API: `GET /api/plans`, `GET /api/plans/<id>`,
  `GET /api/schedule/reliability` (or fold reliability into analysis
  bundle + health payload).

### 5. Schedule reliability model

#### 5.1 Types (core, pure)

```text
ScheduleCadence
  kind: calendar | interval | unknown
  weekday: int | None   # 0=Sun … launchd convention
  hour: int | None
  minute: int | None
  interval_seconds: int | None

ScheduleJobReliability
  name: str
  label: str
  installed: bool              # plist file present
  loaded: bool | None          # launchctl print rc==0 when probed
  cadence: ScheduleCadence
  last_exit_status: int | None
  last_exit_at: ISO | None     # when parseable from print / log mtime fallback
  last_start_at: ISO | None
  overdue: bool | None
  overdue_grace_hours: float
  level: ok | warn | fail | unknown | skipped
  message: str
  details: dict
```

#### 5.2 Cadence extraction

- Prefer **installed** plist (operator may have edited) else bundled
  template.
- Parse with `plistlib`: `StartCalendarInterval` (dict or list of dicts)
  or `StartInterval`.
- Multi-interval calendars: overdue if **all** windows are overdue
  (implementation: next expected fire in the past by > grace).

#### 5.3 Last exit

1. Primary: parse `launchctl print gui/<uid>/<label>` for
   `last exit code = N` and related timestamps when present.
2. Fallback: mtime of `{LOG_DIR}/<name>.err` / `.out` + scan last
   lines for non-zero hints (best-effort; never raise).
3. Quick path (health check cheap default): installed + cadence only;
   skip `launchctl print` unless `--full` / explicit reliability collect.

#### 5.4 Overdue evaluation (pure core)

Given `now`, `cadence`, `last_exit_at` or `last_start_at`, and
`grace_hours` (default 6h for daily, 24h for weekly):

- If not installed → `level=warn`, `overdue=None` (or treat missing
  **expected** templates as warn when schedule module present).
- If installed but never run and first expected window has passed →
  `overdue=True`, `level=warn`.
- If last successful-ish run older than cadence period + grace →
  `overdue=True`.
- If `last_exit_status not in (0, None)` → `level=fail` (or warn if
  exit unknown semantics).
- Non-macOS / no launchctl → all `level=unknown`, checks skipped.

#### 5.5 Integration

| Consumer | How |
|---|---|
| `steward schedule list --json` | Add reliability columns (cheap + optional `--probe`) |
| `steward schedule status <name> --json` | Structured reliability + raw print under `raw` |
| ADR-0017 `collect_estate_health` | `schedule` section populated by `collect_schedule_reliability` |
| Health check `--fail-on` | Optional later token `schedule_overdue` (not default fail-on in 0017 v1) |
| Dashboard | Schedules pane + Queues overdue chips |

Soft-import `steward.infra.schedule` remains (open-core may strip
launchd); collectors return empty/unknown without hard dependency
breakage.

### 6. Module placement

| Concern | Module | Notes |
|---|---|---|
| `PlanStatus`, blocked-reason constants, pure overdue math, cadence parsing helpers that need no FS | `steward.core.plans` + `steward.core.schedule_cadence` (or `core.tracking`) | Portable; unit-tested |
| Register/list/show/prune plan files | `steward.infra.plans` | data_dir I/O |
| Extend `PlanSummary` + auto-register from `plan()` | `steward.infra.db.plan` | Call register after write_manifest |
| Schedule reliability collect + launchctl parse | `steward.infra.schedule.reliability` | Uses templates + launchctl |
| CLI | `steward.cli.plans_cmd` + extend `schedule_cmd` | Register in `cli/main.py` |
| MCP | `handlers` + `server` | `plan_backlog_list/show` |
| Dashboard | `api`, `server`, `render` | Queues pane + charts + schedule columns |
| Estate health | `infra.health` (0017) | Compose schedule reliability |

**Hard rule:** `core` stays pure; no SQLite, no launchctl, no data_dir.

### 7. Estimated bytes and action counts

Computed at plan generation time from manifest rows (O(rows) already
paid by reconciler write):

```python
action_counts = Counter(r.action for r in manifest.rows)
estimated_bytes = sum(r.size_bytes for r in manifest.rows)
```

Optional later: dual-presence filter may produce a **child** plan
record with parent_plan_id and reduced counts (out of scope for v1
schema field except optional `notes` / `parent_plan_id` reserved key).

### 8. Explicit non-goals

- Always-on plan watcher / daemon.
- Automatic apply of backlog plans.
- Storing full dual-presence sample lists on the plan record.
- Migrating historical ad-hoc TSVs from `/tmp` without
  `plans register`.
- Changing apply / stash / retire_direct semantics or plan_token rules.
- Putting plan backlog into the cross-machine inventory wire format.
- Default health `check` fail-on for schedule (opt-in only).

## Consequences

**Positive**

- Agents can `plan_backlog_list` / `show` without filesystem grepping.
- Operators see queues: open plans, blocked reasons, stash, overdue
  schedules in one dashboard pane.
- Schedule reliability is structured (exit + overdue), not only
  “plist exists”.
- inventory.db stays free of backlog bloat; multi-GB path protected.
- ADR-0017 health series powers history charts without a second
  telemetry invention.

**Negative / residual**

- Two plan identifiers in operator speech (`plan_id` vs path) —
  mitigated by default `plan_id == manifest_run_id`.
- JSONL index last-writer-wins is not forensic; audit_log remains
  authority for what was applied.
- launchctl print parsing is best-effort across macOS versions;
  fallbacks required.
- Auto-register writes more files under data_dir; prune policy needed
  for heavy planners.
- Open-core strips schedule module → reliability unknown (acceptable).

## Alternatives rejected

- **Fold entirely into ADR-0017** — overloads estate health; plan
  backlog is a product queue with lifecycle, not a health check.
- **`plan_backlog` table in inventory.db** — multi-GB + scan lock
  contention; rejected for v1.
- **Only improve `PlanSummary` printout** — does not give list/show
  MCP or dashboard queues.
- **systemd/cron abstraction** — macOS-first; no portable scheduler
  invent in this ADR.
- **Require `--execute` to register plans** — registration is not tier
  mutation; would punish planning workflow.

## Implementation notes

### Public API (target)

```python
# steward.core.plans (names illustrative)
PlanStatus = Literal[
    "registered", "blocked", "dry_run_ok", "dry_run_failed",
    "applied", "partially_applied", "superseded", "expired",
]
# PlanBacklogRecord dataclass / pydantic model (I/O-free)
def evaluate_plan_blocked_reasons(...) -> tuple[str, ...]: ...

# steward.core.schedule_cadence
def parse_cadence_from_plist_dict(d: dict) -> ScheduleCadence: ...
def evaluate_overdue(cadence, *, now, last_run_at, grace_hours) -> bool: ...

# steward.infra.plans
def register_plan_from_manifest(...) -> PlanBacklogRecord: ...
def list_plans(*, status=None, policy=None, limit=50) -> list[PlanBacklogRecord]: ...
def show_plan(plan_id: str) -> PlanBacklogRecord: ...
def refresh_plan_status(plan_id: str, *, db_path: Path) -> PlanBacklogRecord: ...

# steward.infra.schedule.reliability
def collect_schedule_reliability(*, probe: bool = True) -> list[ScheduleJobReliability]: ...
```

### Tests

- **Unit:** blocked-reason matrix; cadence parse from fixture plists;
  overdue edge (daily/weekly/grace); compact index round-trip pure
  helpers.
- **Integration:** temp data_dir — `policy plan` registers; list/show
  MCP + CLI; schedule reliability with mocked launchctl print;
  dashboard `/api/plans` 200; Queues pane HTML contains plan rows when
  fixtures present.
- **Preservation:** none required (no tier FS mutation). Plan prune
  with `--execute` only removes data_dir plan artefacts in tests.

### Files expected to touch (implementation slice)

- `docs/adr/0019-plan-backlog-and-schedule-reliability.md` (this file)
- `src/steward/core/plans/` (or `core/tracking/`) — types + pure eval
- `src/steward/core/schedule_cadence.py` — pure cadence/overdue
- `src/steward/infra/plans/` — register/list/show/index
- `src/steward/infra/db/plan.py` — auto-register hook
- `src/steward/infra/schedule/reliability.py` — collect + parse
- `src/steward/cli/plans_cmd.py` + `cli/schedule_cmd.py` + `cli/main.py`
- `src/steward/infra/mcp/handlers.py`, `server.py`
- `src/steward/infra/dashboard/{api,server,render}.py`
- `src/steward/infra/health/` (0017 compose schedule section)
- Tests: `tests/unit/core/`, `tests/unit/infra/`, `tests/integration/`
- Docs: `OPEN_DEVELOPMENT.md`, `QUICKSTART.md`, `CHANGELOG.md` when shipping

## Status progression

- **Accepted** — v0.5 continuous tracking foundation implemented (plan backlog registry, schedule reliability, dashboard Queues, MCP list/show).
- Supersedes nothing; complements ADR-0017 (health) without replacing it.
