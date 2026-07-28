# ADR 0004: YAML policy with Python evaluator

**Status:** Accepted
**Date:** 2026-05-16

## Context

Promotion, retention, dedup-retire, and classification rules all need to
be operator-authorable without recompiling Steward. CUE and Rego both
solve this problem at heavier orgs; for a single-operator v0.1 they're
overkill. The sprawl-audit precedent (`PHASES = {...}` literal in
`promote_execute.py`) proved that the rule data wants to leave Python.

## Decision

Policies are YAML files (`*.yml`):

- Bundled defaults ship under `src/steward/policies/`
- Operator overrides live under `~/.config/steward/policies.d/`
- The schema lives in `core/policy/schema.py` as pydantic models with
  `extra = "forbid"` — typos in keys fail loud.
- The loader (`core/policy/loader.py`) dispatches on `kind`:
  `RetentionPolicy | PromotionPolicy | ClassificationPolicy`.
- `steward policy lint <file>` validates a policy without applying it.
- The evaluator + reconciler (in `core/policy/`) are hand-written Python
  that walks validated policy instances and produces plan manifests.

## Consequences

- Operators read + edit YAML, not Python.
- Schema evolution is straightforward — new fields default sensibly,
  old fields stay readable.
- Authoring complexity is bounded by what pydantic can express; very
  rich rules (cross-references, computed defaults) push toward a real
  policy language.
- v0.2+ may add CUE if the user count or cross-machine policy
  distribution justifies it. v0.1 doesn't.

## Alternatives considered

- **CUE / Rego** — strict validation + computation, but extra runtime
  dep + new mental model for a one-operator product.
- **TOML / JSON Schema** — TOML's poor for nested lists; JSON Schema
  loses the operator-friendly authoring.
- **Python literals** (status quo from sprawl-audit) — code reuse but
  forces re-deploy for every rule change.
