# ADR 0018: Audit-log chain-archive and shrink

**Status:** Accepted (phases A–B) — seal + verify shipped; shrink (phase D) open  
**Date:** 2026-08-05  
**Accepted (partial):** 2026-08-07 — phase B: `steward db audit-archive` seal/verify, migration `0003_audit_chain_segments`, no hot shrink

## Context

`audit_log` is append-only and blake3-chained (ADR-0003). SQLite
`BEFORE UPDATE` / `BEFORE DELETE` triggers make silent rewrite
impossible; `steward db verify` walks `ORDER BY id` and recomputes
every `row_hash`.

At multi-GB inventory scale the live chain grows without bound.
`steward db audit-export` (v0.3.17) already cold-exports JSONL for
forensics and off-box backup, but **explicitly does not delete or
shrink** — export alone cannot reclaim space inside `inventory.db`.

Constraints that any shrink design must satisfy:

1. **Tamper-evidence is law (ADR-0003).** Removing rows from the hot
   table must not invent a history where altered pasts still verify.
2. **No silent UPDATE/DELETE of audit rows on the application path.**
   Triggers stay. Any rebuild is an explicit operator procedure.
3. **Operator-in-the-loop (ADR-0002).** Destructive inventory rewrite
   requires `--dry-run` | `--execute` (exit 2 if neither).
4. **Single-file inventory (ADR-0006).** Cold history may live *beside*
   `inventory.db` under the data dir; the hot inventory remains one
   SQLite file.
5. **`steward.core` purity.** Hash/chain math stays in `core.audit`;
   segment I/O and rebuild live in `infra`.
6. **No always-on daemon.** Archive is CLI (and optionally MCP-read /
   plan); launchd can invoke the CLI later — no new long-lived process.
7. **Foundation before full shrink.** Estate-health and multi-GB
   operability must not wait on table rebuild. Cold segment format +
   verify + health metrics ship first; `--execute` shrink is a later
   slice of the same ADR.

`docs/IMPLEMENTATION_PLAN.md` already sketched
`~/.local/share/steward/execution-log/` for archived audit entries;
this ADR makes that layout and the re-anchor contract concrete.

## Decision

### 1. Two surfaces, not one

| Surface | Mutates hot `audit_log`? | Purpose |
|---|---|---|
| `steward db audit-export` | **No** (read-only JSONL) | Ad-hoc forensics, filters by time/action/limit |
| `steward db audit-archive` | **Only on `--execute` rebuild** | Contiguous chain segments + optional shrink |

`audit-export` remains the filterable cold dump. **Chain archive is
not filterable by action** — segments must be a contiguous `id`
prefix so chain continuity is well-defined.

### 2. Segment model (archive → verify → re-anchor)

A **chain segment** is a contiguous closed interval of audit rows
`[first_id, through_id]` that:

- Starts at the live chain’s current left edge:
  - genesis (`id` of the first remaining hot row after prior archives,
    or the absolute first row when none exist), **or**
  - the row immediately after the previous segment’s `through_id`
- Ends at operator-chosen `through_id` (must be `< max(id)` unless
  the operator is archiving everything and accepting an empty hot
  window — empty hot is allowed only with an explicit flag)
- Contains every intermediate `id` (no holes)

**Segment envelope** (under `${STEWARD_DATA_DIR}/execution-log/` by
default):

```
audit-segment-<machine_id_short>-<through_id>-<iso8601>.tar.xz
├── manifest.json       # segment metadata (see below)
├── audit.jsonl         # one row per line, id ASC, full columns
└── checksums.txt       # blake3 of manifest.json + audit.jsonl
```

`manifest.json` (normative fields):

```json
{
  "segment_format_version": 1,
  "kind": "audit_chain_segment",
  "created_at": "2026-08-05T12:00:00+00:00",
  "exporter": {
    "steward_version": "0.x.y",
    "schema_version": "0002",
    "machine_id": "…"
  },
  "range": {
    "first_id": 1,
    "through_id": 90000,
    "row_count": 90000,
    "genesis_prev_hash": "000…0",
    "tip_hash": "<row_hash of through_id>",
    "first_prev_hash": "<prev_hash of first_id>"
  },
  "payload": {
    "filename": "audit.jsonl",
    "size_bytes": 123456789,
    "blake3": "…"
  },
  "prior_tip_hash": null
}
```

- `genesis_prev_hash` is `GENESIS_PREV_HASH` for the absolute first
  segment; for later segments it is the previous segment’s `tip_hash`
  (also stored as `prior_tip_hash` for clarity).
