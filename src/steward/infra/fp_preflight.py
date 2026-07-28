# SPDX-License-Identifier: Apache-2.0

"""Apply-time FP health gate for manifests that touch cloud-FP tiers."""
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
    """Return human-readable problems if FP state is unsafe for cloud retire.

    Local-only reclaim (``prefer_mount_unlink=False``) only checks that
    the store root is present.
    """
    report = collect_fp_status()
    problems: list[str] = []
    if prefer_mount_unlink:
        if not report.mount.exists:
            problems.append(
                f"CloudStorage mount missing or unstatable: {report.mount_root}"
                + (f" ({report.mount.error})" if report.mount.error else "")
            )
        if report.mount.error:
            problems.append(
                f"Mount stat error (FP may be congested): {report.mount.error}"
            )
        if report.forked_devices:
            problems.append(
                "Store and mount appear on different devices or one side is "
                "missing (forked Dropbox materializations). Cloud-propagating "
                "retire is unsafe without mount-present objects. Rescan the "
                "mount, or use --allow-store-path-unlink for local-only reclaim. "
                "Tree rectification is a separate workstream."
            )
        if report.sample_store_only and not report.sample_both:
            problems.append(
                "Sample dual-presence is store-only — mount may not have live "
                "twins for store-path inventory claims."
            )
    else:
        if not report.store.exists:
            problems.append(
                f"Dropbox store root missing: {report.store_root}"
                + (f" ({report.store.error})" if report.store.error else "")
            )
    return problems


__all__ = ["fp_health_problems", "manifest_needs_fp_health"]
