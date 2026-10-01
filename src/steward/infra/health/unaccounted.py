# SPDX-License-Identifier: Apache-2.0

"""Measure the df-vs-reachable gap for a volume (opt-in; slow).

The 2026-08-16 incident in one number: the boot volume reported 743 GiB
allocated while a root-privileged ``du`` could reach 293 GiB. The 441 GiB
difference lived in a DataVault (nsurlsessiond transfer staging, fed by a
looping File Provider domain) that no userland traversal may enter — so every
directory listing looked innocent for the 46 days the gap grew. Locating it
by hand took twelve falsified hypotheses; this module automates the
measurement so the MAGNITUDE alarms long before the volume is hours from
full.

Two deliberate design points:

- ``du -x -s -k`` in a subprocess rather than an os.walk: du is decades of
  edge-case handling (firmlinks, clones, sockets) and ``-x`` pins the walk to
  one device, which is the semantic this comparison needs.
- The walk is MINUTES-slow on a large volume, which is why the collector is
  opt-in (``--unaccounted``) and scheduled weekly rather than run on every
  gate. Absence is rendered as a ``skipped`` check, never a healthy one.

The gap can never be fully attributed from userland — that is the point of
the check. When it alarms, the runbook is
``docs/field-notes-2026-08-16-boot-volume-hidden-consumption.md``.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from steward.core.health.evaluate import unaccounted_level
from steward.core.health.model import UnaccountedSpace
from steward.core.health.thresholds import DEFAULT_THRESHOLDS, HealthThresholds
from steward.infra.observability.swallowed import log_swallowed_error

_WALK_TIMEOUT_S = 3600


def collect_unaccounted(
    root: str | Path,
    *,
    thresholds: HealthThresholds | None = None,
    timeout_s: int = _WALK_TIMEOUT_S,
) -> UnaccountedSpace:
    """Compare ``statfs`` used bytes against a same-device ``du`` walk.

    Always returns a section (the caller decides whether to run it at all);
    measurement failures surface on ``error``/``level``, never as raises.
    """
    thr = thresholds or DEFAULT_THRESHOLDS
    target = Path(root)
    label = str(target)

    try:
        usage = shutil.disk_usage(target)
        used_bytes = int(usage.used)
    except OSError as exc:
        log_swallowed_error("health.unaccounted.statfs", exc, context={"root": label})
        return UnaccountedSpace(
            root=label, error=repr(exc), level="unknown",
            message="volume not statable",
        )

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            ["du", "-x", "-s", "-k", label],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        log_swallowed_error("health.unaccounted.timeout", exc, context={"root": label})
        return UnaccountedSpace(
            root=label, used_bytes=used_bytes, error=f"du timed out after {timeout_s}s",
            level="unknown", message="walk did not complete",
        )
    except Exception as exc:  # noqa: BLE001 — best-effort health section
        log_swallowed_error("health.unaccounted.run", exc, context={"root": label})
        return UnaccountedSpace(
            root=label, used_bytes=used_bytes, error=repr(exc), level="unknown",
            message="walk could not run",
        )
    walk_seconds = round(time.monotonic() - t0, 1)

    # du exits non-zero when ANY subtree was unreadable — which on a volume
    # with DataVaults is every single run, and is precisely the condition
    # this check exists to quantify. The total on stdout is still the honest
    # "reachable" figure, so parse it regardless of exit code and fail only
    # when there is no total at all.
    try:
        reachable_bytes = int(proc.stdout.split()[0]) * 1024
    except (ValueError, IndexError):
        return UnaccountedSpace(
            root=label, used_bytes=used_bytes, walk_seconds=walk_seconds,
            error=(proc.stderr.strip()[:200] or "no du total"),
            level="unknown", message="walk produced no total",
        )

    gap_bytes = max(0, used_bytes - reachable_bytes)
    level = unaccounted_level(gap_bytes, thresholds=thr)
    return UnaccountedSpace(
        root=label,
        used_bytes=used_bytes,
        reachable_bytes=reachable_bytes,
        gap_bytes=gap_bytes,
        walk_seconds=walk_seconds,
        level=level,
        message=(
            f"{gap_bytes / 1024**3:.0f} GiB unreachable by traversal"
            if level in ("warn", "fail")
            else ""
        ),
    )


__all__ = ["collect_unaccounted"]
