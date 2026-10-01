# SPDX-License-Identifier: Apache-2.0

"""Compare this host's live mounts with what the estate declares for it."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from steward.core.estate import Estate

FindingLevel = Literal["problem", "warning"]
FindingKind = Literal[
    "missing_mount",
    "unexpected_mount",
    "forbidden_mount",
    "unknown_host",
    "data_dir_unmounted",
    "binding",
]


@dataclass(frozen=True, slots=True)
class Finding:
    level: FindingLevel
    kind: FindingKind
    message: str
    path: str | None = None
    volume_id: str | None = None


def volumes_mounts(root: Path = Path("/Volumes")) -> set[str]:
    """Mountpoints directly under ``/Volumes`` (symlinks such as the boot-volume alias are not mounts)."""
    try:
        entries = list(root.iterdir())
    except OSError:
        return set()
    return {str(entry) for entry in entries if os.path.ismount(entry)}


def _is_volumes_mount(path: str) -> bool:
    parts = Path(path).parts
    return len(parts) == 3 and parts[:2] == ("/", "Volumes")


def declared_paths(estate: Estate, host_id: str) -> set[str]:
    """Paths the estate accounts for on ``host_id``: its mount paths and aliases, plus every ``probe_skip``."""
    declared: set[str] = set()
    for vol in estate.volumes.values():
        declared.update(vol.probe_skip)
        mount = vol.mounts.get(host_id)
        if mount is None:
            continue
        declared.add(mount.path)
        declared.update(alias.rstrip("/") for alias in vol.aliases)
    return declared


def check_mounts(estate: Estate, host_id: str, *, live: set[str] | None = None) -> list[Finding]:
    """Missing declared mounts, mounted ``forbid`` volumes, and undeclared ``/Volumes`` mounts.

    ``live`` is the set of mounted ``/Volumes/<name>`` paths (default: read
    from the system). A declared ``/Volumes/<name>`` mount must be in it; any
    other declared path just has to exist. A missing ``critical`` mount is a
    problem, a missing ``normal`` one a warning. Undeclared mounts (a USB
    stick, a share nobody listed) are warnings; ``ignore`` mounts and
    ``probe_skip`` paths are never reported.
    """
    mounted = volumes_mounts() if live is None else live
    findings: list[Finding] = []
    for vol_id, vol in estate.volumes.items():
        mount = vol.mounts.get(host_id)
        if mount is None:
            continue
        if mount.path == "/" or mount.access == "ignore":
            continue
        present = mount.path in mounted if _is_volumes_mount(mount.path) else os.path.exists(mount.path)
        if mount.access == "forbid":
            if present:
                findings.append(
                    Finding(
                        "problem",
                        "forbidden_mount",
                        f"{mount.path} must not be mounted on {host_id!r}",
                        mount.path,
                        vol_id,
                    )
                )
            continue
        if not present:
            level: FindingLevel = "problem" if mount.criticality == "critical" else "warning"
            findings.append(
                Finding(
                    level, "missing_mount", f"{mount.path} ({mount.criticality}) is not mounted", mount.path, vol_id
                )
            )
    for path in sorted(mounted - declared_paths(estate, host_id)):
        findings.append(
            Finding("warning", "unexpected_mount", f"{path} is mounted but not declared for {host_id!r}", path)
        )
    return findings
