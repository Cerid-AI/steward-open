# ADR 0007: Cooling-off stash pattern

**Status:** Accepted
**Date:** 2026-05-16

## Context

Every destructive operation needs a window for the operator to detect
"wait, that wasn't right" and reverse it. Two-phase commit + manifest
review (ADR-0002) covers the "before" side; the cooling-off stash covers
the "after" side — after a `--execute` runs, the bytes are still on disk
in a reversible form for a policy-defined window.

## Decision

Retire-from-live-tier == same-FS `rename` into
`<tier>/_cooling-off-stash/<manifest_run_id>/...`.

- The rename is atomic on the filesystem level (`os.rename` on a single
  device).
- The destination's parent directory is created if missing.
- Cross-FS renames are refused (`infra/stash.py::same_fs_rename_to_stash`
  compares `st_dev`).
- Every rename appends a `stash_committed` audit row; the original
  source path is in `payload_json` for `steward stash restore`.
- Real `unlink` only runs at `steward stash finalize --run-id <id>`
  after the policy-defined `cooling_off_days` (default 7).

For read-only NAS tiers (`Backup`), the same logic emits a manifest
file consumed by DSM Task Scheduler or SSH-with-sudo on the NAS side;
Steward never writes the NAS directly.

## Consequences

- A wrong destructive `--execute` is reversible for `cooling_off_days`.
- The stash directories accumulate until `finalize` runs. Operators
  must remember to finalize, or the disks fill.
- Same-FS constraint excludes "move from L2 to L1 stash" — that's
  promotion, not stash.
- Audit chain ties the rename to its manifest, so a forensic walk can
  reconstruct the original layout from any `stash_committed` row.

## Alternatives considered

- **Immediate `unlink` with backup** — slower, more complex, defeats
  the "reversible at the filesystem level" guarantee.
- **Trash directory** — depends on OS conventions; doesn't carry
  manifest provenance.
- **macOS Time Machine reliance** — out-of-band, no audit integration.
