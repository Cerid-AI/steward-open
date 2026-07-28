# ADR 0003: Append-only audit log with hash chain

**Status:** Accepted
**Date:** 2026-05-16

## Context

The operator + Claude + (eventually) MCP-driven agents all mutate the
inventory. Without a tamper-evident audit trail, a single bug, malicious
edit, or accidental `UPDATE` could rewrite history undetected. Forensic
reversibility (ADR-0002) depends on every prior mutation being
inspectable AND verifiably unmodified.

## Decision

Inventory mutations land in a Samhain-style append-only `audit_log`
table:

- Each row carries `prev_hash` (the previous row's `row_hash`) +
  `row_hash = blake3(prev_hash || canonical_payload(row))`.
- The genesis row uses `prev_hash = "0"*64`.
- SQLite triggers `trg_audit_log_no_update` and `trg_audit_log_no_delete`
  raise `ABORT` on any `UPDATE` or `DELETE`. The append-only property
  is enforced at the SQLite engine layer; even an operator with the
  `sqlite3` CLI can't tamper silently.
- `steward db verify` walks the table in `id` order, recomputes each
  `row_hash`, and reports the first break.

`canonical_payload` excludes the derived `id`, `prev_hash`, `row_hash`
fields and serialises the rest as `json.dumps(sort_keys=True,
separators=(",", ":"))` for byte stability.

## Consequences

- Write cost is higher (every audit row reads the prior row's hash).
  In practice the prior-row lookup is O(log N) via the primary-key
  index and amortises well.
- Tamper-evidence without external KMS. The hash chain itself is the
  evidence.
- Schema migrations that change the column set on `audit_log` would
  invalidate every existing row's hash. The plan calls these out
  explicitly (ADR-0008): retrofit risks force schema-stability for
  the audit table from day one.
- Backups are simple: `cp inventory.db inventory.db.bak` (with
  `BEGIN IMMEDIATE`) captures the chain in its entirety.

## Alternatives considered

- **WORM filesystem** — heavier, OS-specific, doesn't survive backup
  + restore.
- **External signing (TSA)** — requires network calls + a trust root
  Steward doesn't otherwise need.
- **Log per mutation** — granular but lacks the linked-list property
  that lets verify detect tampering.
