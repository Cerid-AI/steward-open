# SPDX-License-Identifier: Apache-2.0

"""Policy → plan reconciler.

The reconciler is the bridge between *declarative* policy YAML (what
the operator wants) and the *executable* plan manifest (what
``steward apply`` consumes). It walks the current claim inventory,
applies the policy rules, and produces a manifest.

Reconcilers:

* :func:`reconcile_dedup_retire` — RetentionPolicy + current claims →
  stash / ``retire_direct`` / ``nas_manifest`` plan. For each permanode
  with N>1 current claims across live tiers, keep the copy in the
  highest-priority tier and stash (or retire_direct on FP tiers) the
  rest. Copies on NAS read-only tiers (`Backup`) emit ``nas_manifest``
  rows; apply exports them for DSM/SSH (does not delete on the NAS).

* :func:`reconcile_promote` — PromotionPolicy + Backup-only permanodes →
  promote manifest with destination paths from ``phases[*].destination_root``.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from os.path import basename
from pathlib import PurePosixPath
from uuid import uuid4

from steward.core.model.manifest import (
    Manifest,
    ManifestHeader,
    ManifestRow,
)
from steward.core.policy.matchers import is_noise
from steward.core.policy.schema import PromotionPolicy, RetentionPolicy
from steward.core.tiers import CLOUD_FP_COOLING_OFF, CLOUD_FP_TIERS, LIVE_TIERS

logger = logging.getLogger("steward.core.policy.reconciler")


@dataclass(frozen=True)
class ClaimSnapshot:
    """One row's worth of data the reconciler needs to make a decision.

    Decouples the reconciler from sqlite — callers (M5.2 CLI) load
    ClaimSnapshots from the inventory and pass them in. Tests pass
    in synthetic snapshots without touching SQLite.
    """

    claim_id: int
    permanode_id: str
    canonical_hash: str
    machine_id: str
    file_path: str
    tier: str
    size_bytes: int
    domain: str | None = None


def reconcile_dedup_retire(
    *,
    claims: Iterable[ClaimSnapshot],
    policy: RetentionPolicy,
    steward_version: str,
    manifest_run_id: str | None = None,
    root_prefix: str | None = None,
) -> Manifest:
    """Produce a dedup-retire manifest from current claims + a retention policy.

    Algorithm:

    1. Filter out noise claims (matching ``policy.exclusions``).
    2. Filter to ``root_prefix`` if supplied (e.g. ``/Volumes/Level 2``).
    3. Group remaining claims by ``permanode_id``.
    4. For each group with 2+ live-tier copies, choose the one in the
       lowest ``tier_priority`` value (most canonical) as the keeper.
       Emit ``stash`` rows for every other live-tier copy and
       ``nas_manifest`` rows for any read-only-NAS copies.

    Parameters
    ----------
    claims
        Iterable of current claims. The caller is responsible for filtering
        to ``is_current=1`` rows before passing in.
    policy
        Validated :class:`RetentionPolicy`.
    steward_version
        Version string for the manifest header (the CLI passes ``__version__``).
    manifest_run_id
        Optional — caller can supply a stable id (e.g. when the same
        ``policy plan`` invocation produces two manifests). Defaults to
        a fresh uuid4.
    root_prefix
        Optional path-prefix filter. Only claims whose ``file_path``
        starts with this prefix are considered.

    Returns
    -------
    A :class:`Manifest` (header + rows). Rows may be empty when no
    dedup opportunities exist.
    """
    run_id = manifest_run_id or uuid4().hex
    # Cast to plain ``dict[str, int]`` so runtime lookups with arbitrary
    # tier strings (from DB rows that may carry legacy / unknown tiers)
    # type-check. The pydantic schema still validates the YAML side
    # against the Tier Literal — this is the boundary cast, not a
    # rejection of policy.
    priority: dict[str, int] = {str(k): v for k, v in policy.dedup_retire.tier_priority.items()}
    live_set: set[str] = {t for t in policy.dedup_retire.live_tiers if t in priority}
    nas_set: set[str] = set(policy.dedup_retire.nas_manifest_tiers)
    recovered_substrings: list[str] = list(policy.dedup_retire.recovered_substrings)
    noise = policy.exclusions

    def _is_recovered(c: ClaimSnapshot) -> bool:
        return any(s in c.file_path for s in recovered_substrings)

    # Index claims by permanode.
    grouped: dict[str, list[ClaimSnapshot]] = {}
    for c in claims:
        if root_prefix and not c.file_path.startswith(root_prefix):
            continue
        if is_noise(
            c.file_path,
            always_skip_substrings=noise.always_skip_substrings,
            basename_prefixes=noise.basename_prefixes,
            basename_exact=noise.basename_exact,
        ):
            continue
        grouped.setdefault(c.permanode_id, []).append(c)

    rows: list[ManifestRow] = []
    for pid, group in grouped.items():
        if len(group) < 2:
            continue
        # Path bias: when ``recovered_substrings`` is configured AND the
        # group is mixed (some claims recovered, some not), recovered
        # claims sort AFTER non-recovered regardless of tier. When all
        # claims are recovered (or recovered_substrings is empty), tier
        # priority decides as usual.
        has_recovered = any(_is_recovered(c) for c in group)
        has_non_recovered = any(not _is_recovered(c) for c in group)
        recovered_bias = has_recovered and has_non_recovered

        def _rank(c: ClaimSnapshot, *, bias: bool = recovered_bias) -> tuple[int, int, int]:
            is_rec = 1 if (bias and _is_recovered(c)) else 0
            return (is_rec, priority.get(c.tier, 999), c.claim_id)

        ranked = sorted(group, key=_rank)
        keeper = ranked[0]
        for c in ranked[1:]:
            if c.tier == keeper.tier and c.file_path == keeper.file_path:
                # Same-tier-same-path duplicate (shouldn't happen but defensive).
                continue
            action: str
            destination: str | None
            destination_tier: str | None
            if c.tier in CLOUD_FP_TIERS and c.tier in live_set:
                # ADR-0014/0015: never same-FS stash on cloud-FP tiers.
                action = "retire_direct"
                destination = None
                destination_tier = CLOUD_FP_COOLING_OFF.get(
                    c.tier, "cloud-fp-external-trash"
                )
            elif c.tier in live_set:
                action = "stash"
                destination = _stash_destination(c.file_path, c.tier, run_id, policy)
                destination_tier = c.tier
            elif c.tier in nas_set:
                action = "nas_manifest"
                # NAS manifests don't get a destination_path — the NAS-side
                # script consumes the row and decides where to move.
                destination = None
                destination_tier = c.tier
            else:
                # Unrecognised tier — skip (no auto-retire on unfamiliar mounts).
                continue
            rows.append(
                ManifestRow(
                    action=action,  # type: ignore[arg-type]
                    permanode_id=pid,
                    canonical_hash=c.canonical_hash,
                    size_bytes=c.size_bytes,
                    source_path=c.file_path,
                    source_tier=c.tier,
                    destination_path=destination,
                    destination_tier=destination_tier,
                    rationale=f"dedup of permanode {pid}; canonical lives on {keeper.tier} ({keeper.file_path})",
                )
            )

    header = ManifestHeader(
        produced_by_steward_version=steward_version,
        produced_at=datetime.now(timezone.utc),
        policy_name="retention.yml",
        phase_name="dedup-retire",
        manifest_run_id=run_id,
    )
    return Manifest(header=header, rows=tuple(rows))


def _stash_destination(
    source_path: str,
    tier: str,
    manifest_run_id: str,
    policy: RetentionPolicy,
) -> str:
    """Compute the cooling-off destination for a live-tier stash action.

    Pattern: ``<stash_root>/<manifest_run_id>/<basename>``.
    Where ``stash_root`` is from ``policy.dedup_retire.stash_roots`` per
    tier — falls back to ``<dirname(source_path)>/_cooling-off-stash``
    if the tier isn't in the policy.
    """
    stash_roots: dict[str, str] = {str(k): v for k, v in policy.dedup_retire.stash_roots.items()}
    stash_root = stash_roots.get(tier)
    if not stash_root:
        # Fallback: same-dir stash. Works for any tier, just less tidy
        # than the policy-prescribed central stash root.
        return str(
            PurePosixPath(source_path).parent / "_cooling-off-stash" / manifest_run_id / basename(source_path)
        )
    # Drop ``${HOME}`` etc. literally — expansion is the apply-side job
    # (operator config decides what $HOME resolves to in the live env).
    return str(PurePosixPath(stash_root) / manifest_run_id / basename(source_path))


# ─────────────────────────── live-claim loader ─────────────────────────────


def load_current_claims_from_db(con: object) -> list[ClaimSnapshot]:
    """Load every ``is_current=1`` claim, joined with its permanode's
    canonical_hash. Defined here (rather than in ``infra/db/``) because
    it's a pure-domain helper that happens to take a sqlite3.Connection.

    The connection comes from the caller (cli/policy_cmd.py) via the
    admin facade — ``core`` doesn't import ``infra.db.connect``.
    """
    import sqlite3

    assert isinstance(con, sqlite3.Connection)
    cur = con.execute(
        """
        SELECT c.id, c.permanode_id, p.canonical_hash, c.machine_id,
               c.file_path, c.tier, c.size_bytes, c.domain
        FROM claims c
        JOIN permanodes p ON p.id = c.permanode_id
        WHERE c.is_current = 1
        """
    )
    out: list[ClaimSnapshot] = []
    for row in cur:
        out.append(
            ClaimSnapshot(
                claim_id=int(row[0]),
                permanode_id=str(row[1]),
                canonical_hash=str(row[2]),
                machine_id=str(row[3]),
                file_path=str(row[4]),
                tier=str(row[5]),
                size_bytes=int(row[6]),
                domain=str(row[7]) if row[7] is not None else None,
            )
        )
    return out


# ────────────────────────────── Promotion ───────────────────────────────────


def _phase_matches(claim: ClaimSnapshot, match: dict[str, str]) -> bool:
    """Return True iff ``claim`` matches every key in ``match``.

    Supported match keys:
      * ``domain`` — exact equality with ``claim.domain``
      * ``path_substring`` — case-sensitive substring search in ``claim.file_path``

    Unknown keys cause the rule to refuse to match (defensive; the policy
    lint catches typos at YAML load time).
    """
    for k, v in match.items():
        if k == "domain":
            if claim.domain != v:
                return False
        elif k == "path_substring":
            if v not in claim.file_path:
                return False
        else:
            return False
    return True


def _apply_path_translations(path: str, policy: PromotionPolicy) -> str:
    """Apply the policy's ``path_translations`` to a path; first match wins."""
    for t in policy.defaults.path_translations:
        if path.startswith(t.src):
            return t.dst + path[len(t.src):]
    return path


