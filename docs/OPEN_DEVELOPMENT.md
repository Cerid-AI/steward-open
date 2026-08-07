# Steward — Open Development Areas

**Updated:** 2026-08-07  
**Package:** `0.3.24` (ADR-0022 inventory matrix + Surface)  
**Remote:** `Cerid-AI/steward`  
**Live inventory:** `~/Library/Application Support/steward/inventory.db` (~9 GiB after store rescan)

This is the **authoritative** open-work doc. Supersedes older “next after v0.3.10” prose that still appears in historical sections of older notes.

---

## Current state (shipped on origin/main)

| Arc | Status |
|---|---|
| v0.1 core (plan/apply, stash, audit, policies) | ✅ |
| v0.2 adapters (watch, photos, rclone, restic, MCP, dashboard, schedule, agents, stats) | ✅ |
| ADR-0013 cross-machine wire | ✅ (v0.3.0–0.3.6) |
| ADR-0014 `retire_direct` | ✅ (v0.3.7–0.3.10) |
| FP timeout deferral | ✅ (v0.3.11) |
| ADR-0015 mount-prefer + **verify==unlink law** | ✅ (v0.3.13–0.3.14) |
| Reconciler emits `retire_direct` for DropboxStorage | ✅ (v0.3.14) |
| `nas_manifest` export (no silent skip) | ✅ (v0.3.13) |
| Open-core plan + export script | ✅ docs/OPEN_CORE.md, scripts/export-open-core.sh |
| Dropbox rectification **research** (history + domain + name split) | ✅ v0.3.18 + field notes |
| Dropbox **systemic validation** (external-drive layout, tier mount, health verdict) | ✅ v0.3.19 |
| Scanner mid-walk commits + rectification runbook | ✅ v0.3.20 |
| Serial scan_run commit-at-start + verify-hash FP timeout deferral | ✅ v0.3.21 |
| Cerid MCP modes + plan_token apply_execute (ADR-0016) | ✅ v0.3.22 |
| Open-core extract factory + auto re-sync | ✅ export `--verify` + workflows |

### Shipped in v0.3.23 (estate-health foundation)

| Arc | ADR | Surfaces |
|---|---|---|
| Estate health model | **0017** (impl; ADR may remain Proposed→Accepted follow-up) | `steward health show\|check`, `collect_estate_health`, data-dir `health/snapshots.jsonl`, MCP `estate_health` / `estate_health_check`, dashboard `GET /api/health` + `/api/health/series` + posture banner + `refresh_health`, `status --refresh` snapshot hook |
| Audit chain-archive / shrink | **0018** design-only | Full ADR proposed; **no** seal/shrink implementation |
| Plan backlog + schedule reliability | **0019** | `data_dir/plans/`, `steward plans list\|show\|register\|refresh\|prune`, schedule reliability collect, dashboard Queues, MCP `plan_backlog_list` / `plan_backlog_show`, auto-register on `policy plan` |
| Dual-presence / cloud-truth | **0020** | `core`/`infra.dual_presence`, `steward plans filter-dual-presence`, `steward fp dual-presence`, MCP sample/filter tools, `EstateHealthReport.dual_presence`, thin script wrapper |
| Fleet health matrix | **0021** | `steward machines health [--check]`, MCP `fleet_health` / `fleet_health_check`, `GET /api/fleet`, `EstateHealthReport.fleet` + envelope SLA |
| Health check fail-on hygiene | 0017/0020/0021 | `DEFAULT_CHECK_FAIL_ON` = `stale_scan,broken_audit,stash_overdue,rollup_stale` only; `dual_presence_poor` / `fp_not_ready` / fleet tokens opt-in via `--fail-on` |

---

## Dropbox tree rectification (operator host + inventory)

**Authoritative research:** [`field-notes-2026-07-28-dropbox-rectification.md`](field-notes-2026-07-28-dropbox-rectification.md).

**Do not bulk-rewrite claim paths.** Inventory is a **2026-05-17 legacy import** (100% store paths); host FP domain reports **unlinked Dropbox**.

