# ADR 0005: Hash ladder — xxh3-128 fast / blake3 archive

**Status:** Accepted
**Date:** 2026-05-16

## Context

The scanner needs to hash millions of files across multiple tiers. The
legacy sprawl-audit walker used SHA-256, which on a 2.7 GHz Intel iMac
maxes out around 350 MB/s — fast enough for a one-shot audit, too slow
for ongoing stewardship over multi-TiB roots.

## Decision

Two-grade ladder per file:

1. **Fast pass: xxh3-128** — every scanned file gets `xxhash.xxh3_128`
   over 8 MiB chunks. ~6 GB/s on M2; bottleneck shifts to disk I/O.
2. **Archive grade: blake3** — also computed when either:
   - file size ≥ 100 MiB (default), OR
   - the fast hash collides with a known permanode (suspected dup
     pending confirmation).

The permanode's `canonical_hash` is blake3 when promoted, xxh3-128
otherwise. `hashes` table carries every algorithm version computed
for a permanode, so a later promote pass can find candidates without
re-walking the filesystem.

**sha256 remains preserved** in `claims.legacy_sha256` for rows
imported from `unified-hash.db`. The compatibility column lets
sprawl-audit-era queries still resolve.

## Consequences

- xxh3-128 is not cryptographically secure. For Steward's threat model
  — accidental data corruption, not adversarial collisions — that's
  acceptable. Blake3 promotion catches the long-tail collision risk.
- The `should_promote` policy hook is the only place size + suspected
  duplication intersect. Tweaking the threshold is a single-constant
  change.
- Hashes for the same permanode at different algos are co-located in
  the `hashes` table: queries like "find me a blake3 for this xxh3"
  are a simple INNER JOIN.

## Alternatives considered

- **sha256 only** — simpler, slower, and offers nothing v0.1 needs.
- **blake3 only** — fast enough but ~2× xxh3 cost without a
  cryptographic upside for the common case.
- **xxh64 fast** — half the bits; collision probability rises
  unacceptably at 10^7 unique contents.
