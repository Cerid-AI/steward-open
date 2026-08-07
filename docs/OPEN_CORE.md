# Open-core split plan

**Status:** Phase 1 hardened (export factory + automated re-sync, 2026-07-29)  
**License:** Apache-2.0  
**Private source of truth:** `Cerid-AI/steward`  
**Public extract:** https://github.com/Cerid-AI/steward-open  
**PyPI target name:** **`steward-fs`** (CLI remains `steward`)

---

## Goal

Publish a **portable** open-core that covers the stewardship contract
(permanode/claim inventory, plan/apply, audit, ADRs, MCP agents), while
keeping operator-lab adapters and dogfood process private.

```
PUBLIC  steward-open / steward-fs          PRIVATE  Cerid-AI/steward
─────────────────────────────────          ──────────────────────────
core/ model, hashing, policy, ADRs         field notes (host paths)
infra/ db, scanner, retire, stash, MCP     photos (osxphotos)
cli/ core verbs + soft-optional adapters   schedule / launchd lab cadences
unit + preservation tests                  full integration suite
CONTRIBUTING: private is source of truth   inventory.db never in git
```

---

## Product identity (frozen for Phase 1)

| Surface | Name | Notes |
|---|---|---|
| Private git | `Cerid-AI/steward` | Sole source of truth until Phase 2 invert |
| Public git | `Cerid-AI/steward-open` | Generated extract only |
| PyPI (planned) | **`steward-fs`** | Avoid name collision; CLI entry point still `steward` |
| Import package | `steward` | Unchanged |

Do not introduce a third public name without updating this table.

---

## What stays private vs public

### Private only

- `docs/field-notes-*` (host paths, rectification run state)
- `HANDOFF.md`, operator run artifacts, `inventory.db`
- `infra/photos`, `infra/schedule`, `launchd/`
- Multi-GB operational runbooks that encode *this host’s* Dropbox layout
- Cerid-internal deploy secrets, dual-stack MCP secrets for product agents

### Public (extract allowlist)

- `steward.core` + portable infra (db, migrations, scanner, retire, MCP, …)
- ADRs (including ADR-0016 agent apply gates)
- QUICKSTART, ROADMAP, OPEN_CORE, CERID_AGENT_INTEGRATION (generic wiring)
- Unit + preservation tests; public CI
- `.mcp.json` with safe defaults (`STEWARD_MCP_MODE=plan`)

### Design rules (both trees)

1. No Cerid runtime dependency in core.  
2. `steward.core` must not import infra/cli (import-linter).  
3. Operator-in-the-loop on destructive apply (ADR-0002).  
4. MCP default capability mode is `plan` (ADR-0016).  
5. macOS File Provider behaviour is documented, not guaranteed on Linux.  
6. Inventory data never committed.  
7. Lab rectification process stays private; portable FP *rules* stay public.

### Multi-machine / fleet matrix (PyPI `steward-fs` readiness)

Cross-machine inventory remains **pull-don't-push** (ADR-0013): operators
move tar.xz envelopes out-of-band; Steward attaches them read-only.
The fleet health matrix (ADR-0021) is designed to ship in open-core:

| Layer | Public? | Notes |
|---|---|---|
| Pure SLA types + fail-on evaluation | Yes (`steward.core`) | No host secrets, no private paths |
| `collect_fleet_health` + ATTACH SQL | Yes (`steward.infra`) | Temp-DB tests on Linux CI |
| `steward machines health` CLI / MCP | Yes | Read-only; no daemon |
| launchd weekly-export template | Optional / private lab | Matrix only *reads* export audit / `exports/` |

No Cerid host paths or field-notes layout may land in matrix code or
public snapshot payloads. Envelope transport is operator-owned until a
separate sync-transport ADR (not required for PyPI readiness).

---

## Phase plan

### Phase 0 — boundaries ✅

- Core purity + this document.

### Phase 1 — public extract + factory ✅ / hardening ✅

| Capability | How |
|---|---|
| Stage extract | `scripts/export-open-core.sh --stage` |
| Self-test | `scripts/export-open-core.sh --stage --verify` |
| Private CI | `.github/workflows/open-core-export.yml` |
| Publish to open | Tag `v*` or workflow `Open-core publish` + secret `OPEN_CORE_DEPLOY_TOKEN` |
| Local publish | `OPEN_CORE_PUSH=1 scripts/sync-steward-open.sh` after verify |

### Phase 2 — private overlay (not started)

Private monorepo **depends on** published `steward-fs` (or git tag of open)
and adds only photos / launchd / lab docs.

**Start Phase 2 only when all are true:**

1. External consumer or real contribution load exists.  
2. Public CLI flags + manifest TSV columns are stable enough for minor/patch semver.  
3. Private-only code is a thin adapter surface (&lt; ~15% of tree) with clean entry points.  
4. `main.py` no longer soft-imports private modules as first-class.  
5. Export self-test has been green on private CI for ≥2 release cycles.  
6. PyPI package `steward-fs` is published and installable.

### Phase 3 — API freeze → v1.0

- Freeze manifest columns, ADR invariants, core CLI flags.  
- Breaking public CLI changes require major bump.

---

## Non-goals (public)

- Operator multi-TB inventory topology or NAS secrets.  
- Guaranteeing Dropbox FP behaviour on Linux.  
- Auto-delete on remote NAS without operator export.  
- Publishing host rectification run status as product API.  
- Developing features primarily on steward-open.

---

## Maintainer commands

```bash
# Verify extract locally (mirrors private CI job)
scripts/export-open-core.sh --stage --verify

# Publish to steward-open (after verify)
OPEN_CORE_PUSH=1 scripts/sync-steward-open.sh

# Or: git tag v0.3.23 && git push origin v0.3.23
# (requires OPEN_CORE_DEPLOY_TOKEN secret on private repo)
```

### Secret setup (one-time)

1. Create a fine-grained PAT with **Contents: Read and write** on `Cerid-AI/steward-open`.  
2. Add to private repo secrets as **`OPEN_CORE_DEPLOY_TOKEN`**.  
3. Confirm `Open-core publish` workflow can push.

---

## Sync hygiene

- Allowlist is explicit; missing allowlist paths **fail** the export.  
- Forbidden globs (field-notes, photos, schedule, pycache) **fail** the export.  
- Docs-only path scrub; tests/src keep `/Users/operator shapes for tier rules.  
- No AI attribution in commits (global commit policy).  
- Public CONTRIBUTING states private is source of truth.

---

## Decision log

| Date | Decision |
|---|---|
| 2026-07-27 | Operator approved open-core direction |
| 2026-07-27 | Core purity + ADRs are the public contract |
| 2026-07-28 | Phase 1 stage/tarball + PUBLIC_README; public repo created |
| 2026-07-29 | Re-sync v0.3.22; ADR-0016; CERID agent docs; extract factory hardened |
| 2026-07-29 | Private sole source of truth until Phase 2 invert; PyPI name target `steward-fs` |
| 2026-07-29 | Automated export verify CI + tag-driven publish workflow |
