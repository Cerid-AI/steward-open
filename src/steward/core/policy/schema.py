# SPDX-License-Identifier: Apache-2.0

"""Pydantic schema for Steward policy YAML.

A policy file ships with Steward bundled (``src/steward/policies/*.yml``)
or is supplied by the operator (``~/.config/steward/policies.d/*.yml``).
All policy authoring is YAML; Python only evaluates.

This module defines the **shape** of valid policy files. The evaluator
(in ``policy.evaluator``) and the reconciler (in ``policy.reconciler``)
consume validated instances of these models.

Three kinds are recognised in v0.1:

* :class:`PromotionPolicy` — phase-driven Backup → live-tier copy plan
* :class:`RetentionPolicy` — noise/exclusion filters + dedup-retire matrix
* :class:`ClassificationPolicy` — content-domain + cluster labels

Each starts with ``version: 1`` and a ``kind`` discriminator.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Tier = Literal[
    "boot", "L1", "L1w", "L2", "L3a", "DropboxStorage", "Backup",
    "BOOTCAMP", "other-volume", "unknown",
]


class _PolicyBase(BaseModel):
    """Common header for every policy YAML."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    kind: str
    metadata: dict[str, str] | None = None


# ────────────────────────────── Retention ──────────────────────────────────


class RetentionExclusions(BaseModel):
    """Path-shape exclusions — when matched, Steward never acts on the path."""

    model_config = ConfigDict(extra="forbid")

    always_skip_substrings: list[str] = Field(default_factory=list)
    """Substrings that appear anywhere in the path. The scanner respects
    these too (some are pre-filtered in :mod:`steward.infra.scanner.skiplist`)."""

    no_promote_substrings: list[str] = Field(default_factory=list)
    """Substrings that block promotion but allow scanning + cataloguing —
    e.g. ``.photoslibrary/`` (we don't extract files from inside a library
    package; we promote the package whole or not at all)."""

    basename_prefixes: list[str] = Field(default_factory=list)
    """File-name prefixes (``._`` etc.)."""

    basename_exact: list[str] = Field(default_factory=list)
    """File-name exact matches (``.DS_Store`` etc.)."""


class DedupRetire(BaseModel):
    """Cross-tier dedup retire matrix."""

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["keep-highest-priority-tier"] = "keep-highest-priority-tier"
    tier_priority: dict[Tier, int]
    live_tiers: list[Tier]
    cooling_off_days: int = Field(ge=0, default=7)
    stash_roots: dict[Tier, str] = Field(default_factory=dict)
    nas_manifest_tiers: list[Tier] = Field(default_factory=list)
    recovered_substrings: list[str] = Field(default_factory=list)
    """Path-substring patterns that mark a claim as "recovered" (e.g.
    files under ``/RECOVERED-*/`` directories). When at least one
    recovered + one non-recovered claim coexist for the same permanode,
    the reconciler always picks a non-recovered keeper regardless of
    tier priority — the recovered claim(s) get stashed. When ALL claims
    in a group are recovered, tier priority decides as usual.

    Ported from sprawl-audit/scripts/recovered_retire.py — bundled
    ``policies/recovered.yml`` populates this list."""


class RetentionPolicy(_PolicyBase):
    """Aggregate retention policy: exclusions + dedup-retire matrix."""

    kind: Literal["RetentionPolicy"] = "RetentionPolicy"
    exclusions: RetentionExclusions
    dedup_retire: DedupRetire


# ────────────────────────────── Classification ──────────────────────────────


class DomainRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path_substring_any_of: list[str]


class DomainEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    rules: list[DomainRule]


class ClusterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    regex_any_of: list[str]


class ClassificationPolicy(_PolicyBase):
    kind: Literal["ClassificationPolicy"] = "ClassificationPolicy"
    domains: list[DomainEntry]
    clusters: list[ClusterEntry] = Field(default_factory=list)


