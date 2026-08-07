# Steward Roadmap

What's shipped, what's next, what's deferred. See [`CHANGELOG.md`](../CHANGELOG.md)
for the full per-release inventory; this doc is the higher-level view.

---

## Shipped (v0.1.0 — May 2026)

The bootstrap milestone. Permanode + claim schema, hash ladder
(xxh3-128 fast / blake3 archive), scanner walker, classification, the
plan/apply lifecycle, cooling-off stash, the legacy unified-hash.db
import path, and the operator CLI surface (`db`, `import`, `scan`,
`classify`, `policy`, `inspect`, `apply`, `stash`).

Patch releases:

- **v0.1.1** — mirror-path resolver, `--resume`, container walker
  (zip/tar), subtree-disjoint parallel walker.
- **v0.1.2** — sprawl-audit script ports: `verify_stash_dedup` →
  `steward stash verify`; `recovered_retire` → bundled
  `recovered.yml` + `recovered_substrings` reconciler bias.

10 ADRs landed in `docs/adr/` covering the foundational decisions
(permanode model, operator-in-the-loop, audit chain, YAML policy,
hash ladder, single DB file, cooling-off stash, machine_id from day
one, pull-don't-push, classification deferred from ingest).

---

## Shipped (v0.2 — May 2026)

The adapter wave + operator surface. Fifteen patch releases since
v0.2.0, every one CI-green on first try (with one detect-secrets
follow-up at v0.2.4 and one bandit follow-up at v0.2.8 — both
inspired their respective local-gate additions).

| Release | Theme | Highlights |
|---|---|---|
| **v0.2.0** | fsevents + containers + embeddings + MCP-readonly | `WatcherProtocol` + `watchdog` adapter, disk-image (hdiutil) + 7z/RAR (unar) container handlers, ONNX e5-small embeddings + vec0 semantic search, 8-tool read-only MCP server. **+63 tests.** |
| **v0.2.1** | osxphotos inventory | `steward photos inventory` walks a `.photoslibrary` via osxphotos, writes claims with `classification=photos-app:<uuid>`. **+7 tests.** |
| **v0.2.2** | photos plan | `steward photos plan` groups staging files by parent dir, classifies new/already/unknown, renders the exact `osxphotos import` command. **+8 tests.** |
| **v0.2.3** | rclone replication | `ReplicationPolicy` schema + `steward replicate run`. Plaintext mirror tier. **+19 tests.** |
| **v0.2.4** | restic archive | `ArchivePolicy` schema + `steward archive {init, snapshot, list, show}`. Encrypted dedup'd snapshots. **+26 tests.** |
| **v0.2.5** | MCP write surface | 7 write tools with `destructiveHint=True` + `mcp_write_invoked` audit marker. **+8 tests.** |
| **v0.2.6** | status CLI | `steward status [--json]` single-pane operator dashboard. **+6 tests.** |
| **v0.2.7** | README + QUICKSTART + ROADMAP + docs guards | Full README rewrite + new QUICKSTART + ROADMAP + 8 CI-enforced docs-consistency tests. **+8 tests.** |
| **v0.2.8** | `steward schedule` (launchd) | Three bundled plists (nightly-archive, nightly-replicate, weekly-verify) + `schedule {list,show,install,uninstall,status}`. **+16 tests.** |
| **v0.2.9** | multi-machine awareness + local bandit gate | `steward machines {list,show}`, MCP `list_machines` / `get_machine`, `inventory.machines` in status report. `make bandit` + `make gates`. **+6 tests.** |
| **v0.2.10** | HTML dashboard | `steward dashboard` over stdlib `http.server`. `/`, `/status.json`, `/healthz`. **+9 tests.** |
| **v0.2.11** | Claude Code sub-agents | Four bundled sub-agents (`tier-auditor`, `promotion-planner`, `retire-decider`, `verifier`) + `docs/AGENTS.md` + agent-consistency validator. **+19 tests.** |
| **v0.2.12** | inspect --json + ADRs 0011, 0012 | `steward inspect --json` + `--machine` + ADR-0011 (MCP write surface) + ADR-0012 (sub-agent scope). **+6 tests.** |
| **v0.2.13** | `steward stats` | Six read-only aggregations: overview / by-tier / by-domain / extensions / classifications / duplicates. All support `--json`. **+17 tests.** |
| **v0.2.14** | `steward db backup` | One-shot online-backup snapshot via `Connection.backup`. Concurrent-write-safe. **+8 tests.** |

**At v0.2.14: 20 subcommands, 338 tests passing, 12 ADRs in force, 4 bundled sub-agents.**

Closes operator-pending items from the sprawl-audit handoff:

- ✅ #2 Synoreport prune (operator-side; done)
- ✅ #3 Bulk Photos.app imports (v0.2.1 inventory + v0.2.2 plan; operator runs the actual `osxphotos import`)
- ✅ #4 CCC config (v0.2.3 ships a tool-agnostic rclone-based replacement)
- ⏳ #5 Boot SSD cleanup (operator-driven once Photos imports complete)

---

## What v0.2 didn't ship that the original plan said it would

| Plan item | Status |
|---|---|
| osxphotos adapter | ✅ v0.2.1 + v0.2.2 |
| rclone / restic / CCC adapters | ✅ v0.2.3 (rclone) + v0.2.4 (restic). CCC replaced by tool-agnostic replicate. |
| fsevents watcher | ✅ v0.2.0 |
| MCP server (read-only) | ✅ v0.2.0 |
| MCP server (write surface) | ✅ v0.2.5 (was framed for v0.3; landed early) |
| HTML dashboard | ✅ v0.2.10 (was framed for v0.3; landed early) |
| Local embeddings (ONNX e5-small) | ✅ v0.2.0 |
| Disk-image + 7z/RAR container handlers | ✅ v0.2.0 |
| Cerid lifts | (Skipped; the bandit / detect-secrets / lock-sync patterns were already in place from v0.1 bootstrap.) |
| `steward fixup --inflight` | Deferred indefinitely — never observed in practice. |

Bonus deliveries that weren't in the v0.2 plan:

- Bundled launchd plists + `steward schedule` (v0.2.8)
- Multi-machine awareness CLI + MCP (v0.2.9)
- Four Claude Code sub-agents (v0.2.11)
- `steward stats` aggregation surface (v0.2.13)
- `steward db backup` online-backup snapshot (v0.2.14)
- 12 ADRs (10 from v0.1 plus 0011 + 0012 from v0.2)
- 8 CI-enforced docs-consistency tests + agent-consistency validator
- README + QUICKSTART + AGENTS + ROADMAP fully aligned to current state

---

## Shipped (v0.3 — May 2026, in progress)

| Release | Theme | Highlights |
|---|---|---|
| **v0.3.0** | ADR-0013 + `steward db export` | Cross-machine wire-format ADR; schema migration `0002_attached_inventories`; `steward db export` produces a tar.xz envelope (inventory.db + manifest.json + checksums.txt) for the future importer to attach. **+18 tests.** |
| **v0.3.1** | `steward db import` | Receiver side of the wire format. Unpacks the envelope, blake3-verifies payload + manifest, verifies the imported audit chain, copies payload to `<data_dir>/imports/<machine_id>/<iso>.db`, upserts `attached_inventories` row. Same-machine refusal + future-version refusal + tamper detection. **+11 tests.** |
| **v0.3.2** | `steward db imports {list, detach}` | Operator surface for `attached_inventories`. List shows machine_id / hostname / version / age / payload status (ok / MISSING). Detach removes the row, unlinks payload, appends `inventory_detached` audit row. Requires `--dry-run` or `--execute` (ADR-0002). Resolves machine_id by full UUID or unique prefix. **+12 tests.** |
| **v0.3.3** | `steward db verify --imports` | Adds `--imports` flag to `steward db verify`. Walks every attached inventory's audit chain independently; updates `chain_verified_at` on success, preserves prior good timestamp on failure (last-known-good signal). Read-side attestation — does NOT append to the local audit chain. **+7 tests.** |
| **v0.3.4** | apply pre-flight (ADR-0013 enforcement) | `steward apply` refuses manifests that reference a permanode_id present only in an attached inventory. Opportunistic — fires only when imports exist (single-machine installs untouched). Attaches each imported .db `?mode=ro`, classifies each row, appends `apply_rejected_imported_claim` audit row per refusal. **+6 tests.** |
| **v0.3.5** | `machines --include-imports` (first read-side fan-out) | New `attach_imports` context manager shared by all future read-side surfaces. `machines list / show` gain `--include-imports`; `MachineSummary` gains a `source` field (`local` / `attached`). Fan-out aggregator UNION-ALLs across schemas; attached inventories surface even with zero rows. **+8 tests.** |
| **v0.3.6** | full read-side fan-out (inspect / stats / dashboard / MCP) | Bundles the remaining four read surfaces. `inspect --include-imports` resolves foreign hashes + tags rows with source. `stats --include-imports` (all 6 aggregators) UNION-ALLs across schemas. Dashboard URL `?include_imports=1` + scope toggle. 4 MCP read tools accept `include_imports`. **Completes ADR-0013.** **+15 tests.** |
| **v0.3.7** | `retire_direct` manifest action + ADR-0014 | New action for cloud-FP-backed tiers (DropboxStorage, iCloudDrive). Direct `Path.unlink()` + audit, no same-FS stash rename (which would propagate as new cloud upload via FP). External tier-specific trash (e.g. Dropbox 30-day cloud trash) provides cooling-off. Algo-aware verifier (xxh3_128 + blake3). Cross-machine pre-flight composes unchanged. Surfaced by real-world Dropbox cleanup work. **+9 tests.** |
| **v0.3.8** | sha256 algo support for retire_direct (legacy-import hot-patch) | Same-day fix: permanodes imported from sprawl-audit's `unified-hash.db` carry `canonical_hash_algo='sha256'`; v0.3.7's verifier fell back to blake3 and would have refused every legacy-imported file. v0.3.8 adds sha256 to the algo-aware path. **+1 test.** |
| **v0.3.9** | `steward apply --skip-verify` mode (F11) | CLI flag for FP-tier-scale retire. Skips per-file hash + size verification (which requires cloud hydration on Dropbox FP — ~4 s/file makes 29K-file plans impractical). Existence + regular-file checks still run; audit row records `verified: false` + `verify_algo: null`. Drops 33h verify time to minutes. Affects only `retire_direct` rows; `stash` + `promote` ignore. **+2 tests.** |
| **v0.3.10** | Universalize algo-aware hash-verify (F10) | New shared helpers `core.hashing.hash_file_by_algo` + `new_hasher_for`. `promote_with_verify` (all three hash sites: idempotency / dry-run / execute) now look up the permanode's `canonical_hash_algo` and use the matching algorithm — fixes the legacy + small-file blind spot that the v0.3.7+v0.3.8 retire fix uncovered. **+1 test.** |

**At v0.3.10: 20 subcommands (24 sub-subcommands), 428 tests passing, 14 ADRs in force.**

ADR-0013 (cross-machine sync) is structurally complete since v0.3.6.
ADR-0014 (retire_direct for cloud-FP tiers) is structurally complete since v0.3.10.

---

## ADR-0013 sprint sequence (cross-machine sync — complete)

The original plan's v0.3 list was multi-machine + MCP write +
dashboard + sub-agents + cloud-archive + stack-bump + open-core
split. The first four landed in v0.2. ADR-0013 (cross-machine
sync) drove the v0.3.0–v0.3.6 arc:

**Design**: SQLite payload in a tar.xz envelope; imported
inventories mounted read-only via `ATTACH DATABASE`; per-machine
audit chain verified independently; `apply --execute` structurally
cannot touch imported claims (per ADR-0009 pull-don't-push).

1. **v0.3.0** — schema migration `0002_attached_inventories.py` +
   `steward db export` (wraps `db backup` + manifest.json + tar.xz).
   ✅ Shipped 2026-05-16.
2. **v0.3.1** — `steward db import` (unpack, blake3 verify,
   chain verify, attach). ✅ Shipped 2026-05-16.
3. **v0.3.2** — `steward db imports {list, detach}`. ✅ Shipped 2026-05-16.
4. **v0.3.3** — `steward db verify --imports` (per-attached
   chain verify). ✅ Shipped 2026-05-16.
5. **v0.3.4** — `apply` pre-flight check (refuse manifests with
   non-local claim_ids). ✅ Shipped 2026-05-16.
6. **v0.3.5** — read-side fan-out: `machines list / show
   --include-imports`. ✅ Shipped 2026-05-17.
7. **v0.3.6** — full read-side fan-out: `inspect`, `stats`,
   `dashboard`, MCP read tools. ✅ Shipped 2026-05-17 —
   **ADR-0013 structurally complete.**

## ADR-0014 sprint sequence (retire_direct for cloud-FP tiers — complete)

Surfaced by real-world Dropbox cleanup work on the operator's
Mac Pro inventory. On cloud-FP-backed tiers (DropboxStorage,
iCloudDrive), same-FS stash rename is wrong — the FP agent
propagates both the source delete and the new file in the stash
dir as two cloud events. The right pattern is direct unlink with
the tier's external trash (e.g. Dropbox 30-day cloud trash)
providing the cooling-off.

1. **v0.3.7** — `retire_direct` action + ADR-0014. ✅ Shipped 2026-05-17.
2. **v0.3.8** — sha256 algo hot-patch (legacy-imported permanodes
   from sprawl-audit `unified-hash.db`). ✅ Shipped 2026-05-17.
3. **v0.3.9** — `apply --skip-verify` mode (F11): drops per-file
   verify so 29K-row Dropbox cleanups become minutes-not-days.
   ✅ Shipped 2026-05-17.
4. **v0.3.10** — universalize algo-aware hash-verify (F10) across
   `promote_with_verify` (and any future verify-before-apply
   sites). ✅ Shipped 2026-05-17 — **ADR-0014 structurally complete.**

## Real-world cleanup landed via Steward (this session)

- 22.34 GiB / 82 files of Dropbox SBC content rm'd via
  `retire_direct` — first production data to flow through
  Steward's audit chain.
- Staged for future operator-driven execute:
  `~/sprawl-audit/manifests/sbc-retire-direct.tsv` (29,038 rows
  / 57.53 GiB, post-F12 dedup).

---

## Patch releases after v0.3.10

| Release | Theme |
|---|---|
| **v0.3.11** | FP-tier robustness: `FPUnavailableError`; congested `unlink` defers the row instead of aborting the apply batch (field-notes gap #2). |
| **v0.3.12** | Dashboard adapts to OS dark mode (`prefers-color-scheme`). |
| **v0.3.13** | **ADR-0015** mount-path Dropbox retire; `nas_manifest` export; docs/runbooks/open-core; weekly-run data-dir fix. |
| **v0.3.14** | **verify==unlink** law; reconciler emits `retire_direct` for DropboxStorage; inventory sample documented. |
| **v0.3.15** | Status rollups + `--quick`/`--refresh`; `steward fp status`; docs consistency; agent refresh. |
| **v0.3.16** | Dashboard quick default; `apply --require-fp-healthy`; MCP plan tools; tier-auditor update. |
| **v0.3.17** | `db audit-export`; open-core stage/tarball + PUBLIC_README; `weekly-inventory-export` launchd template. |
| **v0.3.18** | Dropbox rectification research; `fp status` domain/unlinked + name divergence; preflight hardens. |
| **v0.3.19** | Systemic Dropbox validation: CloudStorage tier class; health verdict; external-drive FP warnings vs hard fails. |
| **v0.3.20** | Scanner mid-walk commits + live scan_run progress; Dropbox rectification runbook + post-scan script. |
| **v0.3.21** | Serial scan_run commit-at-start; mount verify-hash TimeoutError → FPUnavailableError per-row. |
| **v0.3.22** | ADR-0016 MCP modes + plan_token `apply_execute`; status/scan_status/inspect MCP tools; Cerid agent integration doc. |

## Shipped (v0.3.24–0.3.25 — August 2026)

| Release | Theme | Highlights |
|---|---|---|
| **v0.3.24** | ADR-0022 inventory surface + data matrix | `core.matrix`, `stats by-volume` / `cross`, `surface tree`, MCP cross/path_tree, dashboard Surface treemap + overlays, plan under `docs/superpowers/plans/` |
| **v0.3.25** | Continuous stewardship ops | status `--include-imports`; ADR-0017 Accepted; weekly-health-snapshot; ADR-0018 seal+verify; Wave C presence + plan-seed; `bulk-retire-prep`; PyPI prep |

## Next (after v0.3.25)

**Authoritative open list:** [`OPEN_DEVELOPMENT.md`](OPEN_DEVELOPMENT.md) · **Open-core:** [`OPEN_CORE.md`](OPEN_CORE.md)

### Still open

- **Dropbox host repair (operator):** re-link unlinked FP domain; mount rescan; optional path rematerialization — research in [`field-notes-2026-07-28-dropbox-rectification.md`](field-notes-2026-07-28-dropbox-rectification.md). **No bulk path rewrite without that.**
- Open-core: first PyPI upload of `steward-fs`; re-sync extract after private changes; `OPEN_CORE_DEPLOY_TOKEN` for GHA publish.
- Stack bump Python 3.13 + uv (with Cerid — still 3.12).
- CLIP near-dup; ADR-0018 phase D audit **shrink** (seal/verify already shipped).

### Stack bump — Python 3.13 + uv

Mechanical migration. Cerid is still on 3.12 + pip-compile; the
plan says we move the family together. Defer until Cerid is ready.

### Public open-core split

Approved direction — see [`OPEN_CORE.md`](OPEN_CORE.md). Phase 0
scaffolding shipped; Phase 1 is create public repo + first sync.

---

## Deferred (no concrete commitment)

- **Cloud archive tier via rclone backends.** Easy to land but no
  current operator demand — `restic` already covers off-machine
  storage well.
- **`steward fixup --inflight`** for orphan `<dst>.inflight` files
  from interrupted `promote_with_verify`. Never observed in
  practice; ship reactively only.
- **CUE / Rego policy languages.** YAML + pydantic has carried v0.1
  + v0.2 cleanly; defer until cross-machine policy distribution
  raises the bar.
- **Bundled cron-style schedule policies (weekly-archive,
  monthly-snapshot).** The launchd plists in v0.2.8 are templates;
  more cadences will land if operators request them.
