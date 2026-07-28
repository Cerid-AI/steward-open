# ADR 0015: Cloud-FP retire via user-facing mount path

**Status:** Accepted  
**Date:** 2026-07-27  
**Supersedes (partially):** ADR-0014 § execution path only  
**Related:** field notes 2026-07-13 gap #1; ADR-0014

## Context

ADR-0014 introduced `retire_direct` — direct `unlink()` without
same-FS stash — for macOS File Provider (FP) tiers such as
DropboxStorage and iCloud Drive. That decision remains correct:
stash-rename is wrong on FP tiers.

The **path** that receives `unlink()` was left implicit. Operator
practice and early manifests used the **store path**
(`/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/...`), which is
where scans often record claims and where `stat` is reliable.

Field notes from a 2026-07-13 cleanup session observed that deleting
at the store path can free **local** volume space **without**
propagating a delete to the Dropbox cloud (no cloud trash, no quota
reclaim, possible later re-materialization). Deletes via the
**user-facing mount** (`~/Library/CloudStorage/Dropbox/...`) did
propagate.

That observation was under FP congestion; it still load-bears enough
risk that bulk cloud-quota retires must not assume store-path unlink
equals cloud delete.

## Decision

1. **Prefer mount path for Dropbox FP ops; verify == unlink always.**  
   Pure mapping lives in `steward.core.fp_paths`. `retire_direct`
   resolves the claim path into **one** operational path:
   - cloud-propagating mode: **mount** for both verify and unlink
   - local reclaim (`--allow-store-path-unlink`): **claim path** for both

   **Never** hash-check path A and delete path B. Experiment 2026-07-28
   showed store and mount can be forked materializations; split
   verify/unlink is a correctness bug.

2. **Claim identity stays on the recorded path.**  
   `claims.is_current` is flipped for the claim path **and** known
   store/mount aliases so either scan form is covered.

3. **Opt-out for local-only reclaim.**  
   `steward apply --allow-store-path-unlink` restores ADR-0014’s
   “unlink the claim path as written” behaviour when the operator
   intentionally frees only local materialization (no cloud-quota
   guarantee). Audit payload records
   `used_mount_for_unlink: false`.

4. **Missing mount → refuse (do not fall back silently).**  
   If mount-prefer is on and the mount path does not exist, raise
   `ManifestError` with guidance to re-scan the CloudStorage mount
   or pass `--allow-store-path-unlink`. Silent store-path fallback
   would reintroduce gap #1. Inventory on this host is ~100% store
   paths (0 mount claims as of 2026-07-28) — cloud retires need a
   mount rescan first.

5. **iCloud Drive** keeps mount-as-both for now (store layout is
   opaque). Same prefer-mount default.

6. **Timeouts** continue to surface as `FPUnavailableError` (v0.3.11)
   so congested mount deletes defer the row rather than abort the
   batch.

## Consequences

**Positive**

- Cloud-quota retires align with how the FP treats “user delete.”
- External cooling-off (cloud trash / version history) is actually
  reachable.
- Store-path verify keeps bulk `--skip-verify` existence checks
  grounded when the mount is flaky — with the explicit refuse path
  when mount is required for unlink.

**Negative**

- Mount deletes can still `Errno 60` under congestion (handled).
- Operators with only store-path tooling must set
  `--allow-store-path-unlink` and accept cloud divergence risk.
- Path mapping is Dropbox-first; other FPs need explicit prefixes
  as they appear in production.

## Alternatives rejected

- **Provider API only** — correct long-term for some vendors; heavier
  auth surface; mount-path delete is the least new surface that
  matches observed FP behaviour.
- **Always store path** — fails gap #1 for quota reclaim.
- **Silent fallback store → mount** — hides incorrect cloud semantics.

## Implementation notes

- Unit tests: pure mapping in `tests/unit/core/test_fp_paths.py`.
- Integration: monkeypatched HOME + fake store/mount trees in
  `tests/integration/test_retire_direct.py`.
- Docs: QUICKSTART + field notes resolution status; OPEN_DEVELOPMENT
  Track 1b closed for Dropbox path policy (experiment still
  recommended on settled FP for operator confidence).
