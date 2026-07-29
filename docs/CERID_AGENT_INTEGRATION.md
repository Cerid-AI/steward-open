# Cerid agent integration with Steward

How external Cerid agents (Claude Code / Grok, boardroom, trading-agent,
or allowlisted MCP clients) call Steward for filesystem stewardship.

See **ADR-0016** for capability modes and gated `apply_execute`.

## Phase 0 — Discoverability

### Project MCP (this repo)

`.mcp.json` registers Steward stdio:

```json
{
  "mcpServers": {
    "steward": {
      "command": "steward",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

### Dual-stack with cerid-kb (session that needs both)

```json
{
  "mcpServers": {
    "cerid-kb": {
      "type": "sse",
      "url": "http://localhost:8888/mcp/sse"
    },
    "steward": {
      "command": "steward",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

### Env for agents

| Variable | Default | Meaning |
|---|---|---|
| `STEWARD_MCP_MODE` | `plan` | `read` \| `plan` \| `write` |
| `STEWARD_MCP_ACTOR` | `steward-mcp` | Audit actor (use `steward-mcp:<client-id>`) |
| `STEWARD_MCP_MAX_FILES_CAP` | `50` | Hard max for `apply_execute` |
| `STEWARD_DATA_DIR` / `STEWARD_DB_PATH` | platform default | Inventory location |

Cerid product agents should default to **`plan`**. Enable **`write`** only
on trusted host automation with explicit operator config.

### Cerid runtime as MCP client

If the Cerid MCP process should call Steward tools:

1. Register Steward as an external MCP server (stdio or `http://127.0.0.1:8765/mcp`).
2. `MCP_CLIENT_MODE=allowlist` + allowlist name for Steward.
3. Prefer `plan` mode unless a governed path sets `write`.

## Recommended agent workflow (FS retire)

1. `fp_status` / `status` — health  
2. `policy_plan` — write plan TSV  
3. `apply_dry_run(manifest, require_fp_healthy=true)` — obtain `plan_token`  
4. **Human or boardroom approval** of dry-run summary  
5. Only if authorized: set `STEWARD_MCP_MODE=write` and  
   `apply_execute(manifest, plan_token, max_files=N, require_fp_healthy=true)`  

Never bulk-execute unfiltered 100k+ plans via MCP.

## Tool map (v0.3.22+)

| Class | Tools |
|---|---|
| Read | `mcp_capability`, `inventory_stats`, `status`, `scan_status`, `find_*`, `get_permanode`, `inspect_target`, `list_*`, `fp_status`, `tail_audit_log`, … |
| Plan | `policy_plan`, `apply_dry_run`, `replicate_dry_run`, `archive_snapshot_dry_run` |
| Write | `apply_execute`, `replicate_execute`, `archive_*_execute`, `stash_*_execute` |

## Safety invariants

- Bundled Claude sub-agents (ADR-0012) stay **read-side** in their prompts.  
- Default MCP mode is **plan**, not write (pinned in project `.mcp.json` env).  
- `apply_execute` is one-shot plan_token + max_files + FP gate.  
- Host-local inventory + mounts; not a remote multi-tenant FS API.

## Long-running scan

**`steward scan` is CLI-only** (multi-hour trees, caffeinate, workers=1). MCP
exposes `scan_status` for progress. Agents should not block an MCP session on
a full-tree scan; start scan via CLI/shell and poll `scan_status`.
