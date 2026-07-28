# ADR 0002: Operator-in-the-loop on destructive operations

**Status:** Accepted
**Date:** 2026-05-16

## Context

The sprawl-audit work consolidated ~13 TiB across 8 tiers without losing
a byte, but the cost of a single mistake — a wrong-tier `rm`, a stale
manifest, a cross-FS rename — is the destruction of years of data.
Automation without operator confirmation has been responsible for
every catastrophic data loss in the project's history.

## Decision

Every Steward mutation requires an explicit operator decision, structural,
not configurable:

1. `steward apply` invoked without `--dry-run` or `--execute` exits 2.
   The mutually-exclusive flag is the operator's go/no-go signal.
2. `--execute` actions write to `_cooling-off-stash/<manifest_run_id>/`
   first (same-FS rename). Real `unlink` only runs at `steward stash
   finalize` after the policy-defined window (default 7 days).
3. Cross-FS moves are refused by `infra/stash.py::same_fs_rename_to_stash`.
4. Every mutation appends an audit row in the same transaction as the
   data write.

The contract is enforced in code, not in documentation:
- `apply_manifest(dry_run: bool)` has no default — the caller MUST pass
  a boolean.
- `same_fs_rename_to_stash` raises `ManifestError` on missing source,
  existing destination, or cross-FS device mismatch.

## Consequences

- Operator latency is mandatory. Steward cannot run "fully autonomous"
  in v0.1. By design.
- A bad plan still has a 7-day window to be caught and reversed via
  `steward stash restore`.
- Throughput is throttled by operator review; that's the point.
- The audit log + cooling-off stash combination gives forensic
  reversibility: every byte deleted can be reconstructed from the
  audit history up to the cooling-off horizon.

## Alternatives considered

- **Confirmation prompts at runtime** — too easy to ignore; doesn't
  survive non-interactive contexts (cron, CI).
- **`--force` flag** — defeats the safety; the absence-of-flag-is-deny
  default is stronger.
