# ADR 0022: Inventory surface and data matrix

**Status:** Accepted  
**Date:** 2026-08-07  
**Related:** ADR-0002 (operator-in-the-loop), ADR-0006 (single inventory.db),
ADR-0009 / ADR-0013 (pull-don't-push / ATTACH RO fan-out), ADR-0017 (estate
health), ADR-0020 (dual-presence), ADR-0021 (fleet health matrix),
`docs/OPEN_DEVELOPMENT.md` P2 stats by-volume, dashboard panes

## Context

Steward’s inventory answers “what files do I have, where, and what should
happen next” through **tables and 1-D aggregations**:

| Surface | What it answers | Gap |
|---|---|---|
| `steward stats {by-tier,by-domain,…}` | Top-N on one axis | No **cross-tab** (tier × domain, volume × tier) |
| `claims.volume` indexed | Storage axis present | No first-class **by-volume / capacity** rollup (OPEN_DEV P2) |
| Dual-presence sample | Bounded object presence | No **prefix-level presence matrix** for exploration |
| Fleet health matrix | Machine × health signals | No **machine × content** (bytes by tier) view |
| Dashboard Stats / KPIs | Cards + bars | No **spatial hierarchy** of inventory (treemap / drill) |
| `parent_dir` / path indexes | Query-ready | No **lazy path-tree** API for graphic surface |

Operators and agents still cannot answer, as a first-class surface:

1. **Data matrix (mx):** “Break claims by *A* × *B* (and optional filters)
   with claim_count + total_bytes.”
2. **Graphic surface:** “Show me this inventory as an interactive map
   (size ∝ bytes) with a **dynamic overlay** (domain, extension,
   dual-presence kind, local vs import source, claim age).”

Related open-source patterns (mimic, not reimplement wholesale):

- GrandPerspective / WinDirStat — treemap + color-by property + linked list
- Datasette facets — multi-dim filter counts over SQLite
- dust / dirstat — proportional tree / SVG export without a full GUI
- Dual-presence / fleet matrices already in Steward — extend the same
  “matrix” product language to **content** dimensions

### Constraints (non-negotiable)

1. No FS mutation of tiers without apply `--dry-run|--execute` (ADR-0002).
   Surface selection may **seed** plan artefacts only; never one-click
   unlink from the canvas.
2. Append-only `audit_log` (ADR-0003). Read paths write nothing; optional
   rollup refresh may write **meta** / data-dir cache only (same family as
   status rollups).
3. `steward.core` must not import `infra` or `cli` (import-linter).
4. Multi‑GB inventory: default paths must stay **cheap**. Full-table
   treemap builds and unbounded cross-products are forbidden. Prefer
   **lazy depth-1 drill**, rollup cache, and `LIMIT` on high-cardinality
   axes.
5. Pull-don't-push (ADR-0009/0013): attached imports are **read overlay**
   only; apply still cannot target imported claims.
6. No bulk claim path rewrite (field notes / dual-presence).
7. No always-on viz daemon; loopback dashboard + CLI + MCP only.
8. Dashboard stays **no CDN** (inline CSS/JS) unless a later ADR allows
   optional assets.
9. Open-core portable types and pure evaluators in `core`; host I/O in
   `infra`.
10. Commit policy: human-authored only.

## Decision

### 1. Two product surfaces, one shared cube

| Name | Role |
|---|---|
| **Data matrix (mx)** | Low-level multi-dimensional aggregation + facet filters over claims |
| **Inventory surface** | Graphic projection of matrix/tree cells (treemap drill + dynamic overlay) |

The matrix is the **source of truth API**. The surface is a **view** that
consumes matrix / tree payloads. Agents use matrix first; humans use surface.

### 2. Allowed dimensions and measures

**Measures (always):** `claim_count`, `permanode_count`, `total_bytes`

**Group dimensions (Wave A):**

| Key | Column / derivation | Cardinality notes |
|---|---|---|
| `tier` | `claims.tier` | low |
| `domain` | `claims.domain` | medium |
| `volume` | `claims.volume` | low–medium |
| `extension` | `claims.extension` | high → require `limit` |
| `classification` | `claims.classification` | high → require `limit` |
| `machine_id` | `claims.machine_id` | low (fleet content) |
| `source` | `local` \| `attached` when `include_imports` | low |

**Derived / later (Wave B–C, not required for MVP):**

| Key | Notes |
|---|---|
| `path_segment` | Lazy tree child under a prefix (surface drill) |
| `presence_kind` | Dual-presence; FS probe or stratified sample only |
| `claim_age_bucket` | From `observed_at` / `mtime_iso` buckets |

**Filters (AND):** same keys as dimensions, plus `path_prefix` (SQL
`file_path LIKE ? || '%'` with bound param), `is_current=1` always default.

### 3. Cross-tab API contract

```text
CrossStatsRequest
  dim_a: DimensionKey          # required
  dim_b: DimensionKey | None   # optional second axis
  measure: MeasureKey = total_bytes  # sort key
  filters: dict[str, str | list[str]]
  path_prefix: str | None
  limit: int                   # max cells; default 50; max 500
  include_imports: bool = False

CrossStatsCell
  a: str | None
  b: str | None                # None when dim_b omitted
  claim_count: int
  permanode_count: int
  total_bytes: int

CrossStatsResult
  generated_at, request echo, cells: list[CrossStatsCell], truncated: bool
```