| Phase | Status |
|---|---|
| 1. Inventory history (scan_runs, legacy import, prefixes) | ✅ done |
| 2. Dropbox FP docs + Domains.plist / info.json / name fork | ✅ done |
| 3. Dual-write isolation experiment | ✅ done (reconfirmed 2026-07-28 evening) |
| 4. Micro delete dual-isolation | ✅ mount/store arms reconfirmed; dropbox.com optional |
| 5. Re-link unlinked domain | ⏸ **not required** while tray green + `external_drive_fp` |
| 6a. Conflict folder cleanup | 🟡 **partial** — 3 empty conflicts removed; 3 ghost dirs remain (FP timeout) |
| 6b. Rescan store | ✅ **done** run 5 — walked 360991 / hashed 316026 / claims 360991 |
| 6c. Rescan mount | ⛔ **abandoned** run 6 — FP TimeoutError on nearly all hashes; 0 claims; store is inventory authority |
| 7. Bulk cloud retire | 🟡 **filter ready** — `steward plans filter-dual-presence` / ADR-0020 library; bulk execute still operator-gated |
| 8. Optional dual-index ADR | ⬜ only if needed after rematerialized claims |

**Run artifacts:** `~/Library/Application Support/steward/runs/dropbox-rectif-20260728T230940Z/RUN_STATUS.json`

**Steward’s safe postures (≥0.3.19 + dual-presence library):**

| Intent | How |
|---|---|
| Cloud-propagating retire | Mount present; dual-present objects; filter → `plan-dual.tsv`; default `retire_direct` (verify==unlink on mount). Gate with `--require-fp-healthy`. |
| Local free space on external volume | `--allow-store-path-unlink` (no cloud guarantee); filter intent `local_reclaim` may include `store_only`. |
| Probe | `steward fp status` + `steward fp dual-presence` + `steward health show`. |

**Healthy external-drive FP** (Preferences path under `/Volumes/DropboxStorage`, green Dropbox tray, dual roots) is **supported**. Different `st_dev` and residual Domains.plist “unlinked” / `FPFS_SHOULD_NOT_BE_USED` are **warnings**, not automatic re-link mandates. Real blockers: missing mount, store-only dual samples, Selective Sync Conflict name splits for those basenames.

Inventory rematerialization (phase 6) still recommended after any host repair; stale May import remains.

---

## Open development (prioritized)

### P1 — Operability at multi‑GB inventory scale

- [x] Status inventory **rollups** cache in `meta` (`steward status --refresh` / `--quick`) — v0.3.15  
- [x] `steward fp status` — lightweight fork/health probe without full `fileproviderctl dump` — v0.3.15  
- [x] Optional `apply --require-fp-healthy` hard gate — v0.3.16  
- [x] Dashboard quick-path default (`--full` / `?full=1` for complete) — v0.3.16  
- [x] Audit-log **cold export** (`db audit-export`) — v0.3.17 (does not shrink DB)  
- [x] `fp status` domain unlinked + name-divergence + preflight — v0.3.18  
- [x] **Estate health composite** (`steward health show|check`, snapshots, MCP, dashboard posture) — ADR-0017 (working tree)  
- [x] **Fleet health matrix** (`steward machines health`, envelope SLA, MCP `fleet_health`, `GET /api/fleet`) — ADR-0021 (working tree)  
- [x] **Dual-presence tracking** (plan filter + bounded health sample + MCP) — ADR-0020 (working tree)  
- [ ] Audit-log **shrink** / chain-archive — ADR-0018 **Proposed** (design only; phases A–D not implemented)  
- [x] Pre-ship hygiene: `DEFAULT_CHECK_FAIL_ON` = local integrity only (`stale_scan,broken_audit,stash_overdue,rollup_stale`); `dual_presence_poor` / `fp_not_ready` / fleet tokens remain in `KNOWN_FAIL_ON_TOKENS` as **opt-in** (ADR-0017/0020/0021)  
- [ ] Promote ADR-0017 Status Proposed → Accepted when ship PR lands  
- [ ] `steward status --include-imports` CLI flag (collector already supports)  
- [ ] Always-on estate monitor — **blocked** without new daemon ADR (launchd + CLI only)

