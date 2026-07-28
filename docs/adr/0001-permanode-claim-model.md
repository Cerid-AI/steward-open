# ADR 0001: Permanode + claim model

**Status:** Accepted
**Date:** 2026-05-16

## Context

Steward needs a data model that survives files moving, getting renamed,
duplicated, and re-discovered across multiple tiers and (eventually)
multiple machines. The legacy sprawl-audit `unified-hash.db` collapsed
identity + location into a single `files` table, which made queries
like "where else does this content live?" expensive and made claim
history impossible to reconstruct.

## Decision

Adopt a Perkeep-inspired two-table model:

- **Permanode** — one row per `(canonical_hash, size_bytes)`. The
  deduplication identity. Same bytes anywhere produce the same
  permanode. `permanode_id = blake3(canonical_hash || ":" || size_bytes)[:32]`
  (deterministic, machine-independent).

- **Claim** — one row per `(permanode, machine, file_path, container_path,
  scan_run_id)`. An observation of a permanode at a location at a moment.
  Claims accumulate; never overwritten. A moved file produces a new
  claim with `is_current=1` while the prior claim flips to `is_current=0`
  in the same transaction.

All cross-modality queries pivot through this pair: "find all live-tier
copies of every Backup-only permanode" becomes a single SQL JOIN.

## Consequences

- Permanode count grows with **distinct content**; claim count grows
  with **observations**. The two are orthogonal — a single permanode
  with 10 copies across tiers produces 1 permanode row + 10+ claims.
- Renames and re-discoveries are cheap: insert a claim, flip the prior
  one. No data is lost.
- Cross-machine sync (v0.3) only needs to merge permanodes by hash
  + concatenate claim streams. No conflict resolution required.
- Drawback: the model requires a hash before insert, so the import path
  needs to fall back to the legacy sha256 for rows that haven't been
  re-hashed under blake3 yet. `claims.legacy_sha256` is the carry-through.

## Alternatives considered

- **Flat `files` table** (sprawl-audit) — easy to query but conflates
  identity with location; expensive to dedupe; no claim history.
- **Triple-store** — too heavy for v0.1; the use cases don't justify
  a graph DB until cross-machine plan distribution lands.
