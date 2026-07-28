# ADR 0006: Single database file (`inventory.db`)

**Status:** Accepted
**Date:** 2026-05-16

## Context

Across the v0.1 → v0.3 horizon Steward needs to store: the permanode
inventory, the claim history, the audit chain, embeddings (v0.2), and
the legacy import provenance. Splitting these across multiple files
(SQLite-per-concern, ChromaDB-for-vectors, separate audit DB) would
each be easy individually but make the cross-modality JOINs that are
the entire product value impossible.

## Decision

One SQLite file at `${STEWARD_DATA_DIR}/inventory.db` holds everything.
The schema co-locates:

- `permanodes` + `claims` + `hashes` + `tiers` — the inventory
- `audit_log` — the append-only history
- `embeddings` + `embeddings_vec` (sqlite-vec virtual table) — v0.2
  semantic search; the column shape is locked from day one
- `scan_runs` + `legacy_import_log` + `meta` — provenance

SQLite + WAL gives us concurrent readers + a single writer, atomic
multi-table transactions, and `BACKUP IMMEDIATE` for snapshots.
`sqlite-vec` provides ANN search inside the same file.

## Consequences

- Backup = `cp inventory.db inventory.db.bak` (with `BEGIN IMMEDIATE`).
  Atomic to the byte. No multi-file consistency window.
- Cross-modality queries like "show me all photos in L2 whose nearest
  semantic neighbor is in Backup" become one SQL JOIN.
- File size grows monotonically — 4.2 GB on the live unified-hash.db is
  the starting baseline. v0.2 embeddings add ~1.5 KB per permanode
  (~10 GB at 10^7 permanodes), which is within SQLite's comfortable
  range.
- Writer contention is bounded by WAL's serialised-writer model. For
  Steward's single-operator + occasional-cron pattern this is fine;
  high-frequency-write services would need different storage.

## Alternatives considered

- **SQLite-per-concern** — clean separation but no JOINs.
- **PostgreSQL** — multi-writer + richer indexes but operational
  overhead for a single-operator product.
- **DuckDB** — fast analytics but its WAL + concurrency story is less
  mature for write workloads.