# ────────────────────────────── Promotion ──────────────────────────────────


class PathTranslation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    src: str = Field(alias="from")
    dst: str = Field(alias="to")


class PromotionDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path_translations: list[PathTranslation] = Field(default_factory=list)
    source_tier: Tier = "Backup"
    copy_strategy: Literal["stream-hash-rename"] = "stream-hash-rename"
    hash_algo: Literal["blake3", "sha256"] = "blake3"
    preserve_mtime: bool = True
    on_dst_exists_size_match_hash_match: Literal["skip-ok", "error"] = "skip-ok"
    on_dst_exists_size_match_hash_mismatch: Literal["skip-ok", "error"] = "error"
    on_dst_exists_size_mismatch: Literal["skip-ok", "error"] = "error"
    mirror_strip_prefix: str | None = None
    """Default mirror-path behavior: when a phase doesn't set ``mirror_from``,
    strip this prefix from the (translated) source path and append the
    remainder under the phase's ``destination_root``. Typical value:
    ``/Volumes/Backup/`` — so a source under the Backup share preserves its
    subdir structure at the destination. When ``None`` and the phase doesn't
    set ``mirror_from``, fall back to ``basename(source)`` (flat copy)."""


class PromotionPhase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    match: dict[str, str]
    destination_root: str
    max_files: int | None = Field(default=None, ge=1)
    mirror_from: str | None = None
    """Sentinel substring. When set, the destination preserves everything in
    the (translated) source path AFTER the last occurrence of this substring.
    Example: with ``mirror_from: "Photos/"`` and source
    ``/Volumes/Backup/Clones/Mac/Photos/2024/IMG_001.jpg``, the destination is
    ``<destination_root>/2024/IMG_001.jpg``. Overrides
    ``defaults.mirror_strip_prefix`` for this phase."""


class PromotionPolicy(_PolicyBase):
    kind: Literal["PromotionPolicy"] = "PromotionPolicy"
    defaults: PromotionDefaults
    phases: list[PromotionPhase]


# ────────────────────────────── Replication ──────────────────────────────


class ReplicationDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rclone_bin: str = "rclone"
    """Path or basename of the ``rclone`` binary. Defaults to ``rclone``
    (relies on ``PATH``); operator can override per-host via the policy
    YAML if they keep rclone in a non-standard location."""

    timeout_seconds: int = Field(default=3600, ge=60)
    """Hard cap on a single rclone subprocess. Long replications should
    bump this; defaults to one hour."""

    transfers: int = Field(default=4, ge=1)
    """``rclone --transfers`` value."""

    checkers: int = Field(default=8, ge=1)
    """``rclone --checkers`` value."""

    extra_args: list[str] = Field(default_factory=list)
    """Catch-all for rclone flags Steward doesn't model directly
    (e.g. ``--bwlimit 10M``). Appended to every rclone invocation."""


class ReplicationSource(BaseModel):
    """One source → destination pair for ``steward replicate run``.

    Steward invokes ``rclone copy`` (additive, never deletes from dest)
    or ``rclone sync`` (mirror, deletes from dest) per the ``mode`` flag.
    """

    model_config = ConfigDict(extra="forbid")
    name: str
    """Human-readable label. Appears in audit_log entries + CLI output."""

    source: str
    """Source path (or rclone remote spec). Anything ``rclone`` accepts."""

    destination: str
    """Destination path (or rclone remote spec, e.g. ``b2:bucket/path``)."""

    mode: Literal["copy", "sync"] = "copy"
    """``copy`` is additive — never deletes from dest. ``sync`` mirrors —
    deletes from dest anything not in source. Default ``copy`` because
    deletion is the more surprising behaviour."""

    excludes: list[str] = Field(default_factory=list)
    """rclone ``--exclude`` patterns. e.g. ``"*.tmp"`` or
    ``"**/.DS_Store"``. Matches rclone's filter rule syntax."""

    includes: list[str] = Field(default_factory=list)
    """rclone ``--include`` patterns. Applied BEFORE excludes per rclone
    semantics. Useful for opt-in subsets of a larger tree."""

    enabled: bool = True
    """Toggle a source on/off without removing it from the policy."""


