# SPDX-License-Identifier: Apache-2.0

"""Health snapshot JSONL series under the data dir (ADR-0017).

Layout::

    <STEWARD_DATA_DIR>/
      health/
        snapshots.jsonl    # one compact EstateHealthReport dict per line
        LATEST             # ISO-8601 id of last written snapshot

Telemetry only — not forensic claim truth. Writers avoid inventory.db
write locks. Optional meta key ``health_snapshot_latest`` mirrors the ISO
timestamp only (not the full series).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steward.core.health.model import EstateHealthReport
from steward.infra.observability.swallowed import log_swallowed_error


def estate_health_to_snapshot_dict(
    report: EstateHealthReport, *, compact: bool = True
) -> dict[str, Any]:
    from steward.infra.health.collect import estate_health_to_snapshot_dict as _impl

    return _impl(report, compact=compact)


def estate_health_to_dict(report: EstateHealthReport) -> dict[str, Any]:
    from steward.infra.health.collect import estate_health_to_dict as _impl

    return _impl(report)

_MAX_LINES = 500
_MAX_AGE_DAYS = 90
META_KEY_LATEST = "health_snapshot_latest"
SNAPSHOTS_FILENAME = "snapshots.jsonl"
LATEST_FILENAME = "LATEST"


def health_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "health"


def snapshots_path(data_dir: Path) -> Path:
    return health_dir(data_dir) / SNAPSHOTS_FILENAME


def latest_pointer_path(data_dir: Path) -> Path:
    return health_dir(data_dir) / LATEST_FILENAME


def read_latest_pointer(*, data_dir: Path) -> str | None:
    """Return ISO id from LATEST sidecar, or None."""
    path = latest_pointer_path(data_dir)
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text or None
    except OSError as exc:
        log_swallowed_error(
            "health.snapshots.read_latest",
            exc,
            context={"path": str(path)},
        )
        return None


def write_health_snapshot(
    report: EstateHealthReport,
    *,
    data_dir: Path,
    compact: bool = True,
    db_path: Path | None = None,
    update_meta: bool = False,
    max_lines: int = _MAX_LINES,
    max_age_days: int = _MAX_AGE_DAYS,
) -> Path:
    """Append one compact snapshot line; update LATEST; prune retention.

    Returns the snapshots.jsonl path. Prune failures are swallowed
    (log_swallowed_error) and leave the series intact. Does not mutate
    inventory tiers or audit_log.
    """
    from steward.infra.health.collect import estate_health_to_snapshot_dict

    hdir = health_dir(data_dir)
    hdir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(hdir, 0o700)
    except OSError as exc:
        log_swallowed_error(
            "health.snapshots.chmod_dir",
            exc,
            context={"path": str(hdir)},
        )

    path = snapshots_path(data_dir)
    payload = estate_health_to_snapshot_dict(report, compact=compact)
    line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")

    latest = latest_pointer_path(data_dir)
    generated_at = str(payload.get("generated_at") or report.generated_at)
    try:
        latest.write_text(generated_at + "\n", encoding="utf-8")
    except OSError as exc:
        log_swallowed_error(
            "health.snapshots.write_latest",
            exc,
            context={"path": str(latest)},
        )

    try:
        _prune_series(path, max_lines=max_lines, max_age_days=max_age_days)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "health.snapshots.prune",
            exc,
            context={"path": str(path)},
        )

    if update_meta and db_path is not None:
        _maybe_set_meta_latest(db_path=db_path, generated_at=generated_at)

    return path


def write_quick_health_snapshot(
    *,
    db_path: Path,
    data_dir: Path | None = None,
    probes: bool = True,
    include_imports: bool = False,
    refresh_rollups: bool = False,
) -> Path | None:
    """Collect a quick estate-health report and append a compact snapshot.

    Used by ``steward status --refresh`` and ``health check --write-snapshot``.
    Returns series path, or None on hard failure (logged).
    """
    try:
        from steward.infra.health.collect import collect_estate_health

        report = collect_estate_health(
            db_path=db_path,
            quick=True,
            probes=probes,
            include_imports=include_imports,
            refresh_rollups=refresh_rollups,
        )
        if data_dir is None:
            try:
                data_dir = Path(db_path).expanduser().resolve().parent
            except OSError:
                from steward.infra.db.settings import data_dir as default_data_dir

                data_dir = default_data_dir()
        return write_health_snapshot(
            report,
            data_dir=data_dir,
            compact=True,
            db_path=db_path,
            update_meta=True,
        )
    except Exception as exc:  # noqa: BLE001 — status refresh must not fail on snapshot
        log_swallowed_error(
            "health.snapshots.write_quick",
            exc,
            context={"db_path": str(db_path)},
        )
        return None


def read_health_series(*, data_dir: Path, limit: int = 48) -> list[dict[str, Any]]:
    """Return the last ``limit`` compact snapshot points (oldest first)."""
    path = snapshots_path(data_dir)
    if not path.exists():
        return []
    if limit <= 0:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        log_swallowed_error("health.snapshots.read", exc, context={"path": str(path)})
        return []
    out: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            log_swallowed_error(
                "health.snapshots.parse",
                exc,
                context={"path": str(path)},
            )
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _maybe_set_meta_latest(*, db_path: Path, generated_at: str) -> None:
    if not Path(db_path).is_file():
        return
    try:
        from steward.infra.db import repo_meta
        from steward.infra.db.connect import connect

        con = connect(Path(db_path), read_only=False, load_vec=False)
        try:
            repo_meta.set_(con, META_KEY_LATEST, generated_at)
            con.commit()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — meta mirror is optional
        log_swallowed_error(
            "health.snapshots.meta_latest",
            exc,
            context={"db_path": str(db_path), "generated_at": generated_at},
        )


def _prune_series(
    path: Path,
    *,
    max_lines: int = _MAX_LINES,
    max_age_days: int = _MAX_AGE_DAYS,
) -> None:
    """Keep last max_lines or drop points older than max_age_days. Best-effort."""
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    now = datetime.now(timezone.utc)
    kept: list[str] = []
    for raw in lines:
        raw_s = raw.strip()
        if not raw_s:
            continue
        try:
            obj = json.loads(raw_s)
            gen = str(obj.get("generated_at") or "")
            if gen:
                ts = gen.replace("Z", "+00:00")
                when = datetime.fromisoformat(ts)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                age_days = (now - when).total_seconds() / 86400.0
                if age_days > max_age_days:
                    continue
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        kept.append(raw_s)
    kept = kept[-max_lines:]
    original_n = len([ln for ln in lines if ln.strip()])
    if len(kept) < original_n:
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        tmp.replace(path)


__all__ = [
    "LATEST_FILENAME",
    "META_KEY_LATEST",
    "SNAPSHOTS_FILENAME",
    "health_dir",
    "latest_pointer_path",
    "read_health_series",
    "read_latest_pointer",
    "snapshots_path",
    "write_health_snapshot",
    "write_quick_health_snapshot",
]
