# SPDX-License-Identifier: Apache-2.0

"""Live mount / volume probes for estate health (ADR-0017).

Cheap only: ``Path.exists`` / ``stat`` / ``shutil.disk_usage`` — no
recursive walks, no ``fileproviderctl dump``.

Best-effort: missing mounts and OSError become probe levels/errors —
never raise out of the collector path (Linux open-core CI and hosts
without macOS volume layout must stay green).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Sequence

from steward.core.health.evaluate import free_space_level, latency_level, worst_level
from steward.core.health.model import HealthLevel, MountProbe
from steward.core.health.thresholds import DEFAULT_THRESHOLDS, HealthThresholds
from steward.core.tiers import classify_tier
from steward.infra.observability.swallowed import log_swallowed_error

# Well-known macOS dogfood volume roots (presence is best-effort).
_DEFAULT_TIER_ROOTS: tuple[tuple[str, str, bool], ...] = (
    ("/Volumes/Level 1", "L1", False),
    ("/Volumes/Level 1w", "L1w", False),
    ("/Volumes/Level 2", "L2", False),
    ("/Volumes/Level_3a", "L3a", False),
    ("/Volumes/Backup", "Backup", False),
    ("/Volumes/DropboxStorage", "DropboxStorage", True),
    ("/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox", "DropboxStorage", True),
)

_DEFAULT_STORE = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox"

RootSpec = tuple[str, str | None] | tuple[str, str | None, bool]


def probe_one(
    root: str,
    *,
    tier: str | None = None,
    critical: bool = False,
    thresholds: HealthThresholds | None = None,
) -> MountProbe:
    """Probe a single root for presence, free/total bytes, sample latency.

    Never raises: OSError / unexpected failures surface on ``error`` /
    ``level``.
    """
    thr = thresholds or DEFAULT_THRESHOLDS
    path = Path(root)
    error: str | None = None
    present = False
    free_bytes: int | None = None
    total_bytes: int | None = None
    latency_ms: float | None = None
    message = ""

    if tier is None:
        root_for_tier = root if root.endswith("/") else root + "/"
        tier_name, _ = classify_tier(root_for_tier)
        if tier_name == "unknown":
            tier_name, _ = classify_tier(root)
        tier = tier_name if tier_name != "unknown" else None

    t0 = time.perf_counter()
    try:
        present = path.exists()
        if present:
            _ = path.stat()
        latency_ms = (time.perf_counter() - t0) * 1000.0
    except OSError as exc:
        error = repr(exc)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        present = False
        message = error
    except Exception as exc:  # noqa: BLE001 — probe path must not break collectors
        log_swallowed_error(
            "health.probes.probe_one",
            exc,
            context={"root": root},
        )
        error = repr(exc)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        present = False
        message = error

    if present:
        try:
            usage = shutil.disk_usage(path)
            free_bytes = int(usage.free)
            total_bytes = int(usage.total)
        except OSError as exc:
            error = repr(exc)
            message = error
            log_swallowed_error(
                "health.probes.disk_usage",
                exc,
                context={"root": root},
            )

    if not present:
        level: HealthLevel = "fail" if critical else "warn"
        if not message:
            message = "path missing" if critical else "path not present"
    else:
        space_lv = free_space_level(free_bytes, total_bytes, thresholds=thr)
        lat_lv = latency_level(latency_ms, thresholds=thr)
        level = worst_level([space_lv, lat_lv, "ok"])
        if level == "warn" and not message:
            message = "low free space or high sample latency"

    return MountProbe(
        root=root,
        tier=tier,
        present=present,
        free_bytes=free_bytes,
        total_bytes=total_bytes,
        sample_latency_ms=round(latency_ms, 3) if latency_ms is not None else None,
        error=error,
        level=level,
        message=message,
    )


def probe_mount(
    root: str | Path,
    *,
    tier: str | None = None,
    critical: bool = False,
    thresholds: HealthThresholds | None = None,
) -> MountProbe:
    """Alias for :func:`probe_one` accepting ``Path``."""
    return probe_one(str(root), tier=tier, critical=critical, thresholds=thresholds)


def _home_path(home: Path | None) -> Path:
    if home is not None:
        return home.expanduser()
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser()


def _dropbox_roots(home: Path) -> list[tuple[str, str | None, bool]]:
    out: list[tuple[str, str | None, bool]] = [
        (_DEFAULT_STORE, "DropboxStorage", True),
        (str(home / "Library" / "CloudStorage" / "Dropbox"), "DropboxStorage", True),
    ]
    info = home / ".dropbox" / "info.json"
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
        personal = data.get("personal") if isinstance(data, dict) else None
        if isinstance(personal, dict):
            p = personal.get("path")
            if isinstance(p, str) and p:
                out.append((p, "DropboxStorage", True))
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError) as exc:
        log_swallowed_error(
            "health.probes.dropbox_info",
            exc,
            context={"path": str(info)},
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "health.probes.dropbox_info",
            exc,
            context={"path": str(info)},
        )
    return out


def _roots_from_tiers_table(con: sqlite3.Connection) -> list[tuple[str, str | None, bool]]:
    out: list[tuple[str, str | None, bool]] = []
    try:
        rows = con.execute("SELECT name, path_prefixes FROM tiers").fetchall()
    except sqlite3.Error as exc:
        log_swallowed_error("health.probes.tiers_table", exc)
        return out
    for name, raw in rows:
        tier = str(name) if name else None
        critical = tier == "DropboxStorage"
        prefixes: list[str] = []
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    prefixes = [str(p) for p in parsed if p]
            except json.JSONDecodeError as exc:
                log_swallowed_error(
                    "health.probes.tiers_prefixes_json",
                    exc,
                    context={"tier": tier},
                )
                prefixes = [p.strip() for p in text.split(",") if p.strip()]
        else:
            prefixes = [p.strip() for p in text.split(",") if p.strip()]
        for p in prefixes:
            out.append((p, tier, critical))
    return out


def _roots_from_scan_runs(con: sqlite3.Connection) -> list[tuple[str, str | None, bool]]:
    out: list[tuple[str, str | None, bool]] = []
    try:
        rows = con.execute(
            """
            SELECT DISTINCT root_path FROM scan_runs
            WHERE finished_at IS NOT NULL AND root_path IS NOT NULL
            ORDER BY root_path
            """
        ).fetchall()
    except sqlite3.Error as exc:
        log_swallowed_error("health.probes.scan_roots", exc)
        return out
    for (root,) in rows:
        if not root:
            continue
        root_s = str(root)
        tier_name, _ = classify_tier(root_s if root_s.endswith("/") else root_s + "/")
        if tier_name == "unknown":
            tier_name, _ = classify_tier(root_s)
        tier = tier_name if tier_name != "unknown" else None
        out.append((root_s, tier, tier == "DropboxStorage"))
    return out


def _normalize_root_spec(item: RootSpec | Sequence[object]) -> tuple[str, str | None, bool]:
    if len(item) == 2:
        root, tier = item[0], item[1]
        tier_s = str(tier) if tier is not None else None
        return str(root), tier_s, tier_s == "DropboxStorage"
    root, tier, critical = item[0], item[1], item[2]
    tier_s = str(tier) if tier is not None else None
    return str(root), tier_s, bool(critical)


def discover_mount_roots(
    *,
    db_path: Path | None = None,
    home: Path | None = None,
    extra_roots: Sequence[RootSpec] | None = None,
    include_defaults: bool = True,
    include_dropbox: bool = True,
    include_home: bool = True,
) -> list[tuple[str, str | None, bool]]:
    """Ordered unique ``(root, tier, critical)`` candidates to probe.

    Missing mounts are expected on Linux CI / open-core hosts — discovery
    itself never raises.
    """
    seen: set[str] = set()
    ordered: list[tuple[str, str | None, bool]] = []

    def _add(root: str, tier: str | None, critical: bool) -> None:
        key = os.path.normpath(root)
        if not root or key in seen:
            return
        seen.add(key)
        ordered.append((root, tier, critical))

    if extra_roots:
        for item in extra_roots:
            r, t, c = _normalize_root_spec(item)
            _add(r, t, c)

    home_p = _home_path(home)
    if include_home:
        _add(str(home_p), "boot", False)
    if include_defaults:
        for r, t, c in _DEFAULT_TIER_ROOTS:
            _add(r, t, c)
        volumes = Path("/Volumes")
        if volumes.is_dir():
            try:
                for child in sorted(volumes.iterdir())[:8]:
                    if child.name.startswith("."):
                        continue
                    _add(str(child), None, False)
            except OSError as exc:
                log_swallowed_error("health.probes.list_volumes", exc, context={})
    if include_dropbox:
        for r, t, c in _dropbox_roots(home_p):
            _add(r, t, c)

    if db_path is not None and Path(db_path).is_file():
        try:
            uri = Path(db_path).resolve().as_uri() + "?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            try:
                for r, t, c in _roots_from_tiers_table(con):
                    _add(r, t, c)
                for r, t, c in _roots_from_scan_runs(con):
                    _add(r, t, c)
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001 — discovery is best-effort
            log_swallowed_error(
                "health.probes.discover_db",
                exc,
                context={"db_path": str(db_path)},
            )

    return ordered


def probe_mounts(
    roots: Sequence[RootSpec] | None = None,
    *,
    max_probes: int = 12,
    max_roots: int | None = None,
    thresholds: HealthThresholds | None = None,
    db_path: Path | None = None,
    home: Path | None = None,
    extra_roots: Sequence[RootSpec] | None = None,
) -> list[MountProbe]:
    """Probe up to ``max_probes`` volume/tier paths. Never raises.

    Primary name used by :mod:`steward.infra.health.collect`.
    """
    thr = thresholds or DEFAULT_THRESHOLDS
    cap = max_roots if max_roots is not None else max_probes
    if roots is None:
        candidates = discover_mount_roots(
            db_path=db_path,
            home=home,
            extra_roots=extra_roots,
        )
    else:
        candidates = [_normalize_root_spec(x) for x in roots]
        if extra_roots:
            candidates = [_normalize_root_spec(x) for x in extra_roots] + candidates
    out: list[MountProbe] = []
    for root, tier, critical in candidates[: max(0, cap)]:
        out.append(probe_one(root, tier=tier, critical=critical, thresholds=thr))
    return out


# Aliases for ADR / tests / alternate call sites.
probe_mount_roots = probe_mounts
collect_mount_probes = probe_mounts


def default_probe_roots(
    *,
    home: Path | None = None,
    include_dropbox: bool = True,
) -> list[tuple[str, str | None]]:
    """Default ``(root, tier)`` pairs for probing (no critical flag)."""
    return [(r, t) for r, t, _ in discover_mount_roots(home=home, include_dropbox=include_dropbox)]


def mount_warn_reasons(
    probe: MountProbe,
    *,
    thresholds: HealthThresholds | None = None,
) -> list[str]:
    """Human/token reasons a mount probe is not fully healthy."""
    thr = thresholds or DEFAULT_THRESHOLDS
    reasons: list[str] = []
    if not probe.present:
        reasons.append("missing")
        return reasons
    if probe.error:
        reasons.append(f"error:{probe.error}")
    free = probe.free_bytes
    total = probe.total_bytes
    if free is not None and free < thr.free_bytes_min:
        reasons.append(f"low_free:{free}")
    if free is not None and total is not None and total > 0:
        ratio = free / float(total)
        if ratio < thr.free_ratio_min:
            reasons.append(f"low_free_ratio:{ratio:.4f}")
    if (
        probe.sample_latency_ms is not None
        and probe.sample_latency_ms > thr.sample_latency_warn_ms
    ):
        reasons.append(f"high_latency_ms:{probe.sample_latency_ms:.1f}")
    return reasons


__all__ = [
    "collect_mount_probes",
    "default_probe_roots",
    "discover_mount_roots",
    "mount_warn_reasons",
    "probe_mount",
    "probe_mount_roots",
    "probe_mounts",
    "probe_one",
]