- `tip_hash` is the sole **re-anchor** value the hot verifier needs.
- JSONL rows include `id`, `timestamp`, `machine_id`, `actor`,
  `action`, `permanode_id`, `claim_id`, `manifest_run_id`,
  `payload_json`, `prev_hash`, `row_hash` — the same columns
  `verify_chain` already reads. No re-hashing at export time beyond
  recomputation for verify.

### 3. Verify before any shrink

Archive commit order is fixed:

1. **Export** segment to a temp path (or dry-run: count + tip only).
2. **Verify segment** offline: walk JSONL, recompute each `row_hash`
   with `core.audit.compute_row_hash`, check `prev_hash` linkage and
   that `first_prev_hash` / `tip_hash` match the manifest.
3. **Cross-check live DB**: for each archived `id`, live `row_hash`
   equals segment `row_hash` (and live tip at `through_id` equals
   `tip_hash`). Refuse archive if live `verify_chain` is already
   broken.
4. **Seal** envelope (blake3 + rename into `execution-log/`).
5. **Record commit intent** by appending one hot audit row:

   - `action = "audit_archive_commit"`
   - payload: `through_id`, `first_id`, `row_count`, `tip_hash`,
     `segment_path` (relative to data dir), `segment_blake3`,
     `segment_format_version`

6. **Optional shrink (`--execute --shrink`)**: table rebuild (below).
   Without `--shrink`, steps 1–5 alone are a **sealed archive** —
   durable cold history + audit evidence, zero hot deletion. This is
   the foundation slice.

`steward db audit-archive --verify <segment.tar.xz>` is a pure
read-only subcommand (also usable on segments copied off-box).

### 4. Hot re-anchor without rewriting retained row hashes

After a successful sealed archive of `[first_id, through_id]`, the
hot table may still hold those rows until shrink. Shrink removes only
rows with `id <= through_id` via **table rebuild**, not
`DELETE FROM audit_log` on the live table:

```text
CREATE TABLE audit_log_new (…same schema…);
INSERT INTO audit_log_new SELECT * FROM audit_log WHERE id > :through_id;
— drop old table + triggers; rename; recreate APPEND-ONLY triggers;
— optional VACUUM (explicit --vacuum)
```

**Retained rows keep their original `id`, `prev_hash`, and
`row_hash`.** The first remaining hot row still has
`prev_hash = tip_hash` of the archived segment. That is intentional:
we do **not** re-chain the hot suffix (re-chaining would rewrite
every subsequent `row_hash` and destroy continuity with any prior
export of those rows).

**Verifier contract change** (`repo_audit.verify_chain` /
`steward db verify`):

| Mode | Behaviour |
|---|---|
| Default (hot) | Resolve **active anchor** = tip of the latest sealed segment whose range was removed from hot (or `GENESIS_PREV_HASH` if none). Start `prev_expected` at that anchor; walk remaining hot rows. |
| `--full` | Verify each sealed segment in `execution-log/` (or registry paths) in order, then hot against the last tip. |
| Import / wire payloads | Unchanged: attached inventories still verify their own full chain from genesis (ADR-0013). |

Active anchors are discovered by scanning hot rows with
`action = 'audit_archive_commit'` **whose `through_id` is no longer
present in hot `audit_log`**, ordered by `through_id`. Sealed
segments that were archived but not yet shrunk do not change the hot
start (rows still present → genesis or prior shrunk tip still
applies).

Optional convenience registry table (migration, **not** a substitute
for the audit commit row):

```sql
CREATE TABLE audit_chain_segments (
    id              INTEGER PRIMARY KEY,
    sealed_at       TEXT NOT NULL,
    first_id        INTEGER NOT NULL,
    through_id      INTEGER NOT NULL,
    row_count       INTEGER NOT NULL,
    tip_hash        TEXT NOT NULL,
    prior_tip_hash  TEXT,                 -- NULL = GENESIS
    segment_relpath TEXT NOT NULL,
    segment_blake3  TEXT NOT NULL,
    shrunk_at       TEXT,                 -- NULL until rebuild
    audit_row_id    INTEGER NOT NULL      -- the audit_archive_commit id
) STRICT;
```

Registry rows are written only during archive commit (same
transaction family as the audit append). They may be UPDATE’d only
for `shrunk_at` (or replaced by a new row version if we later decide
the registry must also be append-only — **not required for v1**).
The hash chain of `audit_log` remains the forensic authority;
the registry is an index for `verify --full` and status.

### 5. CLI / operator contract

```text
steward db audit-archive --through-id N --dry-run
steward db audit-archive --through-id N --execute
steward db audit-archive --through-id N --execute --shrink [--vacuum]
steward db audit-archive --before <ISO-8601> …   # resolve through_id = max(id) with timestamp < before
steward db audit-archive --verify <segment>
steward db verify              # hot + active anchors
steward db verify --full       # cold segments + hot
```

