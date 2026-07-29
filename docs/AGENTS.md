# Steward sub-agents

Four Claude Code sub-agents ship with Steward under
[`.claude/agents/`](../.claude/agents/). They bundle steward-specific
expertise so an operator driving Steward through Claude Code (or any
agent-aware client) can spawn a focused task without re-explaining
the system every time.

| Agent | When to use | What it does | What it never does |
|---|---|---|---|
| **tier-auditor** | "How is the inventory doing?" / proactive health checks | Sweeps every v0.2.x adapter via read-only commands; flags anomalies | Mutate any state |
| **promotion-planner** | "Plan a Backup → live tier promotion" | Reads `promotion.yml`, generates the plan TSV, dry-runs the apply, recommends an `--execute` batch size | Run `apply --execute` itself |
| **retire-decider** | "Reclaim duplicate space safely" | Generates the retention plan, distinguishes stash vs `retire_direct` (Dropbox), runs `fp status` when needed, spot-checks canonical safety | Delete; finalize stash; bypass cooling-off; bulk Dropbox path rewrite |
| **verifier** | "Is my inventory intact?" / pre/post-major-op | Runs the verification gauntlet (audit chain + SQLite integrity + content spot-check + stash verify) | Recover; mutate; recommend `db migrate` as a fix |

## How the agents stay aligned with Steward

Each agent is just a markdown file with YAML frontmatter — same shape
as a Claude Code project agent. The body is a system prompt with:

1. **Scope (IS / IS NOT)** — explicit boundaries.
2. **A checklist** — ordered commands the agent runs.
3. **Anomalies to flag** — what counts as "something to surface."
4. **Output format** — a markdown skeleton the agent fills in.
5. **Constraints** — guardrails (per ADR-0002, no `--execute`; per
   ADR-0009, no pushing inventory to other machines).

The agents never embed CLI examples that contradict the README /
QUICKSTART; they use the documented surface. If the CLI surface
changes, the agents should be re-reviewed — the
[`tests/test_docs_consistency.py`](../tests/test_docs_consistency.py)
gate catches missing subcommand documentation but not stale agent
checklists. That's a manual review step.

## Invoking from Claude Code

```
Task(subagent_type="tier-auditor",
     description="health sweep",
     prompt="Run a full audit on the local inventory and report.")
```

```
Task(subagent_type="promotion-planner",
     description="plan documents promotion",
     prompt="Plan the documents-validation phase and stop before --execute.")
```

```
Task(subagent_type="retire-decider",
     description="dedup retire plan",
     prompt="Plan a dedup retire under retention.yml. Spot-check safety on the 5 largest rows.")
```

```
Task(subagent_type="verifier",
     description="verify inventory",
     prompt="Verify the inventory. Stop at the first critical failure.")
```

## How agents fit the v0.2.x adapter wave

Each agent uses the read surface only:

- `tier-auditor` reads `status` (prefer `--quick`), `db verify`,
  `db integrity`, `fp status`, `stash list`, `machines list`,
  `schedule list`.
- `promotion-planner` reads `policy show`, `policy plan`, `apply --dry-run`.
  Hands off the `--execute` step to the operator.
- `retire-decider` reads `policy plan`, `inspect`, `apply --dry-run`,
  and `fp status` when Dropbox rows appear. Hands off `apply --execute`
  and `stash finalize` (stash rows only) to the operator.
- `verifier` reads `db verify`, `db integrity`, `db verify --content-spot-check`,
  `stash verify`, `status --json`.

MCP write/execute tools (ADR-0011 + ADR-0016) exist for **external**
operator-confirmed flows under `STEWARD_MCP_MODE=write` and (for apply)
a one-shot `plan_token`. **Bundled** agents stay on the read/plan side
because they're spawned autonomously inside other tasks and must not
surprise the operator (ADR-0012).

### Cerid / external agents

See [`CERID_AGENT_INTEGRATION.md`](CERID_AGENT_INTEGRATION.md). Project
`.mcp.json` registers Steward stdio. Default mode is `plan` (query +
dry-run). Enable `write` only with explicit host configuration and
bounded `max_files` on `apply_execute`.

## Adding new agents

When `.claude/agents/<name>.md` lands, the validator test in
`tests/test_agents_consistency.py` asserts:

- The file has the required YAML frontmatter (`name`, `description`).
- The `name` matches the filename.
- The body is non-trivial (≥ 500 chars).
- The tools list (if present) only references real Claude Code tools.

This catches the most-common agent-authoring mistakes at CI time.
