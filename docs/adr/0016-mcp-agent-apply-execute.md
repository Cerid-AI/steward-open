# ADR 0016: MCP agent capability modes + gated apply_execute

**Status:** Accepted  
**Date:** 2026-07-29

## Context

External Cerid agents (IDE MCP clients, product agents, allowlisted
external MCP callers) need a first-class path to Steward for
filesystem stewardship: inventory query, plan generation, dry-run,
and—when explicitly authorized—bounded execute.

ADR-0011 exposed transport write tools (replicate / archive / stash)
with `destructiveHint=True` but **deliberately omitted**
`apply --execute` so bulk manifest mutations stayed CLI-only (hash
provenance + operator review).

Field use (Dropbox rectification, multi-GB inventory) showed agents
already dry-run and plan successfully via MCP, but:

1. There was no structural mode separating **read / plan / write** for
   automated callers that ignore confirmation UIs.
2. `apply_execute` remained unavailable, so agents fell back to Bash
   (ADR-0012 forbids that for bundled sub-agents; external agents vary).
3. Audit rows always used actor `steward-mcp` with no Cerid client id.

## Decision

### 1. Capability modes (`STEWARD_MCP_MODE`)

| Mode | Allowed |
|---|---|
| `read` | Query tools only |
| `plan` (default) | + `policy_plan`, `apply_dry_run`, transport dry-runs |
| `write` | + all `*_execute` tools including `apply_execute` |

Default **`plan`** is safe for Cerid IDE and product agents: they can
inspect and plan without ambient execute rights.

### 2. Actor identity (`STEWARD_MCP_ACTOR`)

Audit `mcp_write_invoked` uses `STEWARD_MCP_ACTOR` when set (e.g.
`steward-mcp:boardroom-agent`), else `steward-mcp`.

### 3. Gated `apply_execute` (MCP)

`apply_execute` is allowed only when **all** of:

1. `STEWARD_MCP_MODE=write`
2. A one-shot **`plan_token`** issued by a successful `apply_dry_run`
   for the **same manifest path + content digest** (TTL 2h, consume-on-use)
3. **`max_files` is required** and ≤ `STEWARD_MCP_MAX_FILES_CAP` (default 50)
4. **`require_fp_healthy` defaults True** on execute (opt-out explicit)
5. `destructiveHint=True` on the tool (client confirmation UX)

Dry-run with row errors does **not** issue a plan_token.

### 4. Plan token store

Tokens are files under `<data_dir>/runs/mcp-plan-tokens/`, not inventory
DB rows (avoid lock contention with long scans).

### 5. Explicitly not decided here

- Remote non-loopback auth (API keys / mTLS) — still loopback-trust model
- Boardroom approval tiers — product layer; can wrap plan_token issuance
- Token-based confirmation for replicate/archive (unchanged from ADR-0011)

## Consequences

**Positive**

- Cerid agents get a complete plan → bounded execute loop without Bash.
- Default mode blocks ambient execute.
- Manifest content binding prevents stale or swapped plan files.
- Cap prevents bulk 221k-class executes via MCP by default.

**Negative / residual risk**

- A local process with `STEWARD_MCP_MODE=write` and network access to
  loopback MCP still has power — host trust boundary unchanged.
- Clients that ignore `destructiveHint` still rely on mode + token.
- Operators must set `write` deliberately for automation hosts.

## Related

- ADR-0002 operator-in-the-loop  
- ADR-0011 MCP write surface (destructiveHint + audit marker)  
- ADR-0012 sub-agent scope (bundled agents remain read-side)  
- ADR-0015 cloud-FP path policy  
- `docs/CERID_AGENT_INTEGRATION.md`