Rules:

- `dim_a == dim_b` → error.
- High-cardinality dim without `limit` → clamp to default 50.
- Empty inventory → empty cells, not error.
- Fan-out uses existing `_claims_source_clause` / ATTACH pattern from
  `infra.stats`.

### 4. Path tree (surface feed) — lazy depth-1

```text
PathTreeRequest
  path_prefix: str             # "" or absolute prefix; normalized
  depth: int = 1               # MVP: only 1 supported
  measure: total_bytes | claim_count
  color_by: OverlayKey | None  # domain | extension | tier | source | none
  filters: …
  include_imports: bool
  child_limit: int = 100

PathTreeNode
  path: str                    # full path of this node
  name: str                    # basename segment
  is_dir: bool                 # True for aggregate children; leaves optional later
  claim_count, permanode_count, total_bytes
  overlay_value: str | None    # dominant category for color_by (mode by bytes)
  children: list[PathTreeNode] # only for depth>0 response root
```

**Algorithm (MVP):** for current claims under `path_prefix`, group by
**next path segment** after the prefix (derived in SQL or Python from
`file_path` / `parent_dir`). Direct files under the prefix may appear as
leaf nodes if `parent_dir == path_prefix`.

Do **not** materialize a full multi-level tree in one request at multi‑GB
scale. Drill-down = new request with longer prefix.

### 5. Module placement

| Concern | Module |
|---|---|
| Dimension allowlist, request validation, pure overlay dominance helpers | `steward.core.matrix` (new) |
| SQL aggregators: `by_volume`, `cross`, `path_tree_depth1` | `steward.infra.stats` (+ optional `infra/stats_matrix.py` if file grows) |
| Presence-by-prefix aggregation (stratified / cached) | `steward.infra.dual_presence` + matrix types |
| CLI | `steward stats cross`, `by-volume`; later `steward surface` |
| MCP | `inventory_cross_stats`, `inventory_path_tree` |
| Dashboard | `GET /api/stats?axis=cross&…`, `GET /api/surface`, Surface panel + inline JS treemap |
| Plan seed (later) | Selection JSON → TSV skeleton under `data_dir/plans/` with ADR-0002 gates |

### 6. Dynamic overlay (graphic)

Overlay is a **client color mapping** over tree/cross cells:

| Overlay | Cell field |
|---|---|
| `none` | monochrome by size |
| `domain` | `overlay_value` = domain with max bytes in node |
| `extension` | dominant extension |
| `tier` | dominant tier |
| `source` | local vs attached (when include_imports) |
| `presence` (later) | dual / store_only / … from presence matrix |

Linked brushing (Wave B): selecting a node sets a shared filter that
reloads Stats bars and Inspector search prefix.

### 7. Non-goals (this ADR)

- Live disk walk for the surface (inventory-only; optional “refresh scan”
  is existing scan CLI).
- DaisyDisk radial UI as MVP (treemap first).
- DuckDB/Parquet analytics pack (optional Wave C export only).
- Hydrus-style free tags (separate ADR).
- Claim path rewrite; one-click delete from canvas.
- CDN-hosted D3/Plot libraries in default dashboard.

## Consequences

### Positive

- Agents get a stable multi-dim query without inventing SQL.
- Dashboard gains GrandPerspective-class exploration without abandoning
  loopback / no-CDN invariants.
- Dual-presence and fleet matrices become the same “mx” language as
  content pivots.
- by-volume closes an OPEN_DEVELOPMENT P2 item.

### Costs / risks

- Path-segment grouping can be CPU-heavy on huge prefixes → require
  `child_limit`, prefer volume/tier filters, optional rollup cache later.
- Cross-tabs with two high-cardinality axes explode → enforce `limit` and
  disallow extension × classification without filters in validation.
- Inline JS treemap will grow `render.py`; keep logic in a dedicated
  string section / later split file if needed.

## Alternatives considered

1. **Only improve Rich CLI tables** — insufficient for graphic exploration
   goal; rejected as sole deliverable.
2. **Embed Datasette** — powerful but heavy dependency and second server
   model; reject for core; optional external consumer via export later.
3. **Full path_rollups table always maintained at scan** — best eventual
   performance; defer until lazy API proves too slow (premature schema
   weight on scan hot path).
4. **Neo4j permanode graph viz** — already deferred in OPEN_DEVELOPMENT;
   out of scope.

## Implementation plan

Authoritative task breakdown:

[`docs/superpowers/plans/2026-08-07-inventory-surface-data-mx.md`](../superpowers/plans/2026-08-07-inventory-surface-data-mx.md)

## Acceptance (ADR level)

- [x] `cross` + `by_volume` available via stats module, CLI, JSON
- [x] MCP tools for cross + path_tree
- [x] Dashboard Surface panel: depth-1 treemap + overlay switcher
- [x] No destructive action without existing apply gates
- [x] Tests cover pure validation + SQL aggregators on synthetic DB
- [x] OPEN_DEVELOPMENT / ROADMAP note the arc; open-core extract includes
      `core.matrix` when shipped (re-export still an open ops task)
