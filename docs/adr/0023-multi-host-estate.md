# ADR 0023: Multi-host estate: host identity, volume ownership, pull transport

**Status:** Accepted  
**Date:** 2026-10-01  
**Accepted:** 2026-10-01 (shipped through v0.3.29–0.3.35; released as v0.4.0)  
**Related:** ADR-0002 (operator-in-the-loop), ADR-0003 (append-only audit),
ADR-0006 (single inventory.db), ADR-0008 (machine_id from day one),
ADR-0009 / ADR-0013 (pull-don't-push / wire format + ATTACH RO),
ADR-0015 (cloud File Provider paths), ADR-0017 (estate health),
ADR-0021 (fleet health matrix), `docs/estate.example.yml`

## Context

ADR-0008 put `machine_id` on every claim and audit row while Steward ran
on a single machine, so that a second machine would need no schema change.
ADR-0013 then defined the cross-machine wire format (a tar.xz envelope,
imported read-only via `ATTACH`) and left the transport to the operator;
`OPEN_CORE.md` and ADR-0021 §7–8 deferred a transport to a separate ADR.

A second host changes more than the transport. The shape that forced the
question:

- Two hosts on different LANs, joined by a private network. One NAS sits on
  the first host's LAN, and **both hosts mount its shares at the same
  paths**. A path alone cannot say which host may act on it. Before this
  ADR both hosts scanned the shares, so two inventories claimed the same
  files, and a dedup plan on either host could stash a file the other one
  depended on.
- One share is a **Time Machine destination**. macOS owns its layout:
  Steward may read it (it is a useful promote source) and may keep its own
  replicas in a folder there, but must never stash, retire, dedup or write a
  NAS manifest into it.
- A **disk image** (sparsebundle) on the NAS is attached on one host only;
  attaching it on two at once corrupts it. Its band files also look like
  identical small files, which a dedup plan would happily stash.
- Each host keeps its data dir on an **external volume**. When that volume
  was not mounted, its mount point was an ordinary directory and Steward
  created a fresh `inventory.db` — with a new `machine_id` — on the boot
  disk.
- A host bootstrapped by **copying** another host's `inventory.db` would
  carry that host's `machine_id` and write claims under it.

Constraints carried over: operator-in-the-loop for every mutation
(ADR-0002), append-only audit (ADR-0003), pull-don't-push (ADR-0009/0013),
`steward.core` pure and free of host I/O, no daemon, and a single-host
install must keep behaving as it did.

## Decision

### 1. An estate file declares hosts and volumes

`$STEWARD_ESTATE_CONFIG`, else `~/.config/steward/estate.yml`. The same file
is deployed to every host. A `$STEWARD_ESTATE_CONFIG` that names a missing
file is an error; only a missing *default* file means "no estate". An
invalid file fails closed (`EstateError`) for anything that resolves the
data dir.

The model is pure (`steward.core.estate`); loading and host identity live in
`steward.infra.estate`. `docs/estate.example.yml` is an annotated two-host
example.

- **Host** — id (`[a-z0-9-]+`), `role` (`primary` | `client`),
  `hostnames`, optional pinned `machine_id`, optional `data_dir`, optional
  `publish` block (§5).
- **Volume** — id, `kind` (`boot | local | nas | cloud-fp | disk-image`),
  `tier` (claim label), exactly one `owner` host, `scan`, `plan` (§2), and
  one **mount per host that sees it**: path (or raw `prefixes`, or a
  `match_regex` for paths such as a File Provider mount under the home
  directory), `access`, `criticality`, per-mount `scan`. Optional:
  `aliases` (legacy prefixes), `scan_excludes`, `cloud_fp` (store and mount
  roots, replacing the hardcoded Dropbox layout of ADR-0015), `image` +
  `exclusive_attach`, `replica_grants` (§4), `probe_skip`,
  `time_machine_target`.
- **Validation** — every referenced host exists; only the owner may mount
  `rw`; `plan: full | nas-manifest` requires `scan: true`; no host mounts
  the same path twice; a Time Machine target's grants are exactly
  `<root>/<grant host id>`.

**Which host am I.** `$STEWARD_HOST` (must be a declared id), else the
system hostname without `.local`, matched case-insensitively against every
host's `hostnames`; a hostname listed under two hosts matches neither.
With an estate and no match the host is *unknown*: read commands warn and
classify with the legacy grammar, mutating commands refuse.

**Classification.** A path resolves through *this host's* mounts: regex
mounts first, then the longest prefix (aliases count as prefixes). The
same path can therefore resolve to `rw` on the owner and `ro` on another
host. A path on no declared volume is `other-volume` / `unknown` with
access `ignore`.

### 2. One owner per volume; access and plan mode decide every action

Access levels, per host and mount:

| Access | Meaning |
|---|---|
| `rw` | read and mutate; owner only |
| `ro` | read, scan, probe |
| `probe` | capacity probes only |
| `ignore` | known and deliberately unmanaged: not probed, not reported as an unexpected mount |
| `forbid` | must not be present on this host; presence is a health failure |

Plan modes, per volume:

| Plan | Permits |
|---|---|
| `full` | apply, stash, retire, promote destination, stash finalize/restore; promote source |
| `nas-manifest` | NAS manifests; promote source |
| `source-only` | promote source only — nothing is ever planned onto it (scan may be off) |
| `none` | observe only |

`steward.core.estate.ownership` decides each action for this host:

| Action | Allowed when |
|---|---|
| `scan` | access `rw`/`ro`, volume `scan`, and the mount's `scan` |
| `plan` (emit rows) | this host is the owner and plan ≠ `none` |
| `apply`, `stash`, `retire`, `promote-destination`, `stash-finalize`, `stash-restore` | owner, access `rw`, and the plan mode permits it |
| `nas_manifest` | owner, access `rw`, plan `nas-manifest` |
| `promote-source` | access `rw`/`ro` and plan `full`, `nas-manifest` or `source-only` |
| `replicate-destination`, `archive-destination` | access `rw`/`ro` and the path is inside one of this host's `replica_grants` (on a segment boundary); a volume with no grants accepts only its owner with `rw`; a non-absolute destination (an rclone remote) is outside the estate and allowed |
| `probe` | access ≠ `ignore`; on `forbid` the probe is allowed and flagged as a finding |

A path on no declared volume is refused for every action. The checks sit at
the points of mutation, not only in the planners: `apply` preflights every
row's source and destination before any row runs (covering the CLI, MCP,
the dashboard and `bulk-retire-prep`), `stash finalize|restore` skip
entries on foreign volumes, `scan` refuses roots it may not scan,
`policy plan` drops rows this host could not apply, and `replicate` /
`archive` check their destinations.