### P2 — Operator surfaces / agents

- [x] MCP capability modes + gated `apply_execute` (ADR-0016) — v0.3.22  
- [x] Project `.mcp.json` + Cerid agent integration doc — v0.3.22  
- [x] Retire-decider agent understands `retire_direct` + FP flags — v0.3.15  
- [x] MCP tools: `policy_plan` + `apply_dry_run` + `fp_status` — v0.3.16  
- [x] Tier-auditor checklist includes `fp status` + status `--quick` — v0.3.16  
- [x] Preservation tests: verify==unlink, Dropbox plan → `retire_direct` — v0.3.15  
- [x] Plan backlog registry + MCP list/show + Queues pane — ADR-0019 (working tree)  
- [x] Schedule reliability (installed / loaded / last_exit / overdue) — ADR-0019 (working tree)  
- [x] MCP `estate_health` / `estate_health_check` / `fleet_health` / dual-presence tools — working tree  
- [ ] Richer dashboard per-tier health **panes** (banner + APIs landed; full panes optional follow-on)  
- [x] Stats by-volume aggregator (`steward stats by-volume`) — ADR-0022 / v0.3.24 (host free-space capacity still via health probes only)  
- [x] **Inventory data matrix + graphic surface** (ADR-0022 Accepted, v0.3.24): `stats cross`, path-tree, dashboard Surface treemap + overlays — plan: [`docs/superpowers/plans/2026-08-07-inventory-surface-data-mx.md`](superpowers/plans/2026-08-07-inventory-surface-data-mx.md)  
- [x] Dashboard Fleet tab + dual-presence sample + stats cross UI + plan detail/filter + apply execute handoff (ops console; no full CLI parity)  
- [ ] Surface presence overlay / full dual-presence cube (bounded FS probe) — optional Wave C follow-on  
- [ ] Surface selection → plan seed TSV (operator-gated) — optional Wave C follow-on  
- [ ] Launchd templates invoking `health check --write-snapshot` on weekly-verify cadence (plist follow-on)  

### P3 — Open-core Phase 1 (approved direction)

See [`OPEN_CORE.md`](OPEN_CORE.md).

- [x] `export-open-core.sh --stage --tarball` + scrub + PUBLIC_README — v0.3.17  
- [x] Apache-2.0 `LICENSE` file in repo — v0.3.17  
- [x] Public GitHub repo + extract: https://github.com/Cerid-AI/steward-open  
- [x] Linux-first public CI workflow in open extract  
- [ ] Re-export open-core after v0.3.23 (core.health / dual_presence / fleet must stage)  
- [ ] PyPI publish (name TBD)  

### P4 — Strategic (family-locked or low demand)

- Python 3.13 + uv (with Cerid — still 3.12)  
- [x] Weekly inventory envelope schedule template — v0.3.17  
- CLIP near-dup; photos import `--execute` wrapper  
- Cloud archive via rclone; CUE/Rego; Neo4j; Hydrus tags  
- `fixup --inflight` (reactive only)  
- Dual-index claims (phase 8) only if rematerialized mount inventory demands it  

### P5 — Operator unfinished (non-code)

- SBC / Dropbox bulk: filter dual-presence → dual TSV → dry-run → execute (cloud intent)  
- Boot SSD cleanup; Photos bulk import execute  
- Dropbox host: optional re-link unlinked FP domain; then mount rescan (above)  
- Dual data-dir hygiene (Application Support vs empty XDG stub)  
---

## GitHub Actions minute policy (family standard)

Steward CI follows `~/dotfiles` / global `<github_actions_policy>` and
cerid-anneal’s Tier-1 pattern. Checklist for `.github/workflows/ci.yml`:

