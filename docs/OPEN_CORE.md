# Open-core split plan

**Status:** Phase 1 public extract live (re-sync 2026-07-29 → v0.3.22)  
**License:** Apache-2.0 (already)  
**Current repo:** private `Cerid-AI/steward` (family sibling of Cerid AI)

## Goal

Publish a **public** open-core package that covers the portable stewardship
core, while keeping operator-specific / macOS-heavy adapters and private
dogfooding surfaces in the private monorepo (or a private overlay).

```
PUBLIC  steward-open / steward-fs       PRIVATE  Cerid-AI/steward (overlay)
─────────────────────────────           ─────────────────────────────────
core/ model, hashing, policy            infra/ photos (osxphotos)
core/ fp_paths, tiers, audit primitives infra/ schedule (launchd) optional
infra/ db schema + repos (SQLite)       launchd plists (lab cadences)
infra/ scanner, retire, stash, …        field notes with host paths
cli/ (core verbs)                       private operator manifests
policies + ADRs                         inventory.db (never in git)
preservation + unit tests
```

## Phase plan

### Phase 0 — boundaries ✅

- `steward.core` import-linter pure (enforced).
- This document + no Cerid runtime dependency.

### Phase 1 — public extract package (in progress)

| Step | Status |
|---|---|
| `scripts/export-open-core.sh --stage --tarball` | ✅ |
| Path allowlist + host-path scrub in stage | ✅ |
| `docs/open-core/PUBLIC_README.md` | ✅ |
| Public GitHub repo + first push | ✅ https://github.com/Cerid-AI/steward-open (public) |
| Linux-first public CI | ✅ `.github/workflows/ci.yml` in open extract |
| PyPI name (`steward-fs` / `cerid-steward`) | ⏳ name check + publish |

```bash
# Re-sync from private monorepo → public:
scripts/export-open-core.sh --stage
# Then replace contents of a checkout of Cerid-AI/steward-open and push.
```

**Public name:** [`Cerid-AI/steward-open`](https://github.com/Cerid-AI/steward-open)

### Phase 2 — private overlay

- Private repo depends on published package (or git submodule) and adds:
  - osxphotos, lab launchd defaults, Dropbox volume conventions
- Dogfood inventory stays local (never in git).

### Phase 3 — API freeze → v1.0

- Freeze manifest TSV columns, ADR invariants, CLI flags for core verbs.
- Semver: breaking public CLI changes require major bump.

## Non-goals (public)

- Operator 8 GiB inventory or NAS topology.
- Guaranteeing Dropbox FP behaviour on Linux (document macOS-only).
- Auto-delete on remote NAS without operator export (`nas_manifest`).
- Dropbox store/mount **rectification** (private lab workstream).

## Sync hygiene

- Scrub absolute home paths from public docs/tests (script does a pass).
- No AI attribution in commits (global commit policy).
- Detect-secrets + bandit remain on both sides.

## Decision log

| Date | Decision |
|---|---|
| 2026-07-27 | Operator approved open-core direction |
| 2026-07-27 | Core purity + ADRs are the public contract |
| 2026-07-28 | Phase 1 stage/tarball + PUBLIC_README landed in private repo |
| 2026-07-28 | Public repo `Cerid-AI/steward-open` created + initial extract pushed |
| 2026-07-29 | Re-sync extract to v0.3.22 (ADR-0016 MCP modes + plan_token apply_execute); public allowlist adds `CERID_AGENT_INTEGRATION.md` + `.mcp.json` |
