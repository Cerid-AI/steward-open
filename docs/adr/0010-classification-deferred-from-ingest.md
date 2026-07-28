# ADR 0010: Classification deferred from ingest

**Status:** Accepted
**Date:** 2026-05-16

## Context

The sprawl-audit walker hard-coded its classification logic (`domain_hint`
and `CLUSTERS`) into the ingest path. Every classification rule change
required a full re-walk to update. With 6.4M files in the live database,
that's many hours and a lot of unnecessary I/O for what is, fundamentally,
a string-matching pass over already-known data.

## Decision

Classification runs as a **separate pass** over already-ingested claims:

- `steward scan` writes claims with `classification=NULL`.
- `steward classify [--reclassify-all | --since <ts>]` walks claims
  and updates the `classification` column from the active
  `classification.yml` policy.
- Reclassifying after a YAML edit is a sub-second operation against
  the SQLite index; no filesystem walk required.

The same separation applies to domain hinting: `claims.domain` is the
quick path-based domain (photos / music / video / documents / ...);
`classification` is the policy-driven cluster label
("Work-Cannon-AFB", "Media-Recovered-From-Trash", etc.).

## Consequences

- Policy iteration is cheap. Edit the YAML, re-run `classify`, inspect
  the diff.
- The ingest path is simpler — no policy state at scan time.
- Re-classification doesn't reset the audit chain; it appends
  `reclassify` audit rows (when wired in v0.1.1+).
- Schema column for `classification` exists from day one (ADR-0008
  pattern).

## Alternatives considered

- **Classify at scan time** (sprawl-audit precedent) — fast but
  rigid; re-classification == re-walk.
- **Classify on demand** (lazy) — adds latency to every read; cache
  invalidation problem moves from disk to query time.