| Rule | Steward `ci.yml` |
|---|---|
| 1 concurrency cancel | ✅ `cancel-in-progress: true` |
| 2 long jobs `needs: [lint, typecheck]` | ✅ test, security, preservation |
| 3 path filter docs/md | ✅ `paths-ignore` on push + PR (workflow file still triggers when CI changes) |
| 4 consolidated lint | ✅ ruff + import-linter + silent-catch in one job |
| 5 schedule `# why:` | ✅ no cron on main CI |
| 6 dependabot scope | ✅ `test` skips unless label `dependabot-full-ci`; lock-sync + security always |
| 7 high-cost merge-only | ✅ `preservation` on main push / merge_group / dispatch only |
| 8 self-hosted | N/A (ubuntu-latest) |

Open-core export already path-filters + concurrency; publish uses
`cancel-in-progress: false` (artifact integrity).

**Branch protection note:** do not require the `preservation` check on
every PR if it only runs at merge time — gate it on the merge queue /
main, or docs-only PRs will wait forever.

## Dashboard product stance (ops console — not full CLI parity)

**Decision (2026-08-07):** Do **not** pursue full GUI parity with every `steward` command.
The dashboard is an **ops console + exploration + plan hygiene** surface.

| Tier | In GUI | Notes |
|---|---|---|
| Always | status, health, fleet, stats/surface, inspect, FP + dual-presence sample, plans list | Multi‑GB: prefer path_prefix |
| Usually | policy plan, apply dry-run + **execute handoff**, dual-presence filter, replicate/archive dry-run | Existing EXECUTE-gated rail actions **retained** (no regression) |
| CLI/MCP primary | scan/watch/classify/embed, `db *`, **apply --execute**, photos, schedule install, env/policy files | Handoff shows exact CLI/MCP after dry-run |
| Not default | In-browser apply execute, multi‑GB DB transfer, config SoT editors | Would weaken ADR-0002/0016 |

**Regression rule:** Do not remove ops-rail actions that already ship (replicate/archive/stash EXECUTE, dry-runs, inspect, etc.) when refining the console.

## Suggested next theme (post-0.3.24)

> **Continuous stewardship ops**  
> Audit chain-archive (ADR-0018), launchd health-snapshot cadence, open-core re-export (include `core.matrix` + health/fleet/dual-presence), dual-presence bulk execute when operator-ready. Optional Surface Wave C: presence overlay, plan seed from selection. No full GUI parity project.

Dropbox **rectification** remains a **side workstream**.

**Post-ship:** open-core re-export; optional ADR-0017 Status → Accepted polish.

---

## Reference

| Doc | Role |
|---|---|
| `docs/ROADMAP.md` | Shipped release table |
| `docs/OPEN_CORE.md` | Public/private split |
| `docs/adr/0017-estate-health-model.md` | EstateHealthReport contract |
| `docs/adr/0018-audit-chain-archive.md` | Audit shrink design (not implemented) |
| `docs/adr/0019-plan-backlog-and-schedule-reliability.md` | Plans + schedule reliability |
| `docs/adr/0020-dual-presence-tracking.md` | Cloud-truth dual-presence |
| `docs/adr/0021-fleet-health-matrix.md` | Multi-machine fleet matrix |
| `docs/adr/0022-inventory-surface-and-data-matrix.md` | Data matrix + inventory surface (Accepted) |
| `docs/superpowers/plans/2026-08-07-inventory-surface-data-mx.md` | Task-level implementation plan for ADR-0022 |
| `docs/field-notes-2026-07-13-fp-cleanup.md` | FP empirics + sample |
| `docs/field-notes-2026-07-28-dropbox-rectification.md` | Rectification research (history + domain) |
| `docs/runbooks/dropbox-rectification.md` | Operator checklist + post-scan script |
| `docs/runbooks/cloud-fp-retire.md` | Operator procedure (prefer library CLI) |
| `docs/adr/0014-*.md`, `0015-*.md` | retire_direct + mount policy |
| `HANDOFF.md` | Session state for next operator/agent |
| `CHANGELOG.md` | Per-release detail |
