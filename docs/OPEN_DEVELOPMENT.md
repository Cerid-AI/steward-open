# Steward — Open Development Areas

**Updated:** 2026-07-29  
**Package:** `0.3.22` (see `pyproject.toml` / `CHANGELOG.md`)  
**Remote:** `Cerid-AI/steward`  
**Live inventory:** `~/Library/Application Support/steward/inventory.db` (~9 GiB after store rescan)

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
| Dropbox rectification **research** (history + domain + name split) | ✅ v0.3.18 + field notes |
| Dropbox **systemic validation** (external-drive layout, tier mount, health verdict) | ✅ v0.3.19 |
| Scanner mid-walk commits + rectification runbook | ✅ v0.3.20 |
| Serial scan_run commit-at-start + verify-hash FP timeout deferral | ✅ v0.3.21 |
| Cerid MCP modes + plan_token apply_execute (ADR-0016) | ✅ v0.3.22 |

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
| 7. Bulk cloud retire | ⛔ **blocked** — 221k plan; sample dry-run 8/8 with `--skip-verify`; dual-presence filter required |
| 8. Optional dual-index ADR | ⬜ only if needed after rematerialized claims |

**Run artifacts:** `~/Library/Application Support/steward/runs/dropbox-rectif-20260728T230940Z/RUN_STATUS.json`

**Steward’s safe postures (≥0.3.19):**

| Intent | How |
|---|---|
| Cloud-propagating retire | Mount present; dual-present objects; default `retire_direct` (verify==unlink on mount). Gate with `--require-fp-healthy`. |
| Local free space on external volume | `--allow-store-path-unlink` (no cloud guarantee). |
| Probe | `steward fp status` → `layout` / `cloud_retire_ready` / warnings vs problems. |

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
- [ ] Audit-log **shrink** / chain-archive (needs new ADR)

### P2 — Operator surfaces / agents

- [x] MCP capability modes + gated `apply_execute` (ADR-0016) — v0.3.22  
- [x] Project `.mcp.json` + Cerid agent integration doc — v0.3.22  
- [x] Retire-decider agent understands `retire_direct` + FP flags — v0.3.15  
- [x] MCP tools: `policy_plan` + `apply_dry_run` + `fp_status` — v0.3.16  
- [x] Tier-auditor checklist includes `fp status` + status `--quick` — v0.3.16  
- [x] Preservation tests: verify==unlink, Dropbox plan → `retire_direct` — v0.3.15  

### P3 — Open-core Phase 1 (approved direction)

See [`OPEN_CORE.md`](OPEN_CORE.md).

- [x] `export-open-core.sh --stage --tarball` + scrub + PUBLIC_README — v0.3.17  
- [x] Apache-2.0 `LICENSE` file in repo — v0.3.17  
- [x] Public GitHub repo + extract: https://github.com/Cerid-AI/steward-open  
- [x] Linux-first public CI workflow in open extract  
- [ ] PyPI publish (name TBD)  

### P4 — Strategic (family-locked or low demand)

- Python 3.13 + uv (with Cerid — still 3.12)  
- [x] Weekly inventory envelope schedule template — v0.3.17  
- CLIP near-dup; photos import `--execute` wrapper  
- Cloud archive via rclone; CUE/Rego; Neo4j; Hydrus tags  
- `fixup --inflight` (reactive only)  

### P5 — Operator unfinished (non-code)

- SBC bulk: choose local vs cloud intent before execute  
- Boot SSD cleanup; Photos bulk import execute  
- Dropbox host: re-link unlinked FP domain; then mount rescan (above)  
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
| `docs/field-notes-2026-07-28-dropbox-rectification.md` | Rectification research (history + domain) |
| `docs/runbooks/dropbox-rectification.md` | Operator checklist + post-scan script |
| `docs/adr/0014-*.md`, `0015-*.md` | retire_direct + mount policy |
| `docs/runbooks/cloud-fp-retire.md` | Operator procedure |
| `CHANGELOG.md` | Per-release detail |
