# ADR 0011: MCP write surface — destructive hints + audit marker

**Status:** Accepted
**Date:** 2026-05-16

## Context

v0.2.0 shipped a read-only MCP server (8 tools — `inventory_stats`,
`find_permanode_by_path`, `tail_audit_log`, etc.). v0.2.5 added the
write surface (7 more tools — `replicate_execute`,
`archive_snapshot_execute`, `stash_finalize_execute`, etc.) so an
LLM client (Claude Desktop) could drive the same orchestrators the
CLI does.

The hard question: how do we preserve ADR-0002 (operator-in-the-loop
on destructive operations) when the destructive call now comes from
the LLM, not the operator?

Three approaches were considered:

1. **Token-based confirmation.** Each write tool returns a plan_id
   from a paired dry-run call; the execute tool requires that
   plan_id as an argument. The LLM can't race; only the operator
   (who reviews the dry-run output) can hand over the plan_id.

2. **`confirm: true` parameter.** Each destructive tool has an
   explicit `confirm: bool = False` parameter. The LLM has to set
   it to `True` to actually mutate. Trivially bypassable — the LLM
   can pass `True` blindly — but it puts the destructive flag right
   in the tool signature.

3. **MCP `destructiveHint=True` annotation.** Tools declare their
   destructive nature via the protocol-level annotation. The MCP
   client (Claude Desktop, etc.) is responsible for surfacing a
   confirmation UI before the operator allows the call.

## Decision

**Option 3 + a non-bypassable audit marker.**

Specifically:

1. Every write MCP tool is annotated with
   `ToolAnnotations(destructiveHint=True, readOnlyHint=False,
   idempotentHint=False)`. Real MCP clients are expected to surface
   this as a confirmation UI per the MCP spec.

2. Every write handler appends one `mcp_write_invoked` audit row
   (actor = `steward-mcp`) **before** delegating to the orchestrator.
   The orchestrator's own audit chain continues unchanged. Downstream
   queries can distinguish MCP-driven mutations from CLI-driven ones
   by looking for the wrapping marker.

3. The destructive MCP tools delegate to the **same orchestrators**
   the CLI uses (`run_replicate`, `run_archive_snapshot`,
   `finalize_stash`, etc.). Those orchestrators already require
   `--dry-run` or `--execute` semantics structurally — there is no
   separate "execute without confirmation" code path.

## Why not token-based confirmation

The token-based design (option 1) is cleaner in spirit but adds
complexity that doesn't translate to additional safety in practice:

- The operator still has to read the dry-run output before
  approving — the token is just a handoff token, not a review
  enforcement.
- Tokens add state (a `mcp_pending_plans` table or similar) that
  has to be cleaned up on expiry, which is its own correctness
  surface.
- The MCP protocol's `destructiveHint` is the standard idiom; not
  using it cedes that channel to other tools and confuses clients.

## Why not `confirm: true`

The parameter-based approach (option 2) puts the destructive flag
in the wrong place — at the tool signature rather than at the
client UX layer. An LLM can pass `confirm=True` blindly with no
operator awareness. The MCP-protocol-level hint actually engages
the operator because real clients honour it.

## Consequences

**Positive:**

- No new state machinery; reuses the orchestrator chain unchanged.
- Real MCP clients (Claude Desktop) surface confirmation UI for
  destructive tools — the operator stays in the loop where it
  matters (the LLM-to-human handoff).
- The wrapping `mcp_write_invoked` audit row makes every
  MCP-driven mutation traceable. Forensics can answer "did this
  come from the CLI or the LLM?"
- Read tools are correctly annotated `readOnlyHint=True` so the
  LLM client can call them freely without confirmation friction.

**Negative:**

- Trust in the MCP client's UX. A client that ignores
  `destructiveHint` makes the LLM the sole gate. This is mitigated
  by:
  - The orchestrators' own `--dry-run` / `--execute` discipline
    (structural, not configurable, per ADR-0002).
  - The `mcp_write_invoked` audit marker (operator can review
    after the fact).
  - The "dry-run sibling" tools (`replicate_dry_run`,
    `archive_snapshot_dry_run`) — well-behaved LLMs ask for those
    first.
- The destructive hint is a **hint**, not enforcement. We document
  this clearly in the tool docstrings + this ADR.

## Status of the write surface

Seven destructive tools shipped in v0.2.5:

| Tool | Wraps |
|---|---|
| `replicate_execute` | `run_replicate(dry_run=False)` |
| `archive_snapshot_execute` | `run_archive_snapshot(dry_run=False)` |
| `archive_init_execute` | `run_archive_init` |
| `stash_finalize_execute` | `finalize_stash` |
| `stash_restore_execute` | `restore_stash` |

Plus two dry-run siblings (`replicate_dry_run`,
`archive_snapshot_dry_run`) annotated `readOnlyHint=True`.

`steward apply` is **not** exposed as an MCP write tool — its
manifest argument carries hash provenance that ought to be inspected
by hand. Future v0.3+ work may revisit this; for now, the CLI is the
only way to apply a manifest.