def _resolve_destination(
    *,
    translated_source: str,
    destination_root: str,
    mirror_from: str | None,
    mirror_strip_prefix: str | None,
) -> str:
    """Compute the destination path for a promote row.

    Resolution priority (first match wins):

    1. **Sentinel** — when ``mirror_from`` is set, take everything after the
       last occurrence of the sentinel substring in ``translated_source`` and
       append it under ``destination_root``. Preserves subdir structure
       below the sentinel. If the sentinel isn't found, fall back to step 2/3.
    2. **Strip prefix** — when ``mirror_strip_prefix`` is set and the
       translated source starts with it, strip the prefix and mirror
       everything below under ``destination_root``.
    3. **Basename** (back-compat default) — flat copy.

    Two source paths that share a basename under different ancestors will
    collide under (3) but be distinguished under (1) or (2).
    """
    root = destination_root.rstrip("/")
    if mirror_from:
        idx = translated_source.rfind(mirror_from)
        if idx >= 0:
            suffix = translated_source[idx + len(mirror_from):].lstrip("/")
            if suffix:
                return f"{root}/{suffix}"
    if mirror_strip_prefix and translated_source.startswith(mirror_strip_prefix):
        suffix = translated_source[len(mirror_strip_prefix):].lstrip("/")
        if suffix:
            return f"{root}/{suffix}"
    return f"{root}/{translated_source.rsplit('/', 1)[-1]}"


