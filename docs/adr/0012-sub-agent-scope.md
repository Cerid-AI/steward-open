# ADR 0012: Sub-agent scope — read-side, never `--execute`

**Status:** Accepted
**Date:** 2026-05-16

## Context

v0.2.11 bundled four Claude Code sub-agents under `.claude/agents/`:
`tier-auditor`, `promotion-planner`, `retire-decider`, `verifier`.
Each is a markdown file with a YAML frontmatter + system prompt that
makes Claude usefully steward-aware for one task.

The agents have access to the full `Bash` tool, which means they
could in principle call `steward apply --execute`,
`steward stash finalize`, `steward archive init --execute`, etc.
The MCP write surface (v0.2.5; ADR-0011) gives them another path
to the same mutations through `replicate_execute` /
`archive_snapshot_execute` / `stash_finalize_execute`.

The question: **should the bundled agents ever invoke a write
operation themselves?**

## Decision

**No. Bundled sub-agents are read-side-by-default. They never
invoke `--execute`, `stash finalize`, `archive init`, or any MCP
destructive tool. They propose; the operator executes.**

Each agent's system prompt contains an explicit "What this agent
IS NOT" section listing the forbidden operations. The
`promotion-planner` and `retire-decider` agents explicitly hand
off the `--execute` step to the operator with the literal command
to run, then stop.

## Why

Three reasons:

1. **Agents are invoked from inside other tasks.** The operator
   may have spawned the parent task ("audit my inventory and
   tell me what's wrong") without anticipating that a downstream
   agent could decide to act. ADR-0002 makes the human a structural
   gate; bundled agents must not undermine that.

2. **Agents run with full Bash.** Unlike the MCP server where
   `destructiveHint` lets the client surface a confirmation UI,
   `Bash` invocations are direct. There is no protocol-level
   confirmation layer between a `Task(subagent_type=...)` call and
   a shell command.

3. **Operators trust the documented scope.** If the docs say
   "tier-auditor is read-only," the operator should be able to
   spawn it freely without re-reading the system prompt. Drift
   between documented scope and actual capability is a leak.

## How we enforce it

This is policy, not code-level enforcement. The validator test
in `tests/test_agents_consistency.py` checks structural shape
(frontmatter, body length, tools list) but doesn't grep for
forbidden commands — that would be brittle.

Instead:

1. Every agent's system prompt has a prominent "What this agent
   IS NOT" section listing the forbidden operations.
2. `docs/AGENTS.md` carries a table with a "What it never does"
   column that mirrors the IS NOT sections.
3. The hand-off pattern in the planner agents is documented and
   the agent emits the exact `--execute` command for the operator
   to run.

Future v0.3+ may add a `--max-agent-capability` flag or similar to
strip dangerous tools from sub-agent invocations. For v0.2.x, the
policy is enforced by documentation + the agent author's discipline.

## What this means for new bundled agents

If a future bundled agent wants write capability, it requires:

1. A separate ADR explaining the structural safety mechanism
   (e.g. token-based gating, explicit operator-supplied scoped
   credential).
2. The agent's "What this agent IS / IS NOT" sections updated
   accordingly.
3. The validator test extended to recognise the new pattern.

## Consequences

**Positive:**

- The operator can freely spawn any bundled agent without losing
  the ADR-0002 guarantee.
- Authoring discipline is concentrated in one place (the system
  prompt). Reviewing a new agent means reading one markdown file.
- The four bundled agents are exemplars; new ones can model on
  them.

**Negative:**

- Operators who want a "just do it" workflow have to chain agent
  output + manual execution. The planner agents emit the literal
  command to copy-paste, which softens this.
- Hand-off ergonomics depend on the operator reading the agent's
  recommendation. A distracted operator who skims the
  "recommended next step" and runs blindly is back to where they
  started with raw `steward apply --execute`. That's outside the
  ADR's scope — it's an operator-side responsibility.
