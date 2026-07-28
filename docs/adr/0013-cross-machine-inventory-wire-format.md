# ADR 0013: Cross-machine inventory wire format

**Status:** Accepted
**Date:** 2026-05-16

## Context

v0.2.9 shipped multi-machine **awareness** — `steward machines list`
and `machines show` aggregate over the existing `machine_id` axis on
claims / scan_runs / audit_log, and the MCP server exposes the same
view. But every row still comes from a single local `inventory.db`.
Truly cross-machine work needs a **wire format**: a way to take the
inventory from machine A, hand it to machine B, and let B query A's
claims without giving B a path to mutate them.

The constraints that shape the format:

1. **Pull-don't-push (ADR-0009).** An imported inventory is queried,
   never executed. Machine B must NOT be able to drive a
   `steward apply --execute` against a claim that originated on
   machine A, even by accident.

2. **Audit chain integrity (ADR-0003).** `audit_log.prev_hash` chains
   across all rows in a single table, regardless of `machine_id`.
   Splicing machine A's audit rows into machine B's `audit_log` table
   would break B's chain — B's `verify_chain` would fail at the
   splice point. The wire format must preserve A's chain verifiably
   on its own.

3. **Single-file inventory (ADR-0006).** Everything lives in one
   SQLite file. The wire format should not require a parallel data
   model — it should be the same schema, transported.

4. **Schema continuity (ADR-0008).** `machine_id` is already on every
   relevant row from day one. The wire format does not need a
   migration; it needs a transport.

5. **Privacy.** The inventory contains file paths, basenames, hashes,
   and audit history. The transport must support compression and
   atomic transfer; future ADRs may layer encryption.

## Decision

**The wire format is a SQLite database file, packaged in a tar.xz
envelope alongside a JSON manifest. Imported inventories are mounted
read-only via `ATTACH DATABASE`, never merged into the local
`inventory.db`.**

Specifically:

### 1. Envelope format

```
inventory-<machine_id_short>-<iso8601>.tar.xz
├── manifest.json          # wire-format metadata (see below)
├── inventory.db           # SQLite payload (single file, schema as ADR-0006)
└── checksums.txt          # blake3 hashes of the two above
```

Tar + LZMA (xz) is stdlib-only. zstd was the original design preference
for speed but zstandard isn't in the dependency set and the compression
ratio LZMA gives on a SQLite payload is already excellent. The
`wire_format_version` field in the manifest is the formal evolution
point; future versions may switch compressors.

`manifest.json` schema:

```json
{
  "wire_format_version": 1,
  "exported_at": "2026-05-16T15:30:00+00:00",
  "exporter": {
    "steward_version": "0.3.0",
    "schema_version": "0001",
    "machine_id": "f3c2a1d4-...",
    "hostname": "mac-pro-2026"
  },
  "payload": {
    "filename": "inventory.db",
    "size_bytes": 5242880,
    "blake3": "abc123...",
    "audit_rows": 12345,
    "claim_rows": 178000,
    "permanode_rows": 56000
  },
  "excluded_tables": ["embeddings", "embeddings_vec", "legacy_import_log"]
}
```

### 2. What's in the payload

| Table | Included | Why |
|---|---|---|
| `permanodes` | Yes | Content identity — the cross-machine join key. |
| `claims` | Yes | The observations. Each carries its origin `machine_id`. |
| `hashes` | Yes | Multi-algo hash history; useful for cross-machine integrity spot-checks. |
| `scan_runs` | Yes | Provenance — operator may ask "when did machine A last scan this tree?" |
| `audit_log` | Yes | The chain — verifies independently per ADR-0003. |
| `meta` | Yes | `machine_id` and `schema_version` (the importer cross-checks both). |
| `tiers` | No | Per-machine tier-mount configuration; tier names overlap across machines but mean different things (machine A's "L2" ≠ machine B's "L2"). |
| `embeddings` / `embeddings_vec` | No | Large (384 floats × millions of rows). Optional inclusion via `--with-embeddings` flag; default excludes. |
| `legacy_import_log` | No | Records the local `unified-hash.db` import path; meaningless on another machine. |