Scans also skip `*.sparsebundle` directories by default, plus each volume's
`scan_excludes`, so image bands never become claims.

### 3. A host is bound to its database, and its data dir to its volume

- **Data dir precedence:** `STEWARD_DB_PATH` > `STEWARD_DATA_DIR` > this
  host's estate `data_dir` > the platformdirs default.
- **Mount guard:** creating, migrating or opening the inventory for writing
  refuses a data dir under `/Volumes/<name>` while `/Volumes/<name>` is not
  a mountpoint (`DataDirUnavailableError`). Read-only opens are not guarded.
- **Binding:** comparing the host's pinned `machine_id` with the DB's
  `meta.machine_id` gives `bound | unpinned | missing | mismatch`. A
  `mismatch` refuses every mutation (`BindingMismatchError`), whatever the
  enforcement mode. A new host starts from its own `steward db migrate`, and
  its `machine_id` is pinned in the estate afterwards. Another host's
  database is never copied in.

### 4. Replicas live in per-host namespaces

`replica_grants` let a host write replicas and archives under
`<volume root>/<prefix>` of a volume it does not own. On a Time Machine
target every grant is `<root>/<host id>`, so a host only ever writes its own
folder. A primary that re-publishes a client's artefacts writes them under
**its own** namespace, never the client's.

The inventory database is replicated as `kind: sqlite-snapshot`: an
online-backup copy that must pass `PRAGMA quick_check`, never the live WAL
file, optionally staged on another disk (`staging_dir`) so a spinning data
volume is not read and written on the same spindle. Replication commits
each audit row as it is written, so a multi-hour run holds no write
transaction.

### 5. Transport: the primary pulls over ssh; clients never push

This is the transport ADR-0013 and ADR-0021 deferred. Pull-don't-push
(ADR-0009) holds unchanged.

- A client declares `publish {ssh, envelope, health_dir, max_age_hours}`
  and exports its inventory there on a schedule (`steward db export --out
  <envelope> --overwrite`); every health snapshot also writes
  `health/latest.json`, atomically.
- `steward fleet pull --host <id> [--dry-run | --execute]` runs only on the
  `primary`. It copies the envelope and health dir with `rsync -t --partial`
  over `ssh -o BatchMode=yes` into `<data_dir>/inbox/<id>/`; nothing is
  written on the client. Dry-run (the default) prints the commands.