class ReplicationPolicy(_PolicyBase):
    """Replicate selected tiers / paths to off-machine targets via rclone.

    Each :class:`ReplicationSource` runs as a single ``rclone copy`` or
    ``rclone sync`` subprocess; the run is bracketed by a
    ``replicate_start`` / ``replicate_end`` pair in the audit log.

    Per ADR-0002 (operator-in-the-loop), the CLI requires explicit
    ``--dry-run`` or ``--execute``. ``--dry-run`` passes ``--dry-run``
    through to rclone so neither side mutates state.
    """

    kind: Literal["ReplicationPolicy"] = "ReplicationPolicy"
    defaults: ReplicationDefaults = Field(default_factory=ReplicationDefaults)
    sources: list[ReplicationSource]


# ────────────────────────────── Archive (restic) ──────────────────────────────


class ArchiveDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    restic_bin: str = "restic"
    """Path or basename of the ``restic`` binary."""

    timeout_seconds: int = Field(default=7200, ge=60)
    """Hard cap on a single restic subprocess. Default 2 h — restic
    backups can take a while on large trees."""

    password_command: str | None = None
    """Shell command that outputs the restic repository password on
    stdout. Passed to restic via ``RESTIC_PASSWORD_COMMAND``. Steward
    never reads/echoes the password itself.

    Typical macOS recipe (Keychain-backed):
    ``security find-generic-password -s steward-restic -w``.
    """

    password_file: str | None = None
    """Path to a file containing the restic password. Passed via
    ``RESTIC_PASSWORD_FILE``. Mutually exclusive with
    ``password_command``."""

    extra_args: list[str] = Field(default_factory=list)
    """Catch-all for restic flags Steward doesn't model
    (e.g. ``--limit-upload 10240``). Appended to every restic
    invocation after the subcommand-specific args."""


class ArchiveSource(BaseModel):
    """One source → repository pair for ``steward archive snapshot``."""

    model_config = ConfigDict(extra="forbid")
    name: str
    """Human-readable label. Appears in audit + CLI output."""

    source: str
    """Source path to back up. Absolute paths preferred so restic
    stores absolute paths in the snapshot (makes ``restore`` saner)."""

    repository: str
    """Restic repository spec. Path (``/Volumes/Backup/_steward-archive``)
    or remote (``b2:bucket-name`` / ``sftp:host:/path``)."""

    tags: list[str] = Field(default_factory=list)
    """Restic ``--tag`` values. ``snapshots --tag`` can filter on these
    later. Steward also injects the source ``name`` automatically."""

    excludes: list[str] = Field(default_factory=list)
    """Restic ``--exclude`` patterns. Restic's pattern syntax (gitignore-
    style) — e.g. ``"*.tmp"``, ``"**/__pycache__"``."""

    exclude_caches: bool = True
    """Pass ``--exclude-caches``. Restic skips dirs tagged
    ``CACHEDIR.TAG`` — a small win on Mac systems with build trees."""

    enabled: bool = True
    """Toggle a source on/off without removing it from the policy."""


class ArchivePolicy(_PolicyBase):
    """Encrypted, deduplicated archive via restic.

    Each :class:`ArchiveSource` becomes one ``restic backup`` invocation
    (with ``restic init`` on first use when the operator opts in).
    Snapshots are content-addressable + deduplicated across runs;
    Steward's role is policy + audit, not data transport.
    """

    kind: Literal["ArchivePolicy"] = "ArchivePolicy"
    defaults: ArchiveDefaults = Field(default_factory=ArchiveDefaults)
    sources: list[ArchiveSource]