def _backup_only_permanodes(
    claims: list[ClaimSnapshot], source_tier: str
) -> dict[str, list[ClaimSnapshot]]:
    """Return permanode_id → [snapshots on source_tier] for permanodes that
    appear ONLY on ``source_tier`` (no live-tier copy)."""
    by_pid: dict[str, list[ClaimSnapshot]] = {}
    for c in claims:
        by_pid.setdefault(c.permanode_id, []).append(c)
    out: dict[str, list[ClaimSnapshot]] = {}
    for pid, group in by_pid.items():
        tiers = {c.tier for c in group}
        if tiers == {source_tier}:
            out[pid] = [c for c in group if c.tier == source_tier]
    return out


def reconcile_promote(
    *,
    claims: Iterable[ClaimSnapshot],
    policy: PromotionPolicy,
    steward_version: str,
    phase_name: str | None = None,
    manifest_run_id: str | None = None,
    max_files: int | None = None,
) -> Manifest:
    """Produce a promote manifest from current claims + a promotion policy.

    Algorithm:
      1. Filter to permanodes that exist only on ``policy.defaults.source_tier``.
      2. For each, find the first phase whose ``match`` accepts the claim.
      3. Emit a ``promote`` row with destination computed by
         :func:`_resolve_destination` (sentinel > strip-prefix > basename).

    ``phase_name`` filters to a single phase; ``max_files`` caps rows.
    """
    run_id = manifest_run_id or uuid4().hex
    claims_list = list(claims)
    backup_only = _backup_only_permanodes(claims_list, policy.defaults.source_tier)
    phases = [p for p in policy.phases if phase_name is None or p.name == phase_name]
    mirror_strip_prefix = policy.defaults.mirror_strip_prefix

    rows: list[ManifestRow] = []
    for pid, snapshots in backup_only.items():
        rep = snapshots[0]
        chosen = next((ph for ph in phases if _phase_matches(rep, dict(ph.match))), None)
        if chosen is None:
            continue
        if max_files is not None and len(rows) >= max_files:
            break
        translated_source = _apply_path_translations(rep.file_path, policy)
        dest = _resolve_destination(
            translated_source=translated_source,
            destination_root=chosen.destination_root,
            mirror_from=chosen.mirror_from,
            mirror_strip_prefix=mirror_strip_prefix,
        )
        rows.append(
            ManifestRow(
                action="promote",
                permanode_id=pid,
                canonical_hash=rep.canonical_hash,
                size_bytes=rep.size_bytes,
                source_path=translated_source,
                source_tier=rep.tier,
                destination_path=dest,
                destination_tier="L2",
                rationale=f"promotion phase {chosen.name}",
            )
        )

    header = ManifestHeader(
        produced_by_steward_version=steward_version,
        produced_at=datetime.now(timezone.utc),
        policy_name="promotion.yml",
        phase_name=phase_name,
        manifest_run_id=run_id,
    )
    return Manifest(header=header, rows=tuple(rows))


# Re-export the LIVE_TIERS constant so the CLI doesn't need to know two
# modules to summarise plan output.
__all__ = [
    "ClaimSnapshot",
    "LIVE_TIERS",
    "load_current_claims_from_db",
    "reconcile_dedup_retire",
    "reconcile_promote",
]
