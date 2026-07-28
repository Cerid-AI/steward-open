# ADR 0008: Machine-id claim attribution from day one

**Status:** Accepted
**Date:** 2026-05-16

## Context

v0.1 ships single-machine — the only machine is the Mac Pro. v0.3
activates multi-machine claim distribution (cross-machine plan
distribution, pull-only inventory replication). Retrofitting
`machine_id` across an active claim + audit history would require
rewriting every audit row's `row_hash`, breaking the tamper-evidence
property (ADR-0003).

## Decision

Every claim and every audit row carries a `machine_id` column from
v0.1's `0001_initial.py`. The id is:

- Stored as a key/value pair in `meta` (`machine_id`)
- Seeded on first `steward db migrate` (uuid4)
- Read by `resolve_machine_id` and passed into every claim / audit
  insert

v0.1 is single-machine, so every row has the same `machine_id`. The
column is trivial today; it's the *shape* that matters.

Other forward-compatible columns landed in the same vein:
- `embeddings.model_name` + `model_version` (v0.2 e5-small swap)
- `claims.classification` (M4 reclassify pass)

## Consequences

- v0.3 cross-machine sync becomes a "merge two claim streams by
  permanode and concatenate" operation. No schema change.
- Slightly heavier rows on disk (~37 bytes per uuid4 per claim).
  Negligible against typical claim payload size.
- Forces operators to think about machine identity from the start.

## Alternatives considered

- **Add `machine_id` in v0.3** — requires a migration that rewrites
  the entire `audit_log`. Breaks ADR-0003 tamper-evidence guarantee
  during the migration window.
- **Hostname-as-id** — easy to get wrong (DHCP renames, /etc/hostname
  edits). uuid4 + first-run seeding is stable.