Rules:

- Missing `--dry-run` / `--execute` → exit 2 (ADR-0002 pattern).
- `--shrink` requires `--execute`.
- Refuse shrink if sealed segment for that range is missing or fails
  verify.
- Refuse archive if live chain is broken.
- Keep a configurable **hot minimum** (default: do not archive the
  newest N days or leave at least K rows — exact defaults are
  implementation knobs, not forensic ones).
- Actor: `steward-cli` (or cron actor when invoked from launchd).

MCP: **read/plan only** in this ADR — e.g. expose segment list +
verify result on status/tools. No MCP `audit_archive_execute` without
a follow-up that reuses ADR-0016 gating patterns.

### 6. Phased delivery (foundation can ship without shrink)

| Phase | Ships | Shrink? |
|---|---|---|
| **A — ADR + health** | This ADR; status fields: `audit_entries`, oldest/newest audit ts, estimated audit table bytes if cheap, last `audit_archive_commit`, sealed segment count; document cold export vs archive | No |
| **B — Seal + verify** | Segment writer + offline verify + `audit_archive_commit` row + registry; `audit-archive --execute` without `--shrink` | No |
| **C — Hot re-anchor verify** | `verify_chain` active-anchor start; `db verify --full` | No |
| **D — Shrink** | Table rebuild + trigger recreate + optional VACUUM; preservation tests | Yes |

Phases A–C unblocked multi-GB operability and forensic offload.
Phase D is the only step that reclaims SQLite pages.

### 7. Explicitly not decided here

- Encryption of segments at rest (future; paths may leave the host).
- Automatic launchd cadence for archive (template only if demanded;
  still CLI under the hood).
- Merging cold segments back into hot (restore-for-forensics is
  attach/read of the segment, not splice into `audit_log`).
- Changing genesis constant or `canonical_payload` column set
  (still forbidden without a chain-breaking migration ADR).
- Always-on monitor / daemon.

## Consequences

**Positive**

- Cold history remains fully re-verifiable offline with the same
  blake3 chain math as live (`core.audit`).
- Hot `verify` stays O(hot rows), not O(lifetime rows).
- Shrink never rewrites retained `row_hash` values — prior
  `audit-export` dumps of the hot suffix remain consistent.
- Foundation (health + sealed archive) ships without waiting for
  rebuild risk.
- Append-only triggers remain on the hot table at all times except
  the brief rebuild transaction under `--execute --shrink`.

**Negative / residual risk**

- Full forensic proof of ancient history requires operators to
  retain sealed segments; losing a segment is permanent gap in
  `--full` verify (mitigation: segment blake3 on the
  `audit_archive_commit` row + offsite copy of `execution-log/`).
- Table rebuild + VACUUM is exclusive and can be slow on multi-GB
  DBs; must not run concurrent with long scans (operator runbook).
- `audit_chain_segments.shrunk_at` is a non-chained side table;
  forensic authority stays on `audit_log` + segment envelopes.
- Default hot verify no longer walks lifetime history — operators
  must know `--full` exists (document in QUICKSTART + status).

## Alternatives considered

- **Soft-delete / tombstone flag on rows** — does not shrink
  `inventory.db`; rejected for the multi-GB goal.
- **Drop triggers and `DELETE`** — violates ADR-0003 spirit; any
  bug becomes silent history rewrite. Rebuild under explicit
  execute is the only allowed removal mechanism.
- **Re-chain hot suffix under a synthetic genesis row** — would
  change every retained `row_hash`, invalidating prior exports and
  complicating proofs. Rejected.
- **Separate audit.db always** — splits ADR-0006 single-file
  inventory and breaks “backup = one file” for the hot path.
  Cold segments beside the DB are enough.
- **Rely on `audit-export` only** — already shipped; does not
  reclaim space or define re-anchor.
- **External TSA / cosign** — network trust root Steward does not
  otherwise need (same rejection as ADR-0003).

## Related

- ADR-0002 operator-in-the-loop  
- ADR-0003 append-only audit chain (law this ADR extends, not weakens)  
- ADR-0006 single database file  
- ADR-0013 cross-machine inventory wire format (payload chain still
  verifies from genesis; local hot re-anchor is independent)  
- `steward db audit-export` (v0.3.17 cold export)  
- `docs/OPEN_DEVELOPMENT.md` P1 audit-log shrink item  
- `docs/IMPLEMENTATION_PLAN.md` `execution-log/` layout  