### 3. How imports are stored

Imported snapshots live at:

```
~/.local/share/steward/imports/<exporter_machine_id>/<iso8601>.db
```

The local `inventory.db` gets a new `attached_inventories` table:

```sql
CREATE TABLE attached_inventories (
    machine_id        TEXT PRIMARY KEY,    -- exporter's machine_id (UUID)
    file_path         TEXT NOT NULL,       -- absolute path to the imported .db
    imported_at       TEXT NOT NULL,
    exporter_version  TEXT NOT NULL,
    exporter_hostname TEXT,
    payload_blake3    TEXT NOT NULL,       -- from manifest.json
    audit_rows        INTEGER NOT NULL,
    chain_verified_at TEXT,                -- last time `db verify --imports` checked this chain
    notes             TEXT
) STRICT;
```

The file is never modified after import. Cross-machine queries open
the local DB plus each attached inventory via:

```sql
ATTACH DATABASE 'file:/path/to/imported.db?mode=ro' AS m_<short_id> KEY '';
```

The `?mode=ro` URI flag means the OS-level file handle is read-only;
even a coding bug can't write through the attachment. Queries
fan out via UNION ALL:

```sql
SELECT * FROM claims
UNION ALL SELECT * FROM m_abc123.claims
UNION ALL SELECT * FROM m_def456.claims
WHERE permanode_id = ?
```

### 4. CLI surface

```
steward db export <out.tar.xz> [--with-embeddings]
    Exports the local inventory.db as a portable snapshot.
    Equivalent to `db backup` + add manifest + tar/zstd.

steward db import <in.tar.xz>
    Unpacks the envelope, verifies blake3 + audit chain, copies the
    payload .db to ~/.local/share/steward/imports/<machine_id>/,
    upserts a row into attached_inventories. Refuses if the exporter
    machine_id matches the local machine_id (you can't import your
    own inventory).

steward db imports list
    Show attached inventories: machine_id, hostname, age, row counts,
    last chain-verify timestamp.

steward db imports detach <machine_id_prefix>
    Removes the row from attached_inventories and unlinks the .db.
    Audit row appended to local audit_log.

steward db verify --imports
    Runs verify_chain on each attached inventory; updates
    attached_inventories.chain_verified_at.
```

### 5. The pull-don't-push invariant

`steward apply --execute` is structurally prevented from touching
imported claims by two mechanisms:

1. **Path lookup**: `apply` translates manifest rows to file paths via
   the local DB only. Imported claims live in attached schemas — the
   apply loader never SELECTs from `m_*.claims`.
2. **Pre-flight check**: before executing, `apply` verifies every
   `claim_id` in the manifest exists in the **local** `claims` table
   (not an attached one). Mismatch → exit 2 with a clear error.

Read-side surfaces are extended (with care) to span attached schemas:

| Surface | Reads imports? | Notes |
|---|---|---|
| `steward inspect <hash>` | Yes via `--machine <id>` flag | v0.2.12 already filters by machine prefix. |
| `steward machines list/show` | Yes | The reason multi-machine awareness shipped in v0.2.9. |
| `steward stats` | Yes via `--include-imports` flag | Default excludes — most operators want LOCAL aggregates. |
| `steward search <query>` | No in v0.3 | Embeddings excluded from wire format; cross-machine semantic search is a v0.4 question. |
| MCP read tools | Yes (mirrors CLI flags) | `inventory_stats` gets `include_imports`. |
| `steward dashboard` | Yes with explicit toggle | Top-of-page selector: "local only" / "all machines". |
| MCP write tools | No (structural) | Same lookup path as `apply`. |
| `steward replicate run` | No | Operates on the local inventory; the policy file is local. |
| `steward archive snapshot` | No | Same. |

### 6. Audit chain semantics

Each machine's `audit_log` chain is independent — verified on import,
re-verified on `db verify --imports`. The local audit chain is
**unaffected** by imports — the only mutation to the local DB is the
insert into `attached_inventories`, which appends one
`inventory_attached` row to the local audit chain.

