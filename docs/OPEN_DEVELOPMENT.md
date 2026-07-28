# Steward — Open Development Areas

**Updated:** 2026-07-28  
**Package:** `0.3.17` (see `pyproject.toml` / `CHANGELOG.md`)  
**Remote:** `Cerid-AI/steward`  
**Live inventory:** `~/Library/Application Support/steward/inventory.db` (~7.9 GiB)

This is the **authoritative** open-work doc. Supersedes older “next after v0.3.10” prose that still appears in historical sections of older notes.

---

## Current state (shipped)

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

---

## Explicitly deferred: Dropbox tree rectification

**Do not attempt bulk store↔mount “fix” without a dedicated history + API review.**

Live findings (2026-07-28):

- Mount (`~/Library/CloudStorage/Dropbox`) and store (`/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox`) are **different devices**.
- Inventory: **357 733** DropboxStorage claims, **100% store-path**, **0** mount-path claims.
- Sample (n=95): store exists ~99%; mount exists ~19% when stat succeeds.
- New writes did not cross trees within 60s.

**Steward’s safe postures (code already supports):**

| Intent | How |
|---|---|
| Cloud-propagating retire | Mount present; default `retire_direct` (verify==unlink on mount). Rescan mount root first if inventory is store-only. |
| Local free space on external volume | `--allow-store-path-unlink` (no cloud guarantee). |
| Dedup plans including Dropbox | Re-plan under ≥0.3.14 so rows are `retire_direct`, not `stash`. |

**Future Dropbox rectification workstream (separate, high-risk):**

1. Deep inventory history of path prefixes + scan_run roots  
2. Dropbox File Provider / desktop API behaviour (delete, trash, selective sync)  
3. Controlled dual-write / dual-delete experiments with cloud-side confirmation  
4. Only then: rematerialize claims, migrate paths, or dual-index strategy  

Until that workstream completes, treat store and mount as **possibly forked materializations**.

---

## Open development (prioritized)

### P1 — Operability at multi‑GB inventory scale

- [x] Status inventory **rollups** cache in `meta` (`steward status --refresh` / `--quick`) — v0.3.15  
- [x] `steward fp status` — lightweight fork/health probe without full `fileproviderctl dump` — v0.3.15  
- [x] Optional `apply --require-fp-healthy` hard gate — v0.3.16  
- [x] Dashboard quick-path default (`--full` / `?full=1` for complete) — v0.3.16  
- [x] Audit-log **cold export** (`db audit-export`) — v0.3.17 (does not shrink DB)  
- [ ] Audit-log **shrink** / chain-archive (needs new ADR)

### P2 — Operator surfaces / agents

- [x] Retire-decider agent understands `retire_direct` + FP flags — v0.3.15  
- [x] MCP tools: `policy_plan` + `apply_dry_run` + `fp_status` — v0.3.16  
- [x] Tier-auditor checklist includes `fp status` + status `--quick` — v0.3.16  
- [x] Preservation tests: verify==unlink, Dropbox plan → `retire_direct` — v0.3.15  

### P3 — Open-core Phase 1 (approved direction)

See [`OPEN_CORE.md`](OPEN_CORE.md).

- [x] `export-open-core.sh --stage --tarball` + scrub + PUBLIC_README — v0.3.17  
- [x] Apache-2.0 `LICENSE` file in repo — v0.3.17  
- [ ] Public GitHub repo create + first push (org step: `Cerid-AI/steward-open`)  
- [ ] Linux-first public CI; PyPI name  

### P4 — Strategic (family-locked or low demand)

- Python 3.13 + uv (with Cerid — still 3.12)  
- [x] Weekly inventory envelope schedule template — v0.3.17  
- CLIP near-dup; photos import `--execute` wrapper  
- Cloud archive via rclone; CUE/Rego; Neo4j; Hydrus tags  
- `fixup --inflight` (reactive only)  

### P5 — Operator unfinished (non-code)

- SBC bulk: choose local vs cloud intent before execute  
- Boot SSD cleanup; Photos bulk import execute  
- Dropbox host rectification workstream (above)  
- Dual data-dir hygiene (Application Support vs empty XDG stub)  

---

## Suggested v0.4 theme

> **Cloud-truth & continuous stewardship**  
> Mount-aware FP workflows (without assuming Dropbox is healed), multi‑GB status/perf, inventory envelopes on a schedule, agents/MCP that match production retires.

Dropbox **rectification** is a **side workstream**, not a silent assumption of v0.4.

---

## Reference

| Doc | Role |
|---|---|
| `docs/ROADMAP.md` | Shipped release table |
| `docs/OPEN_CORE.md` | Public/private split |
| `docs/field-notes-2026-07-13-fp-cleanup.md` | FP empirics + sample |
| `docs/adr/0014-*.md`, `0015-*.md` | retire_direct + mount policy |
| `docs/runbooks/cloud-fp-retire.md` | Operator procedure |
| `CHANGELOG.md` | Per-release detail |
