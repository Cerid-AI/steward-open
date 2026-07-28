# ADR 0014: `retire_direct` manifest action

**Status:** Accepted
**Date:** 2026-05-17

## Context

ADR-0007 established the cooling-off stash pattern: a destructive
``apply --execute`` operation is realised as a same-FS rename into
`<tier>/_cooling-off-stash/<run_id>/`, with the actual `rm` deferred
to `steward stash finalize` after a policy-defined window (default
7 days). The pattern works cleanly on tiers where (a) the operator
controls all writes and (b) same-FS rename is semantically free.

It does **not** work on tiers backed by an external cache+sync
agent such as macOS's Dropbox File Provider (FP). Same-FS rename
inside `/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/...` is
seen by the FP as **two events**:

1. Source file deleted → propagated to Dropbox cloud as delete.
2. New file appeared at the stash path → propagated as new upload
   at a fresh cloud path.

Net effect: the content stays in the Dropbox cloud account (at the
stash path), the cloud quota is unchanged, and the `cooling-off`
becomes a quirky cloud-side rename rather than the intended pause.
Operator intent for retiring duplicates from cloud storage is
defeated by the stash semantics.

Several real-world tiers share the FP-style property:

* **DropboxStorage** — Dropbox FP backing on macOS.
* **iCloudDrive** — Apple FP backing.
* (Future) Box, OneDrive, Google Drive FP backings.

These tiers all share two attributes:

1. The FP/sync agent automatically propagates all FS changes to a
   cloud counterpart.
2. The cloud counterpart **already has a built-in retention pattern**
   — Dropbox 30-day cloud trash, iCloud Drive 30-day deleted
   items, etc.

In other words: the cooling-off window already exists, it just
lives on the cloud side, not the same FS. Steward should defer to
that external mechanism rather than fight it.

## Decision

**Add a new manifest action ``retire_direct`` that performs a
direct ``unlink()`` on the source file plus an audit row, with no
same-FS rename and no in-Steward cooling-off period. Pair it with
a per-tier policy declaring which tiers warrant this action and
where the external cooling-off lives.**

Specifically:

1. **New action kind**: ``retire_direct`` in
   :class:`steward.core.model.manifest.ActionKind`.
2. **Pre-execute verification** identical to ``stash``:
   * the source path exists and is a regular file
   * the file's blake3 matches ``row.canonical_hash``
   * the file's size matches ``row.size_bytes``
   * (recommended; not enforced) at least one current claim on a
     different tier or path exists for the same permanode — the
     "canonical elsewhere" sanity guard. Surfaced as a warning in
     the audit row payload, not a refusal.
3. **Execution**: ``Path.unlink()`` on the source path inside a
   transaction with the audit-row append. The ``claims.is_current``
   flag for the affected claim flips to ``0``.
4. **Audit row** with ``action = "retire_direct_executed"``.
   ``payload_json`` carries:
   * the absolute source path
   * the canonical hash
   * the rationale from the manifest row
   * a ``cooling_off_mechanism`` string describing where the
     post-action recovery lives (e.g. ``"dropbox-cloud-trash-30d"``)
   * any "canonical elsewhere" claim ids found at pre-flight time
5. **No stash row** is created. There is no `stash list` /
   `stash finalize` lifecycle.
6. **Dry-run semantics**: identical to other actions —
   ``apply --dry-run`` runs the verification but commits nothing
   and does not call ``unlink()``.
7. **The pre-flight cross-machine check (ADR-0013) applies
   unchanged**: a ``retire_direct`` row referencing a permanode
   that exists only in an attached inventory is refused along with
   every other action.

## Why not …

### …per-tier policy on the existing ``stash`` action?

Could add a `cooling_off_mode: "stash" | "direct"` field on
``RetentionPolicy`` and have ``stash`` dispatch accordingly. Two
reasons against:

* The manifest action is the operator-facing primitive; making
  one action mean two different filesystem outcomes depending on
  a policy lookup hidden in YAML hurts predictability. An operator
  reading the manifest row should be able to reason about what it
  will do without consulting the policy.
* The audit-row ``action`` column becomes ambiguous — was the
  rename done or skipped? — and forensic queries get harder.

A separate action name preserves clarity.

### …always-stash, always-7-day-cooling-off, never trust the cloud?

A non-starter for cloud-FP tiers (see Context). The "always stash"
semantics actively work against the operator's intent on those
tiers.

### …implement as a CLI flag (``apply --retire-direct``)?

