# ADR 0009: Pull-don't-push for inventory

**Status:** Accepted
**Date:** 2026-05-16

## Context

v0.2 introduces an fsevents-driven watcher; v0.3 introduces multi-
machine plan distribution. Both raise the question of how mutations
flow between machines. A push model — machine A sends "delete this"
to machine B — exposes machine B to bad state from machine A's
mistakes, malware, or compromise.

## Decision

The watcher (v0.2) and the cross-machine sync (v0.3) are **pull-only**:

- Machine A produces a **plan manifest** in its local inventory.
- Machine B fetches the manifest, validates it against its own
  inventory state, and applies it under its own `steward apply`
  contract (including the operator review window).
- Machine B never sees an unsolicited mutation request from A.

The watcher cannot bypass `steward apply` — it produces plans, not
state changes. An fsevents trigger may auto-generate a plan, but the
plan still requires `--dry-run` / `--execute` to land.

## Consequences

- A poisoned inventory on machine A cannot silently mutate machine B.
- Cross-machine throughput is bounded by operator approval per
  machine. That's acceptable — the multi-machine use case is durability
  + local discovery, not high-throughput coordination.
- The plan manifest TSV becomes the inter-machine contract. Its schema
  (carried by `core/manifest_io.py`) needs to stay stable across the
  v0.2 → v0.3 transition.

## Alternatives considered

- **CRDT-based replicated state** — much heavier, doesn't solve the
  "I want operator review" requirement.
- **Push with signature verification** — same review requirement; the
  signature is orthogonal.
