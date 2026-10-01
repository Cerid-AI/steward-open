# SPDX-License-Identifier: Apache-2.0

"""Read the health sidecars pulled from other estate hosts and grade them.

``steward fleet pull`` stages each client's published health directory at
``<data_dir>/inbox/<host>/health/``; the client's ``latest.json`` there is
graded by :func:`steward.core.health.remote.grade_remote_capacity`. Only the
estate's primary grades remote hosts — it is the host that pulls.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from steward.core.health.model import HealthCheckResult
from steward.core.health.remote import RemoteHostCapacity, check_remote_capacity, grade_remote_capacity
from steward.core.health.thresholds import DEFAULT_THRESHOLDS, HealthThresholds
from steward.infra.estate.active import active_estate
from steward.infra.health.snapshots import LATEST_REPORT_FILENAME
from steward.infra.observability import log_swallowed_error
from steward.infra.sync.pull import inbox_health_dir


def read_sidecar(path: Path) -> dict[str, Any] | None:
    """The pulled ``latest.json``; ``None`` when absent, ``{}`` when unreadable."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log_swallowed_error("health.remote.read_sidecar", exc, context={"path": str(path)})
        return {}
    return data if isinstance(data, dict) else {}


def collect_remote_capacity(
    *,
    data_dir: Path,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
    now: datetime | None = None,
) -> list[RemoteHostCapacity] | None:
    """Grade every publishing host's sidecar; ``None`` unless this host is the estate primary."""
    ctx = active_estate()
    if ctx.legacy or ctx.estate.hosts[ctx.host_id].role != "primary":
        return None
    estate, me = ctx.estate, ctx.host_id
    graded: list[RemoteHostCapacity] = []
    for host_id, host in estate.hosts.items():
        if host_id == me or host.publish is None or host.publish.health_dir is None:
            continue
        sidecar = read_sidecar(inbox_health_dir(data_dir, host_id) / LATEST_REPORT_FILENAME)
        graded.append(
            grade_remote_capacity(
                host_id,
                sidecar,
                max_age_hours=host.publish.max_age_hours,
                now=now,
                thresholds=thresholds,
            )
        )
    return graded


def remote_capacity_checks(
    *,
    data_dir: Path,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
    now: datetime | None = None,
) -> list[HealthCheckResult]:
    """The ``remote_capacity`` check on the primary; nothing elsewhere (or without an estate)."""
    try:
        hosts = collect_remote_capacity(data_dir=data_dir, thresholds=thresholds, now=now)
    except Exception as exc:  # noqa: BLE001 — best-effort health section
        log_swallowed_error("health.remote.collect", exc, context={"data_dir": str(data_dir)})
        return []
    return [] if hosts is None else [check_remote_capacity(hosts)]


__all__ = ["collect_remote_capacity", "read_sidecar", "remote_capacity_checks"]