- The client must pin a `machine_id`, and the envelope's exporter
  `machine_id` must equal it, or the pull is refused and audited
  `fleet_pull_refused`. Otherwise `import_inventory` verifies blake3 and the
  audit chain and attaches the payload read-only (ADR-0013), audited
  `fleet_pull`.
- An envelope whose payload (same blake3) is already attached and still on
  disk is not unpacked or re-imported; the pull is audited as `unchanged`.
  A client may therefore export less often than the primary pulls.
- How the client restricts the pull key — for example a forced command that
  allows only a read-only rsync of the published paths, from the primary's
  address — is operator configuration outside Steward.

### 6. The primary grades remote capacity

`remote_capacity` (opt-in `--fail-on` token; primary only): each pulled
`health/latest.json` is re-graded against the **primary's** capacity
thresholds, so one set of numbers governs the estate. A sidecar older than
`publish.max_age_hours` warns; a missing one is `unknown`. Fleet rows are
labelled by estate host id, and with `--include-imports` the stats rollups
count only the owner's claims for owned volumes, so a share both hosts once
scanned is not counted twice.

Health also gains `foreign_attach` (a `forbid` mount is present, or an
`exclusive_attach` image is attached on another host) and `estate_binding`
(unknown host, or a DB `machine_id` that is missing or not the pinned one).
`steward estate show | validate | whoami | check` expose the estate as this
host sees it; `check` compares live mounts with the declaration and sums
the would-refuse audit rows.

### 7. `report` before `enforce`

`enforcement: report` (the default) evaluates every check, audits
`ownership_would_refuse` and lets the action proceed; `enforce` refuses and
audits under the caller's action (`apply_rejected_foreign_volume`,
`scan_refused_foreign_volume`, `replicate_refused_namespace`, …). Refusal
rows are aggregated per check, role and volume, with a count and sample
paths. An estate is meant to run a full cycle in `report`, with every
would-refuse row explained, before it switches to `enforce`. An unknown
host or a binding mismatch refuses in both modes.

### 8. No estate file means the single-host behaviour

Without an estate file Steward builds `legacy.default_estate()`: the
existing tier grammar owned by one implicit host, transcribed from
`core/tiers.py` and parity-tested against it. The ownership guard is not
loaded, so no check runs and no audit row is added. What changed for a
single-host install is limited to fixes and additive output: the data-dir
mount guard, scans skipping `*.sparsebundle` directories, the
`health/latest.json` sidecar, and a null `host_id` on fleet rows.

### 9. Non-goals

- **Cross-host dedup.** Each host plans only its own volumes; identical
  content on two hosts is not a duplicate to remove.
- **Remote apply.** No host mutates another host's volumes, directly or by
  sending it a plan to run. A client's claims arrive read-only.
- Push, a sync daemon, more than one owner per volume, or automatic
  ownership failover.

## Consequences

**Positive**

- A shared path has exactly one host that may change it, and a Time Machine
  share or an exclusive disk image cannot be damaged by a plan.
- A host cannot silently start a fresh inventory on the boot disk, or write
  under another host's `machine_id`.
- The primary holds a verified, read-only copy of every client inventory
  and grades the clients' capacity, with no inbound path into the primary.
- The model is pure and portable, and a single-host install keeps its
  behaviour.

**Negative / residual**

- Claims a client recorded on shared volumes before ownership existed stay
  in its inventory as history: non-actionable on that host, not deleted.
- The primary is the single point of collection; while it is down, envelopes
  and health sidecars wait on the client until the next pull.
- The pulled copy is only as fresh as the client's export schedule;
  `max_age_hours` makes staleness visible, not impossible.
- The estate file must be deployed identically to every host; a host with a
  stale copy decides with stale rules (`estate check` and `estate_binding`
  catch the common cases).

## Alternatives considered

- **Ownership by path** — impossible when two hosts mount one share at the
  same path; the decision has to be per host.
- **One estate file per host** — each would describe only its own host and
  they would drift; one shared file makes "who owns this" a single answer.
- **Clients push to the primary, or into a NAS drop folder** — gives a
  client a write path into the primary or onto a share it does not own,
  against ADR-0009.
- **Open the client's database over a network file system** — SQLite
  locking over SMB is unsafe, and it would bypass the envelope's blake3 and
  chain verification.
- **Cross-host dedup** — one host would need authority over another's
  volumes; a non-goal (§9).