The choice of mechanism per-row belongs in the manifest, not in
the apply invocation, because a single manifest may legitimately
contain mixed rows (stash some files, retire-direct others). The
manifest action is the right place.

## Consequences

**Positive:**

* Steward can correctly retire duplicates from cloud-FP tiers
  (Dropbox, iCloud Drive, …) by issuing direct unlinks; the
  external trash provides the cooling-off.
* Audit chain captures the retire event with full payload.
* Cross-machine pre-flight (ADR-0013) applies to the new action
  with zero changes (one-line dispatch addition).
* No new policy-engine concept — the manifest row is fully
  self-describing.
* Behavioural parity with ``stash`` on the verification path
  (hash + size + permanode existence checks) — operators get the
  same safety guarantees.

**Negative:**

* The "cooling-off lives on the cloud side" claim is operator
  knowledge, not enforced by Steward. If an operator runs
  ``retire_direct`` on a tier whose external trash doesn't exist
  or has been disabled, the file is permanently gone immediately.
  Mitigated by: the per-row ``rationale`` field naming the
  cooling-off mechanism + the audit row's
  ``cooling_off_mechanism`` field for forensics.
* Two semantically similar actions (``stash`` and
  ``retire_direct``) — operators need to know which to use on
  which tier. Mitigated by: bundled retention policies will
  encode the right choice per tier.
* No restore path within Steward — if the operator needs to
  recover, they must use the cloud-side trash UI. (Acceptable
  given those UIs are operator-facing in any case.)

## Status

Shipped in v0.3.7 (2026-05-17).

* Manifest model: ``ActionKind`` extended.
* Apply dispatch: branch added in ``_apply_with_con``.
* Infrastructure: new ``infra/retire.py`` module.
* Tests: integration coverage in
  ``tests/integration/test_retire_direct.py`` (verify-hash
  refusal, idempotency, audit chain, dry-run no-writes,
  cross-machine pre-flight composition).

v0.3.8 (same-day hot-patch): added sha256 to the algo-aware
verifier (legacy-imported permanodes carry
``canonical_hash_algo='sha256'``).

v0.3.9 (same-day): added ``--skip-verify`` mode to
``steward apply`` for retire_direct rows at FP-tier scale.

## Skip-verify mode (v0.3.9, F11)

On cloud-FP-backed tiers, per-file content verification reads
the file content. For Dropbox FP this can require cloud
hydration of every retire candidate — at ~4 s/file via the
Dropbox sync agent, a 30K-file retire takes 33 hours of verify
time before any rm happens. That's not practical for bulk
cleanup.

The mode trades the per-file content check for the inventory's
recorded hash + the post-action cooling-off (cloud trash) as
the safety net. Specifically:

* CLI: ``steward apply --execute --skip-verify``
* Apply propagates ``skip_verify`` only to ``retire_direct``
  rows; other actions (``stash``, ``promote``) ignore it.
* When ``skip_verify=True``:
  * The existence + regular-file checks STILL run (cheap).
  * The size check is SKIPPED.
  * The hash check is SKIPPED.
  * The audit row's ``verified`` field is ``False`` and
    ``verify_algo`` is ``None``, so forensics can distinguish
    skip-verified retires from verified ones.
* The CLI warns conspicuously when ``--skip-verify`` is set.

**Use it when:** the inventory's recorded hash is the
operator's source of truth (e.g. the file hasn't been touched
since the last scan), and the cooling-off mechanism (cloud
trash) is sufficient recovery for any rare case where Steward's
recorded hash drifted.

**Don't use it when:** files may have been modified since the
last scan AND the cooling-off recovery isn't acceptable.

## Follow-up field notes (2026-07-13)

A real-world Dropbox/iCloud FP cleanup session surfaced empirical findings
that **qualify the load-bearing assumption** of this ADR — namely that a
direct `unlink()` on a `/Volumes/DropboxStorage/.CloudStorage/Data/...`
store path propagates to the cloud as a delete. Observation (under FP
congestion): it did **not** propagate; only a delete via the user-facing
mount (`~/Library/CloudStorage/Dropbox/...`) or dropbox.com did. If confirmed
on a settled FP, `retire_direct` should target the mount path, not the store
path. Also: cloud-trash retention is account-specific (this account = 1-year
Extended Version History, not 30 days), and the mount times out (Errno 60)
under FP congestion. See [`docs/field-notes-2026-07-13-fp-cleanup.md`](../field-notes-2026-07-13-fp-cleanup.md)
for the full analysis + suggested sequencing. **Re-verify finding #1 before
relying on `retire_direct` for cloud-quota reclaim.**
