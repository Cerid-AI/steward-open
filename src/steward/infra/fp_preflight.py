# SPDX-License-Identifier: Apache-2.0

"""Apply-time FP health gate for manifests that touch cloud-FP tiers.

Uses :func:`steward.infra.fp_status.collect_fp_status` +
:class:`~steward.infra.fp_status.FPHealthVerdict`. External-drive File
Provider (different st_dev, residual Domains.plist unlinked metadata)
is **not** a hard fail by itself — see field notes 2026-07-28.
"""

from __future__ import annotations

from pathlib import Path

from steward.core.manifest_io import read_manifest
from steward.core.tiers import CLOUD_FP_TIERS
from steward.infra.fp_status import collect_fp_status


def manifest_needs_fp_health(manifest_path: Path) -> bool:
    """True when the plan includes retire_direct or a cloud-FP source tier."""
    manifest = read_manifest(manifest_path)
    for row in manifest.rows:
        if row.action == "retire_direct":
            return True
        if row.source_tier in CLOUD_FP_TIERS:
            return True
    return False


def fp_health_problems(
    *,
    prefer_mount_unlink: bool = True,
) -> list[str]:
    """Return hard problems if FP state is unsafe for the chosen intent.

    * Cloud-propagating (``prefer_mount_unlink=True``): missing mount,
      mount stat errors, store-only dual samples, or hard domain disconnect
      without a healthy external-drive layout.
    * Local reclaim (``prefer_mount_unlink=False``): store root must exist.

    Warnings (forked devices, residual unlinked metadata, name divergence)
    are **not** returned here — they appear on ``steward fp status`` only.
    """
    report = collect_fp_status()
    verdict = report.verdict
    if prefer_mount_unlink:
        if verdict is not None:
            return list(verdict.problems)
        # Fallback if verdict missing (should not happen)
        problems: list[str] = []
        if not report.mount.exists:
            problems.append(f"CloudStorage mount missing or unstatable: {report.mount_root}")
        return problems

    # Local reclaim
    if not report.store.exists:
        return [
            f"Dropbox store root missing: {report.store_root}"
            + (f" ({report.store.error})" if report.store.error else "")
        ]
    if report.store.error:
        return [f"Store stat error: {report.store.error}"]
    return []


def fp_health_warnings(*, prefer_mount_unlink: bool = True) -> list[str]:
    """Non-blocking warnings for operators (fork, residual domain, names)."""
    report = collect_fp_status()
    if report.verdict is None:
        return []
    if prefer_mount_unlink:
        return list(report.verdict.warnings)
    # Local reclaim: surface name divergence still useful
    return [w for w in report.verdict.warnings if "basename" in w.lower() or "info.json" in w.lower()]


__all__ = [
    "fp_health_problems",
    "fp_health_warnings",
    "manifest_needs_fp_health",
]