A future ADR may revisit cross-machine chain semantics (e.g. a
machine-of-record manifest signed by exporter, verified by importer
with a public key). For v0.3 the per-machine chain is the integrity
unit.

## Alternatives considered

### Alternative A: JSONL + manifest (no SQLite payload)

Export every table as JSON Lines, importer rebuilds the SQLite shape
on the receiving side.

**Why rejected:** the schema is the contract (ADR-0006). Round-tripping
through JSON forces every column type to traverse a parse/serialize
boundary; STRICT mode column-affinity, the audit-chain triggers, and
the sqlite-vec virtual table all become "the importer's
responsibility" — i.e. another surface where machine A and machine B
can disagree. A SQLite payload is the same artifact on both sides.

### Alternative B: Merge imported claims into the local DB

Drop machine A's rows into machine B's tables, using `machine_id` as
the discriminator.

**Why rejected:** breaks the audit chain (machine A's `prev_hash`
values don't link into machine B's chain), and undermines the
read-only enforcement (an `apply` bug could touch a row attributed to
the wrong machine). ATTACH gives us OS-level read-only and chain
isolation for free.

### Alternative C: Cloud-mediated sync (S3/etc.)

Export pushes to S3; import pulls from S3. Skip the local artifact.

**Why rejected:** orthogonal concern. The transport question (file
copy / NAS share / S3 / rclone destination) is layered on top of the
wire format. v0.3 ships the wire format; an operator can move it via
any tool. A future "cross-machine sync policy" can wrap rclone here.

### Alternative D: protobuf / msgpack wire format

Define a schema in proto3, codegen for both sides.

**Why rejected:** every Steward schema change becomes two artifacts
(SQLite migration + proto migration). The alembic story is already
the schema-evolution surface. Doubling it is not worth the wire-size
savings on a snapshot that compresses well as tar.xz.

## Consequences

**Positive:**

- No second schema; the wire format **is** the schema.
- Audit chain verifiable per-machine, without splicing.
- `apply --execute` cannot touch imported claims by construction —
  it never SELECTs from attached schemas.
- ATTACH DATABASE is a stable SQLite feature; no new dependencies.
- The exporter is a thin wrapper around the existing `db backup`
  (v0.2.14); the importer is a thin wrapper around blake3 verify +
  `ATTACH DATABASE`.
- Cross-machine `inspect` / `stats` / `machines` queries are UNION ALL
  — straightforward SQL; SQLite optimizer handles them.

**Negative:**

- Cross-machine queries fan out across N attached files; per-query
  overhead grows with N. Expected to be fine at N≤10 machines; if a
  v0.4 operator wants to attach 100 inventories, we revisit (likely
  by collapsing to "warm cache" tables per attached inventory).
- The `attached_inventories` table is now a piece of operator state
  the schema has to migrate forward. Added in the v0.3 migration.
- An operator can hand-edit the imported .db (OS read-only via URI
  flag is process-scoped, not POSIX-permission-set). The next
  `db verify --imports` catches the tamper but doesn't prevent it.
  ADR-0003 explicitly accepts this — tamper-evidence over
  tamper-prevention.
- The wire format excludes `tiers` — a cross-machine `inspect` shows
  imported claims with their raw tier name (`L2`, `Backup`, etc.)
  but cannot resolve them to local mount points. This is intentional
  (you can't apply imported claims locally) but operators may need
  educating.

## Migration path

This ADR governs v0.3.0. Work that lands under it:

1. **Schema migration** `0003_attached_inventories.py` — adds the new
   table.
2. **`db export`** subcommand — wraps `db backup` + manifest.json +
   tar.xz. Wire-format version 1.
3. **`db import`** subcommand — unpacks, verifies, attaches.
4. **`db imports {list, detach}`** subcommands.
5. **`db verify --imports`** flag — runs verify_chain on each
   attached inventory.
6. **Read-side fan-out**: `inspect`, `machines`, `stats`,
   `dashboard`, MCP read tools.
7. **Pre-flight check in `apply`** — refuses if any claim_id is not
   in the local `claims` table.

Each piece is its own sprint; v0.3.0 ships when (1)–(5) are in. (6)
and (7) are v0.3.x.
