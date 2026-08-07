# SPDX-License-Identifier: Apache-2.0

"""Dashboard API: analysis payloads + gated operator actions.

Read paths reuse the same helpers as MCP/CLI. Write/plan paths call
orchestrators directly with ``actor=dashboard`` and require loopback
clients. Destructive actions need an explicit confirmation string so
the browser surface cannot one-click mutate the filesystem.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from steward.infra.observability import log_swallowed_error
from steward.infra.status import (
    StatusReport,
    collect_status,
    refresh_inventory_rollups,
    status_to_dict,
)

# Confirmation token operators must type for destructive dashboard actions.
CONFIRM_EXECUTE = "EXECUTE"

ActionFn = Callable[[Path, dict[str, Any]], dict[str, Any]]

# ADR-0017 foundation thresholds (dashboard cheap path; full health package may override).
_SCAN_MAX_AGE_HOURS = 168.0  # 7d
_ADAPTER_MAX_AGE_HOURS = 168.0
_STASH_COOLING_OFF_DAYS = 7.0
_STASH_GRACE_HOURS = 24.0
_ROLLUP_MAX_AGE_HOURS = 24.0

HealthLevel = Literal["ok", "warn", "fail", "unknown", "skipped"]

_LEVEL_RANK: dict[str, int] = {
    "ok": 0,
    "skipped": 0,
    "unknown": 1,
    "warn": 2,
    "fail": 3,
}


def _ok(**payload: Any) -> dict[str, Any]:
    out = {"ok": True}
    out.update(payload)
    return out


def _err(msg: str, **payload: Any) -> dict[str, Any]:
    out = {"ok": False, "error": msg}
    out.update(payload)
    return out


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        ts = str(value).replace("Z", "+00:00")
        when = datetime.fromisoformat(ts)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when
    except (TypeError, ValueError):
        return None


def _age_hours(iso: str | None, *, now: datetime | None = None) -> float | None:
    when = _parse_iso_dt(iso)
    if when is None:
        return None
    ref = now or datetime.now(timezone.utc)
    return max(0.0, (ref - when).total_seconds() / 3600.0)


def _worst_level(*levels: str) -> str:
    worst = "ok"
    worst_rank = -1
    for level in levels:
        rank = _LEVEL_RANK.get(level, 1)
        if rank > worst_rank:
            worst = level if level in _LEVEL_RANK else "unknown"
            worst_rank = rank
    return worst


def _check(
    name: str,
    level: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "level": level,
        "message": message,
        "details": details or {},
    }


def _score_from_checks(checks: list[dict[str, Any]]) -> int:
    """Composite 0–100 score: start 100, penalize warns/fails (foundation banner)."""
    score = 100
    for c in checks:
        level = str(c.get("level") or "unknown")
        if level == "fail":
            score -= 35
        elif level == "warn":
            score -= 12
        elif level == "unknown":
            score -= 4
        # skipped / ok: no penalty
    return max(0, min(100, score))


def posture_from_status_report(
    report: StatusReport,
    *,
    quick: bool = True,
    now: datetime | None = None,
    scan_max_age_hours: float = _SCAN_MAX_AGE_HOURS,
    adapter_max_age_hours: float = _ADAPTER_MAX_AGE_HOURS,
    stash_cooling_off_days: float = _STASH_COOLING_OFF_DAYS,
    stash_grace_hours: float = _STASH_GRACE_HOURS,
) -> dict[str, Any]:
    """Derive estate posture signals from a :class:`StatusReport`.

    Foundation path until ``steward.infra.health.collect_estate_health`` lands.
    Cheap and soft-poll friendly — pure evaluation over already-collected status.
    """
    ref = now or datetime.now(timezone.utc)
    checks: list[dict[str, Any]] = []
    signals: dict[str, Any] = {}

    # ── audit ──────────────────────────────────────────────
    audit = report.audit_chain
    if getattr(audit, "skipped", False):
        audit_level: str = "skipped"
        audit_msg = "audit chain not walked (quick mode)"
        checks.append(
            _check(
                "broken_audit",
                "skipped",
                audit_msg,
                details={"rows_checked": audit.rows_checked, "quick": True},
            )
        )
    elif not audit.ok:
        audit_level = "fail"
        audit_msg = f"audit chain broken: {audit.error or 'unknown error'}"
        checks.append(
            _check(
                "broken_audit",
                "fail",
                audit_msg,
                details={"rows_checked": audit.rows_checked, "error": audit.error},
            )
        )
    else:
        audit_level = "ok"
        audit_msg = f"audit chain ok ({audit.rows_checked:,} rows)"
        checks.append(
            _check(
                "broken_audit",
                "ok",
                audit_msg,
                details={"rows_checked": audit.rows_checked},
            )
        )
    signals["audit"] = {
        "level": audit_level,
        "message": audit_msg,
        "ok": audit.ok,
        "skipped": bool(getattr(audit, "skipped", False)),
        "rows_checked": audit.rows_checked,
    }

    # ── scan freshness ─────────────────────────────────────
    scan = report.latest_scan
    scan_age = _age_hours(scan.finished_at, now=ref)
    if scan.scan_run_id is None or not scan.finished_at:
        scan_level = "warn"
        scan_msg = "no finished scan yet"
        checks.append(
            _check(
                "stale_scan",
                "warn",
                scan_msg,
                details={"scan_run_id": None, "age_hours": None},
            )
        )
    elif scan_age is not None and scan_age > scan_max_age_hours:
        scan_level = "fail"
        scan_msg = (
            f"latest finished scan is {scan_age:.1f}h old "
            f"(threshold {scan_max_age_hours:.0f}h)"
        )
        checks.append(
            _check(
                "stale_scan",
                "fail",
                scan_msg,
                details={
                    "scan_run_id": scan.scan_run_id,
                    "finished_at": scan.finished_at,
                    "root_path": scan.root_path,
                    "age_hours": round(scan_age, 2),
                    "max_age_hours": scan_max_age_hours,
                },
            )
        )
    else:
        scan_level = "ok"
        age_part = f"{scan_age:.1f}h ago" if scan_age is not None else "age unknown"
        scan_msg = f"latest scan finished {age_part}"
        checks.append(
            _check(
                "stale_scan",
                "ok",
                scan_msg,
                details={
                    "scan_run_id": scan.scan_run_id,
                    "finished_at": scan.finished_at,
                    "root_path": scan.root_path,
                    "age_hours": round(scan_age, 2) if scan_age is not None else None,
                    "max_age_hours": scan_max_age_hours,
                },
            )
        )
    if scan.errors and scan_level == "ok":
        scan_level = "warn"
        scan_msg = f"latest scan reported {scan.errors} error(s)"
    signals["scan"] = {
        "level": scan_level,
        "message": scan_msg,
        "scan_run_id": scan.scan_run_id,
        "root_path": scan.root_path,
        "finished_at": scan.finished_at,
        "age_hours": round(scan_age, 2) if scan_age is not None else None,
        "errors": scan.errors,
    }

    # ── stash backlog ──────────────────────────────────────
    stash = report.stash
    stash_max_hours = stash_cooling_off_days * 24.0 + stash_grace_hours
    oldest_age = _age_hours(stash.oldest_ts_iso, now=ref)
    if quick and stash.in_flight_entries == 0 and stash.oldest_ts_iso is None:
        stash_level = "skipped"
        stash_msg = "stash summary skipped or empty (quick path)"
        checks.append(
            _check(
                "stash_overdue",
                "skipped",
                stash_msg,
                details={"quick": quick, "in_flight": 0},
            )
        )
    elif stash.in_flight_entries == 0:
        stash_level = "ok"
        stash_msg = "no in-flight stash entries"
        checks.append(_check("stash_overdue", "ok", stash_msg, details={"in_flight": 0}))
    elif oldest_age is not None and oldest_age > stash_max_hours:
        stash_level = "fail"
        stash_msg = (
            f"oldest in-flight stash is {oldest_age:.1f}h old "
            f"(cooling-off+grace {stash_max_hours:.0f}h); {stash.in_flight_entries} entries"
        )
        checks.append(
            _check(
                "stash_overdue",
                "fail",
                stash_msg,
                details={
                    "in_flight": stash.in_flight_entries,
                    "oldest_ts": stash.oldest_ts_iso,
                    "age_hours": round(oldest_age, 2),
                    "max_age_hours": stash_max_hours,
                },
            )
        )
    else:
        stash_level = "warn"
        age_bit = f", oldest {oldest_age:.1f}h" if oldest_age is not None else ""
        stash_msg = f"{stash.in_flight_entries} in-flight stash entr{'y' if stash.in_flight_entries == 1 else 'ies'}{age_bit}"
        checks.append(
            _check(
                "stash_overdue",
                "ok",
                "stash within cooling-off window" if oldest_age is not None else stash_msg,
                details={
                    "in_flight": stash.in_flight_entries,
                    "oldest_ts": stash.oldest_ts_iso,
                    "age_hours": round(oldest_age, 2) if oldest_age is not None else None,
                },
            )
        )
    signals["stash"] = {
        "level": stash_level,
        "message": stash_msg,
        "in_flight_entries": stash.in_flight_entries,
        "oldest_ts_iso": stash.oldest_ts_iso,
        "age_hours": round(oldest_age, 2) if oldest_age is not None else None,
    }

    # ── inventory / rollups ────────────────────────────────
    inv = report.inventory
    if inv.permanodes == 0 and inv.current_claims == 0 and inv.scan_runs == 0:
        inv_level = "warn"
        inv_msg = "empty inventory (no permanodes, claims, or scans)"
    else:
        inv_level = "ok"
        inv_msg = (
            f"{inv.permanodes:,} permanodes · {inv.current_claims:,} claims · "
            f"{inv.scan_runs:,} scan runs"
        )
    signals["inventory"] = {
        "level": inv_level,
        "message": inv_msg,
        "permanodes": inv.permanodes,
        "current_claims": inv.current_claims,
        "scan_runs": inv.scan_runs,
        "audit_entries": inv.audit_entries,
        "machines": inv.machines,
    }

    rollups = report.rollups
    if rollups is None:
        rollup_level = "unknown"
        rollup_msg = "rollup cache info unavailable"
        checks.append(
            _check("rollup_stale", "unknown", rollup_msg, details={})
        )
    elif rollups.used_cache:
        rollup_level = "ok"
        rollup_msg = f"inventory counts from rollup cache (refreshed {rollups.refreshed_at or '—'})"
        checks.append(
            _check(
                "rollup_stale",
                "ok",
                rollup_msg,
                details={
                    "used_cache": True,
                    "refreshed_at": rollups.refreshed_at,
                    "max_age_seconds": rollups.max_age_seconds,
                },
            )
        )
    else:
        # Live COUNT path — not stale, just not cached (ok for small DBs).
        rollup_level = "ok"
        rollup_msg = "inventory counts from live COUNT (no fresh rollup cache)"
        checks.append(
            _check(
                "rollup_stale",
                "ok",
                rollup_msg,
                details={"used_cache": False, "refreshed_at": rollups.refreshed_at},
            )
        )
    signals["rollups"] = {
        "level": rollup_level,
        "message": rollup_msg,
        "used_cache": bool(rollups.used_cache) if rollups is not None else None,
        "refreshed_at": rollups.refreshed_at if rollups is not None else None,
    }

    # ── adapters (soft warn only) ──────────────────────────
    adapter_levels: list[str] = []
    adapter_msgs: list[str] = []
    for label, run in (("replicate", report.last_replicate), ("archive", report.last_archive)):
        if run is None or not run.timestamp:
            adapter_levels.append("unknown")
            adapter_msgs.append(f"no {label} run yet")
            continue
        age = _age_hours(run.timestamp, now=ref)
        failures = int((run.payload or {}).get("failures", 0) or 0)
        if failures > 0:
            adapter_levels.append("warn")
            adapter_msgs.append(f"last {label} had {failures} failure(s)")
        elif age is not None and age > adapter_max_age_hours:
            adapter_levels.append("warn")
            adapter_msgs.append(f"last {label} is {age:.1f}h old")
        else:
            adapter_levels.append("ok")
            adapter_msgs.append(f"last {label} ok")
    adapter_level = _worst_level(*adapter_levels) if adapter_levels else "unknown"
    signals["adapters"] = {
        "level": adapter_level,
        "message": "; ".join(adapter_msgs) if adapter_msgs else "no adapter history",
        "replicate_ts": report.last_replicate.timestamp if report.last_replicate else None,
        "archive_ts": report.last_archive.timestamp if report.last_archive else None,
    }

    overall = _worst_level(
        audit_level if audit_level != "skipped" else "ok",
        scan_level,
        stash_level if stash_level != "skipped" else "ok",
        inv_level,
        rollup_level if rollup_level != "unknown" else "ok",
        # adapters are soft — only elevate overall to warn, never fail alone
        "warn" if adapter_level == "fail" else ("warn" if adapter_level == "warn" else "ok"),
    )
    # If everything soft-ok but only unknowns remain, keep ok.
    score = _score_from_checks(checks)
    if overall == "ok" and inv_level == "warn":
        overall = "warn"
    if overall == "ok" and stash_level == "warn":
        overall = "warn"
    if overall == "ok" and scan_level == "warn":
        overall = "warn"
    if overall == "ok" and adapter_level == "warn":
        overall = "warn"

    messages: list[str] = []
    for c in checks:
        if c["level"] in ("fail", "warn"):
            messages.append(str(c["message"]))
    for sig_name in ("scan", "stash", "adapters", "inventory"):
        sig = signals.get(sig_name) or {}
        if sig.get("level") in ("fail", "warn") and sig.get("message") not in messages:
            messages.append(str(sig["message"]))

    return {
        "overall": overall,
        "score": score,
        "signals": signals,
        "checks": checks,
        "messages": messages[:8],
        "quick": quick,
        "thresholds": {
            "scan_max_age_hours": scan_max_age_hours,
            "adapter_max_age_hours": adapter_max_age_hours,
            "stash_cooling_off_days": stash_cooling_off_days,
            "stash_grace_hours": stash_grace_hours,
            "rollup_max_age_hours": _ROLLUP_MAX_AGE_HOURS,
        },
        "generated_at_iso": ref.isoformat(timespec="seconds"),
        "source": "status",
    }


def _signals_from_estate_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Map EstateHealthReport dict sections into dashboard banner signals."""
    inv = data.get("inventory") or {}
    if inv.get("audit_skipped"):
        audit_level = "skipped"
        audit_msg = "audit chain not walked (quick mode)"
    elif inv.get("audit_ok") is False:
        audit_level = "fail"
        audit_msg = f"audit chain broken: {inv.get('audit_error') or 'unknown error'}"
    elif inv.get("audit_ok") is True:
        audit_level = "ok"
        audit_msg = f"audit chain ok ({int(inv.get('audit_rows_checked') or 0):,} rows)"
    else:
        audit_level = "unknown"
        audit_msg = "audit status unknown"

    scans = list(data.get("scan_freshness") or [])
    if not scans:
        scan_level = "warn"
        scan_msg = "no finished scan yet"
        scan_age = None
        scan_root = None
        scan_finished = None
    else:
        scan_level = "ok"
        for s in scans:
            scan_level = _worst_level(scan_level, str(s.get("level") or "unknown"))
        # Prefer worst / oldest for display
        worst = max(
            scans,
            key=lambda s: _LEVEL_RANK.get(str(s.get("level") or "unknown"), 0),
        )
        scan_msg = str(worst.get("message") or f"scan roots={len(scans)} level={scan_level}")
        if scan_level == "fail":
            scan_msg = next(
                (str(c.get("message")) for c in (data.get("checks") or []) if c.get("name") == "stale_scan" and c.get("level") == "fail"),
                scan_msg,
            )
        scan_age = worst.get("age_hours")
        scan_root = worst.get("root_path")
        scan_finished = worst.get("finished_at")

    stash = data.get("stash") or {}
    stash_level = str(stash.get("level") or "unknown")
    stash_msg = str(
        stash.get("message")
        or (
            f"{stash.get('in_flight_entries', 0)} in-flight"
            if stash.get("in_flight_entries")
            else "stash ok"
        )
    )

    rollups = data.get("rollups") or {}
    if not rollups:
        rollup_level = "unknown"
        rollup_msg = "rollup info unavailable"
    elif rollups.get("used_cache"):
        rollup_level = "ok"
        rollup_msg = f"inventory counts from rollup cache (refreshed {rollups.get('refreshed_at') or '—'})"
    else:
        rollup_level = "ok"
        rollup_msg = "inventory counts from live COUNT"

    adapters = data.get("adapters") or {}
    adapter_level = str(adapters.get("level") or "unknown")
    adapter_msg = f"adapters {adapter_level}"

    fp = data.get("fp") or {}
    if not fp or fp.get("present") is False:
        fp_level = "skipped"
        fp_msg = "FP section not present"
    else:
        fp_level = str(fp.get("level") or "unknown")
        if fp.get("cloud_retire_ready"):
            fp_msg = "cloud retire ready"
        else:
            fp_msg = "FP not cloud-retire ready" if fp_level != "ok" else "FP present"

    inv_level = "ok"
    if int(inv.get("permanodes") or 0) == 0 and int(inv.get("current_claims") or 0) == 0:
        inv_level = "warn"
    inv_msg = (
        f"{int(inv.get('permanodes') or 0):,} permanodes · "
        f"{int(inv.get('current_claims') or 0):,} claims · "
        f"{int(inv.get('scan_runs') or 0):,} scan runs"
    )

    return {
        "audit": {
            "level": audit_level,
            "message": audit_msg,
            "ok": inv.get("audit_ok"),
            "skipped": inv.get("audit_skipped"),
            "rows_checked": inv.get("audit_rows_checked"),
        },
        "scan": {
            "level": scan_level,
            "message": scan_msg,
            "age_hours": scan_age,
            "root_path": scan_root,
            "finished_at": scan_finished,
            "roots": len(scans),
        },
        "stash": {
            "level": stash_level,
            "message": stash_msg,
            "in_flight_entries": stash.get("in_flight_entries"),
            "age_hours": stash.get("age_hours_oldest"),
        },
        "inventory": {
            "level": inv_level,
            "message": inv_msg,
            "permanodes": inv.get("permanodes"),
            "current_claims": inv.get("current_claims"),
            "scan_runs": inv.get("scan_runs"),
            "audit_entries": inv.get("audit_entries"),
            "machines": inv.get("machines"),
        },
        "rollups": {
            "level": rollup_level,
            "message": rollup_msg,
            "used_cache": rollups.get("used_cache") if rollups else None,
            "refreshed_at": rollups.get("refreshed_at") if rollups else None,
        },
        "adapters": {
            "level": adapter_level,
            "message": adapter_msg,
            "replicate": adapters.get("replicate"),
            "archive": adapters.get("archive"),
        },
        "fp": {
            "level": fp_level,
            "message": fp_msg,
            "cloud_retire_ready": fp.get("cloud_retire_ready"),
            "layout": fp.get("layout"),
        },
        "schedule": data.get("schedule"),
        "mounts": data.get("mounts"),
        "attached_imports": data.get("attached_imports"),
        "scan_freshness": data.get("scan_freshness"),
    }


def _try_collect_estate_health(
    db_path: Path,
    *,
    quick: bool,
    include_imports: bool,
    probes: bool,
    include_fp: bool = False,
) -> dict[str, Any] | None:
    """Soft-import full ADR-0017 collector when the health package exists."""
    try:
        from steward.infra.health import collect_estate_health, estate_health_to_dict
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001 — broken optional surface
        log_swallowed_error(
            "dashboard.api.estate_health_import",
            exc,
            context={"db_path": str(db_path)},
        )
        return None
    try:
        report = collect_estate_health(
            db_path=db_path,
            quick=quick,
            include_imports=include_imports,
            probes=probes,
            include_fp=include_fp,
            include_schedule=False if quick else True,
        )
        data = estate_health_to_dict(report)
        overall = str(data.get("overall") or "unknown")
        checks = list(data.get("checks") or [])
        messages: list[str] = []
        for c in checks:
            if isinstance(c, dict) and c.get("level") in ("fail", "warn"):
                messages.append(str(c.get("message") or c.get("name") or ""))
        notes = data.get("notes") or ()
        if isinstance(notes, (list, tuple)):
            for n in notes:
                if n and str(n) not in messages:
                    messages.append(str(n))
        signals = _signals_from_estate_dict(data)
        return {
            "overall": overall,
            "score": _score_from_checks(checks)
            if checks
            else (
                100
                if overall == "ok"
                else (70 if overall == "warn" else (30 if overall == "fail" else 50))
            ),
            "signals": signals,
            "checks": checks,
            "messages": [m for m in messages if m][:8],
            "quick": bool(data.get("quick", quick)),
            "machine_id": data.get("machine_id"),
            "report": data,
            "generated_at_iso": data.get("generated_at")
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "estate_health",
        }
    except Exception as exc:  # noqa: BLE001 — fall back to status posture
        log_swallowed_error(
            "dashboard.api.estate_health_collect",
            exc,
            context={"db_path": str(db_path), "quick": quick},
        )
        return None


def build_health_payload(
    db_path: Path,
    *,
    include_imports: bool = False,
    quick: bool = True,
    probes: bool = False,
    include_fp: bool = False,
) -> dict[str, Any]:
    """Estate posture payload for ``GET /api/health`` (ADR-0017 foundation).

    Cheap default: ``quick=True``, ``probes=False``, no FP probe unless
    ``include_fp`` or ``not quick``. Prefers full estate collector when present.
    """
    t0 = time.time()
    want_fp = include_fp or (not quick)
    estate = _try_collect_estate_health(
        db_path,
        quick=quick,
        include_imports=include_imports,
        probes=probes,
        include_fp=want_fp,
    )
    if estate is not None:
        payload = _ok(
            **estate,
            include_imports=include_imports,
            probes=probes,
            elapsed_ms=int((time.time() - t0) * 1000),
            generated_at=time.time(),
        )
        return payload

    report = collect_status(
        db_path=db_path,
        include_imports=include_imports,
        quick=quick,
    )
    posture = posture_from_status_report(report, quick=quick)

    if want_fp:
        try:
            from steward.infra.fp_status import collect_fp_status, fp_status_to_dict

            fp_report = collect_fp_status()
            fp_dict = fp_status_to_dict(fp_report)
            verdict = fp_dict.get("verdict") or {}
            cloud_ok = bool(verdict.get("cloud_retire_ready")) if verdict else False
            if not verdict:
                fp_level = "unknown"
                fp_msg = "FP verdict unavailable"
            elif cloud_ok:
                fp_level = "ok"
                fp_msg = "cloud retire ready"
            else:
                problems = list(verdict.get("problems") or [])
                fp_level = "warn"
                fp_msg = (
                    "FP not cloud-retire ready: " + "; ".join(problems[:3])
                    if problems
                    else "FP not cloud-retire ready"
                )
            posture["signals"]["fp"] = {
                "level": fp_level,
                "message": fp_msg,
                "cloud_retire_ready": cloud_ok if verdict else None,
                "layout": verdict.get("layout"),
            }
            # fp_not_ready is opt-in for fail-on; surface as warn only on overall.
            if fp_level == "warn":
                posture["overall"] = _worst_level(str(posture["overall"]), "warn")
                if fp_msg not in posture["messages"]:
                    posture["messages"].append(fp_msg)
            posture["checks"].append(
                _check(
                    "fp_not_ready",
                    "ok" if cloud_ok else ("warn" if verdict else "unknown"),
                    fp_msg,
                    details={"cloud_retire_ready": cloud_ok if verdict else None},
                )
            )
            posture["score"] = _score_from_checks(posture["checks"])
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "dashboard.api.health_fp",
                exc,
                context={"db_path": str(db_path)},
            )
            posture["signals"]["fp"] = {
                "level": "unknown",
                "message": f"FP probe failed: {exc}",
            }

    return _ok(
        overall=posture["overall"],
        score=posture["score"],
        signals=posture["signals"],
        checks=posture["checks"],
        messages=posture["messages"],
        quick=quick,
        thresholds=posture["thresholds"],
        include_imports=include_imports,
        probes=probes,
        source=posture["source"],
        generated_at_iso=posture["generated_at_iso"],
        elapsed_ms=int((time.time() - t0) * 1000),
        generated_at=time.time(),
    )


def build_health_series(
    db_path: Path,
    *,
    limit: int = 48,
) -> dict[str, Any]:
    """Last N compact health snapshots for sparklines (ADR-0017 data-dir series).

    Returns an empty series when the health package / snapshot files are absent.
    """
    limit = max(1, min(int(limit), 500))
    try:
        from steward.infra.db.settings import data_dir
        from steward.infra.health import read_health_series
    except ImportError:
        return _ok(series=[], limit=limit, count=0, note="health snapshot series not available")
    try:
        points = read_health_series(data_dir=data_dir(), limit=limit)
        return _ok(series=list(points), limit=limit, count=len(points))
    except Exception as exc:  # noqa: BLE001 — foundation: empty series is fine
        log_swallowed_error(
            "dashboard.api.health_series",
            exc,
            context={"db_path": str(db_path), "limit": limit},
        )
        return _ok(series=[], limit=limit, count=0, note="health snapshot series not available")


def build_status_payload(
    db_path: Path,
    *,
    include_imports: bool = False,
    quick: bool = True,
) -> dict[str, Any]:
    report = collect_status(
        db_path=db_path,
        include_imports=include_imports,
        quick=quick,
    )
    payload = status_to_dict(report)
    payload["include_imports"] = include_imports
    payload["quick"] = quick
    payload["ok"] = True
    payload["generated_at"] = time.time()
    # Soft-poll friendly: derive posture from the same StatusReport (no second DB pass).
    payload["posture"] = posture_from_status_report(report, quick=quick)
    return payload


def _list_schedule_rows(*, probe: bool = False) -> list[dict[str, Any]]:
    """Launchd schedule templates when available (ADR-0019 reliability fields).

    ``steward.infra.schedule`` is private to the monorepo (not open-core).
    Loaded via importlib so open-core mypy never hard-depends on it.
    """
    import importlib

    try:
        rel_mod = importlib.import_module("steward.infra.schedule.reliability")
        collect = getattr(rel_mod, "collect_schedule_reliability")
        to_dicts = getattr(rel_mod, "jobs_to_dicts")
        jobs = collect(probe=probe)
        result: list[dict[str, Any]] = list(to_dicts(jobs))
        return result
    except Exception as exc:  # noqa: BLE001 — fall back to thin template list
        log_swallowed_error(
            "dashboard.api.schedule_reliability",
            exc,
            context={"probe": probe},
        )

    try:
        mod = importlib.import_module("steward.infra.schedule")
        list_templates = getattr(mod, "list_templates")
    except Exception as exc:  # noqa: BLE001 — missing module or broken private surface
        log_swallowed_error(
            "dashboard.api.schedule_module",
            exc,
            context={},
        )
        return [{"error": "schedule module not available in this build (open-core)"}]
    rows: list[dict[str, Any]] = []
    try:
        for tmpl in list_templates():
            rows.append(
                {
                    "name": tmpl.name,
                    "label": tmpl.label,
                    "installed": tmpl.installed_plist_path.exists(),
                    "plist": (
                        str(tmpl.installed_plist_path) if tmpl.installed_plist_path.exists() else None
                    ),
                }
            )
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]
    return rows


def build_analysis_bundle(
    db_path: Path,
    *,
    include_imports: bool = False,
    scan_limit: int = 12,
    audit_limit: int = 25,
) -> dict[str, Any]:
    """Cheap cross-surface bundle for the analysis panes.

    Intentionally omits machines / full stats overview — those are
    multi-minute on multi-GB inventories and load via dedicated endpoints.
    """
    from steward.infra.mcp import handlers

    t0 = time.time()
    scans = handlers.recent_scan_runs(db_path, limit=scan_limit)
    audit = handlers.tail_audit_log(db_path, limit=audit_limit)
    policies = handlers.list_policies()
    schedules = _list_schedule_rows()

    return _ok(
        scans=scans,
        audit=audit,
        policies=policies,
        schedules=schedules,
        include_imports=include_imports,
        elapsed_ms=int((time.time() - t0) * 1000),
    )


def build_stats_overview(db_path: Path, *, include_imports: bool = False) -> dict[str, Any]:
    """Full inventory stats overview — can be slow on multi-GB DBs."""
    from steward.infra.stats import overview

    t0 = time.time()
    ov = overview(db_path=db_path, include_imports=include_imports)
    return _ok(
        overview=asdict(ov),
        include_imports=include_imports,
        elapsed_ms=int((time.time() - t0) * 1000),
    )


def build_stats_axis(
    db_path: Path,
    *,
    axis: str,
    limit: int = 20,
    include_imports: bool = False,
    dim_a: str | None = None,
    dim_b: str | None = None,
    path_prefix: str | None = None,
) -> dict[str, Any]:
    from steward.infra.stats import (
        by_classification,
        by_domain,
        by_extension,
        by_tier,
        by_volume,
        duplicate_permanodes,
    )

    t0 = time.time()
    axis_l = axis.strip().lower()
    if axis_l in ("tier", "by-tier", "tiers"):
        rows = [asdict(r) for r in by_tier(db_path=db_path, include_imports=include_imports)]
    elif axis_l in ("domain", "by-domain", "domains"):
        rows = [asdict(r) for r in by_domain(db_path=db_path, include_imports=include_imports)]
    elif axis_l in ("volume", "by-volume", "volumes"):
        rows = [asdict(r) for r in by_volume(db_path=db_path, include_imports=include_imports)]
    elif axis_l in ("extension", "extensions", "by-extension"):
        rows = [asdict(r) for r in by_extension(db_path=db_path, limit=limit, include_imports=include_imports)]
    elif axis_l in ("classification", "classifications"):
        rows = [
            asdict(r) for r in by_classification(db_path=db_path, limit=limit, include_imports=include_imports)
        ]
    elif axis_l in ("duplicate", "duplicates"):
        rows = [
            asdict(r)
            for r in duplicate_permanodes(db_path=db_path, limit=limit, include_imports=include_imports)
        ]
    elif axis_l in ("cross", "matrix"):
        from steward.core.matrix.types import CrossStatsRequest
        from steward.core.matrix.validate import MatrixValidationError
        from steward.infra.stats_matrix import cross_stats, cross_stats_to_dict

        if not dim_a:
            return _err("axis=cross requires dim_a")
        try:
            req = CrossStatsRequest(
                dim_a=dim_a,  # type: ignore[arg-type]
                dim_b=dim_b,  # type: ignore[arg-type]
                path_prefix=path_prefix,
                limit=limit,
                include_imports=include_imports,
            )
            payload = cross_stats_to_dict(cross_stats(db_path=db_path, req=req))
        except MatrixValidationError as exc:
            return _err(str(exc))
        return _ok(axis=axis_l, **payload, elapsed_ms=int((time.time() - t0) * 1000))
    else:
        return _err(f"unknown stats axis: {axis}")
    return _ok(axis=axis_l, rows=rows, elapsed_ms=int((time.time() - t0) * 1000))


def build_surface_payload(
    db_path: Path,
    *,
    path_prefix: str = "",
    color_by: str = "none",
    tier: str | None = None,
    volume: str | None = None,
    child_limit: int = 100,
    measure: str = "total_bytes",
    include_imports: bool = False,
) -> dict[str, Any]:
    """Inventory surface path-tree payload (ADR-0022)."""
    from steward.core.matrix.types import PathTreeRequest
    from steward.core.matrix.validate import MatrixValidationError
    from steward.infra.stats_tree import path_tree_depth1, path_tree_to_dict

    t0 = time.time()
    try:
        req = PathTreeRequest(
            path_prefix=path_prefix,
            color_by=color_by,  # type: ignore[arg-type]
            measure=measure,  # type: ignore[arg-type]
            tier=tier,
            volume=volume,
            child_limit=child_limit,
            include_imports=include_imports,
        )
        payload = path_tree_to_dict(path_tree_depth1(db_path=db_path, req=req))
    except MatrixValidationError as exc:
        return _err(str(exc))
    return _ok(**payload, elapsed_ms=int((time.time() - t0) * 1000))


def action_catalog() -> list[dict[str, Any]]:
    """Machine-readable list of dashboard-runnable operations."""
    return [
        {
            "id": "refresh_rollups",
            "group": "status",
            "label": "Refresh inventory rollups",
            "description": "Recount permanodes/claims into meta cache (write meta only).",
            "kind": "write_meta",
            "destructive": False,
            "params": [],
        },
        {
            "id": "refresh_health",
            "group": "status",
            "label": "Refresh estate health",
            "description": "Recompute estate posture (read-only; optional snapshot when health package present).",
            "kind": "read",
            "destructive": False,
            "params": [
                {"name": "full", "default": False},
                {"name": "include_fp", "default": False},
                {"name": "write_snapshot", "default": False},
            ],
        },
        {
            "id": "status_full",
            "group": "status",
            "label": "Collect full status",
            "description": "Full audit-chain walk + stash CTE (can be slow).",
            "kind": "read",
            "destructive": False,
            "params": [],
        },
        {
            "id": "verify_chain",
            "group": "status",
            "label": "Verify audit chain",
            "description": "Walk the full hash chain and report mismatches.",
            "kind": "read",
            "destructive": False,
            "params": [],
        },
        {
            "id": "fp_status",
            "group": "cloud",
            "label": "Probe Dropbox File Provider",
            "description": "Store vs mount layout + health verdict (ADR-0015).",
            "kind": "read",
            "destructive": False,
            "params": [],
        },
        {
            "id": "list_machines",
            "group": "inventory",
            "label": "List machines",
            "description": "Machine IDs with claim/scan/audit counts (slow on huge DBs).",
            "kind": "read",
            "destructive": False,
            "params": [],
            "slow": True,
        },
        {
            "id": "stats_overview",
            "group": "inventory",
            "label": "Stats overview",
            "description": "Tiers, domains, largest permanode, duplicate count.",
            "kind": "read",
            "destructive": False,
            "params": [],
            "slow": True,
        },
        {
            "id": "stats_axis",
            "group": "inventory",
            "label": "Stats by axis",
            "description": "tier | domain | volume | extensions | classifications | duplicates | cross",
            "kind": "read",
            "destructive": False,
            "params": [
                {"name": "axis", "default": "tier"},
                {"name": "limit", "default": 20},
                {"name": "dim_a", "default": "domain"},
                {"name": "dim_b", "default": ""},
                {"name": "path_prefix", "default": ""},
            ],
            "slow": True,
        },
        {
            "id": "surface_tree",
            "group": "inventory",
            "label": "Surface path tree",
            "description": "Depth-1 inventory path tree (ADR-0022). Prefer prefix/tier on multi-GB DBs.",
            "kind": "read",
            "destructive": False,
            "params": [
                {"name": "path_prefix", "default": ""},
                {"name": "color_by", "default": "none"},
                {"name": "tier", "default": ""},
                {"name": "volume", "default": ""},
                {"name": "child_limit", "default": 40},
            ],
            "slow": True,
        },
        {
            "id": "fleet_health",
            "group": "inventory",
            "label": "Fleet health matrix",
            "description": "Multi-machine fleet matrix (ADR-0021).",
            "kind": "read",
            "destructive": False,
            "params": [
                {"name": "include_imports", "default": True},
                {"name": "quick", "default": True},
            ],
        },
        {
            "id": "dual_presence_sample",
            "group": "cloud",
            "label": "Dual-presence sample",
            "description": "Bounded store/mount presence sample (ADR-0020).",
            "kind": "read",
            "destructive": False,
            "params": [{"name": "sample", "default": 32}],
        },
        {
            "id": "filter_plan_dual_presence",
            "group": "cloud",
            "label": "Filter plan by dual-presence",
            "description": "Bucket a plan TSV by dual-presence (writes filtered artefacts; no tier FS mutate).",
            "kind": "plan",
            "destructive": False,
            "params": [
                {"name": "manifest_path", "required": True},
                {"name": "intent", "default": "cloud_retire"},
                {"name": "limit", "default": 0},
            ],
            "slow": True,
        },
        {
            "id": "plan_show",
            "group": "policy",
            "label": "Show plan backlog record",
            "description": "Detail for one registered plan_id (ADR-0019).",
            "kind": "read",
            "destructive": False,
            "params": [{"name": "plan_id", "required": True}],
        },
        {
            "id": "search_path",
            "group": "inspect",
            "label": "Search by path",
            "description": "Current claims whose path contains the query.",
            "kind": "read",
            "destructive": False,
            "params": [
                {"name": "q", "required": True},
                {"name": "limit", "default": 25},
            ],
        },
        {
            "id": "search_hash",
            "group": "inspect",
            "label": "Search by hash prefix",
            "description": "Permanodes matching a hash prefix.",
            "kind": "read",
            "destructive": False,
            "params": [
                {"name": "q", "required": True},
                {"name": "limit", "default": 25},
            ],
        },
        {
            "id": "inspect",
            "group": "inspect",
            "label": "Inspect target",
            "description": "Path, permanode id, or hash → claims + audit.",
            "kind": "read",
            "destructive": False,
            "params": [{"name": "target", "required": True}],
        },
        {
            "id": "list_policies",
            "group": "policy",
            "label": "List policies",
            "description": "Bundled policy YAML files.",
            "kind": "read",
            "destructive": False,
            "params": [],
        },
        {
            "id": "show_policy",
            "group": "policy",
            "label": "Show policy YAML",
            "description": "Raw YAML for one bundled policy.",
            "kind": "read",
            "destructive": False,
            "params": [{"name": "name", "required": True, "default": "retention.yml"}],
        },
        {
            "id": "policy_plan",
            "group": "policy",
            "label": "Generate plan (TSV)",
            "description": "Write a plan manifest from a policy (no apply).",
            "kind": "plan",
            "destructive": False,
            "params": [
                {"name": "policy", "default": "retention.yml"},
                {"name": "root_prefix", "default": ""},
            ],
        },
        {
            "id": "apply_dry_run",
            "group": "apply",
            "label": "Apply dry-run",
            "description": (
                "Simulate apply for a plan TSV (no FS mutation). Returns plan_token + "
                "execute_handoff for CLI/MCP apply execute (not run in GUI)."
            ),
            "kind": "plan",
            "destructive": False,
            "params": [{"name": "manifest_path", "required": True}],
        },
        {
            "id": "replicate_dry_run",
            "group": "replicate",
            "label": "Replicate dry-run",
            "description": "rclone --dry-run across replication policy.",
            "kind": "plan",
            "destructive": False,
            "params": [{"name": "policy", "default": "replication.yml"}],
        },
        {
            "id": "replicate_execute",
            "group": "replicate",
            "label": "Replicate execute",
            "description": "DESTRUCTIVE: run replication for real.",
            "kind": "write",
            "destructive": True,
            "confirm": CONFIRM_EXECUTE,
            "params": [{"name": "policy", "default": "replication.yml"}],
        },
        {
            "id": "archive_dry_run",
            "group": "archive",
            "label": "Archive snapshot dry-run",
            "description": "restic snapshot dry-run.",
            "kind": "plan",
            "destructive": False,
            "params": [{"name": "policy", "default": "archive.yml"}],
        },
        {
            "id": "archive_execute",
            "group": "archive",
            "label": "Archive snapshot execute",
            "description": "DESTRUCTIVE: create archive snapshot.",
            "kind": "write",
            "destructive": True,
            "confirm": CONFIRM_EXECUTE,
            "params": [{"name": "policy", "default": "archive.yml"}],
        },
        {
            "id": "stash_finalize",
            "group": "stash",
            "label": "Finalize stash run",
            "description": "DESTRUCTIVE: rm cooling-off entries after window.",
            "kind": "write",
            "destructive": True,
            "confirm": CONFIRM_EXECUTE,
            "params": [{"name": "run_id", "required": True}],
        },
        {
            "id": "stash_restore",
            "group": "stash",
            "label": "Restore stash run",
            "description": "DESTRUCTIVE: restore stashed paths to live tier.",
            "kind": "write",
            "destructive": True,
            "confirm": CONFIRM_EXECUTE,
            "params": [{"name": "run_id", "required": True}],
        },
        {
            "id": "schedule_list",
            "group": "schedule",
            "label": "List schedules",
            "description": "Bundled launchd templates + install state.",
            "kind": "read",
            "destructive": False,
            "params": [],
        },
        {
            "id": "cli_hint",
            "group": "cli",
            "label": "CLI command hint",
            "description": "Return the equivalent steward CLI for an operation (no run).",
            "kind": "read",
            "destructive": False,
            "params": [
                {"name": "op", "required": True},
                {"name": "args", "default": ""},
            ],
        },
    ]


def _require_confirm(params: dict[str, Any], *, destructive: bool) -> dict[str, Any] | None:
    if not destructive:
        return None
    got = str(params.get("confirm") or "").strip()
    if got != CONFIRM_EXECUTE:
        return _err(
            f"destructive action requires confirm={CONFIRM_EXECUTE!r}",
            need_confirm=CONFIRM_EXECUTE,
        )
    return None


def run_action(db_path: Path, *, action_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch one dashboard action. Caller enforces loopback."""
    params = dict(params or {})
    catalog = {a["id"]: a for a in action_catalog()}
    meta = catalog.get(action_id)
    if meta is None:
        return _err(f"unknown action: {action_id}", known=sorted(catalog))

    bad = _require_confirm(params, destructive=bool(meta.get("destructive")))
    if bad is not None:
        return bad

    try:
        return _DISPATCH[action_id](db_path, params)
    except KeyError:
        return _err(f"action not implemented: {action_id}")
    except Exception as exc:  # noqa: BLE001 — surface to operator UI
        return _err(str(exc), action=action_id)


def _act_refresh_rollups(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    include = bool(params.get("include_imports"))
    counts = refresh_inventory_rollups(db_path=db_path, include_imports=include)
    return _ok(inventory=asdict(counts), include_imports=include)


def _act_refresh_health(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Non-destructive health recompute; optional snapshot write via health package."""
    full = bool(params.get("full"))
    include_fp = bool(params.get("include_fp")) or full
    include_imports = bool(params.get("include_imports"))
    write_snapshot = bool(params.get("write_snapshot"))
    payload = build_health_payload(
        db_path,
        include_imports=include_imports,
        quick=not full,
        probes=full,
        include_fp=include_fp,
    )
    snapshot_path: str | None = None
    if write_snapshot:
        try:
            from steward.infra.db.settings import data_dir
            from steward.infra.health import (
                collect_estate_health,
                write_health_snapshot,
            )

            report = collect_estate_health(
                db_path=db_path,
                quick=not full,
                include_imports=include_imports,
                probes=full,
            )
            path = write_health_snapshot(report, data_dir=data_dir())
            snapshot_path = str(path)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "dashboard.api.refresh_health_snapshot",
                exc,
                context={"db_path": str(db_path)},
            )
            payload["snapshot_error"] = str(exc)
    if snapshot_path:
        payload["snapshot_path"] = snapshot_path
    return payload


def _act_status_full(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    include = bool(params.get("include_imports"))
    return build_status_payload(db_path, include_imports=include, quick=False)


def _act_verify_chain(db_path: Path, _params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.db.admin import verify_chain

    t0 = time.time()
    result = verify_chain(db_path)
    return _ok(
        rows_checked=result.rows_checked,
        chain_ok=result.ok,
        error=result.error,
        elapsed_ms=int((time.time() - t0) * 1000),
    )


def _act_fp_status(_db_path: Path, _params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.fp_status import collect_fp_status, fp_status_to_dict

    return _ok(**fp_status_to_dict(collect_fp_status()))


def _act_list_machines(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.mcp import handlers

    t0 = time.time()
    rows = handlers.list_machines(db_path, include_imports=bool(params.get("include_imports")))
    return _ok(machines=rows, elapsed_ms=int((time.time() - t0) * 1000))


def _act_stats_overview(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    return build_stats_overview(db_path, include_imports=bool(params.get("include_imports")))


def _act_stats_axis(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    dim_b = str(params.get("dim_b") or "").strip() or None
    path_prefix = str(params.get("path_prefix") or "").strip() or None
    dim_a = str(params.get("dim_a") or "").strip() or None
    return build_stats_axis(
        db_path,
        axis=str(params.get("axis") or "tier"),
        limit=int(params.get("limit") or 20),
        include_imports=bool(params.get("include_imports")),
        dim_a=dim_a,
        dim_b=dim_b,
        path_prefix=path_prefix,
    )


def _act_surface_tree(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    return build_surface_payload(
        db_path,
        path_prefix=str(params.get("path_prefix") or ""),
        color_by=str(params.get("color_by") or "none"),
        tier=str(params.get("tier") or "").strip() or None,
        volume=str(params.get("volume") or "").strip() or None,
        child_limit=int(params.get("child_limit") or params.get("limit") or 40),
        measure=str(params.get("measure") or "total_bytes"),
        include_imports=bool(params.get("include_imports")),
    )


def _act_fleet_health(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    include = params.get("include_imports")
    if include is None:
        include = True
    return build_fleet_payload(
        db_path,
        include_imports=bool(include),
        quick=params.get("quick", True) is not False,
    )


def _act_dual_presence_sample(_db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.mcp import handlers

    return handlers.dual_presence_sample(sample=int(params.get("sample") or 32))


def _act_filter_plan_dual_presence(_db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.mcp import handlers

    path = str(params.get("manifest_path") or "").strip()
    if not path:
        return _err("manifest_path required")
    return handlers.filter_plan_dual_presence(
        manifest_path=path,
        out_dir=str(params.get("out_dir") or "").strip() or None,
        limit=int(params.get("limit") or 0),
        path_col=str(params.get("path_col") or "source_path"),
        intent=str(params.get("intent") or "cloud_retire"),
        register_with=str(params.get("register_with") or "").strip() or None,
    )


def _act_plan_show(_db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    plan_id = str(params.get("plan_id") or "").strip()
    if not plan_id:
        return _err("plan_id required")
    return build_plan_detail(plan_id)


def _act_search_path(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.mcp import handlers

    q = str(params.get("q") or "").strip()
    if not q:
        return _err("q required")
    limit = int(params.get("limit") or 25)
    rows = handlers.find_permanode_by_path(db_path, path_substring=q, limit=limit)
    return _ok(query=q, results=rows, count=len(rows))


def _act_search_hash(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.mcp import handlers

    q = str(params.get("q") or "").strip()
    if not q:
        return _err("q required")
    limit = int(params.get("limit") or 25)
    rows = handlers.find_permanode_by_hash(db_path, hash_prefix=q, limit=limit)
    return _ok(query=q, results=rows, count=len(rows))


def _act_inspect(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.mcp import handlers

    target = str(params.get("target") or "").strip()
    if not target:
        return _err("target required")
    # handlers.inspect_target uses settings db path; pass via env consistency
    del db_path  # inventory path resolved inside inspect
    return handlers.inspect_target(
        target,
        audit_limit=int(params.get("audit_limit") or 20),
        include_imports=bool(params.get("include_imports")),
    )


def _act_list_policies(_db_path: Path, _params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.mcp import handlers

    return _ok(policies=handlers.list_policies())


def _act_show_policy(_db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.mcp import handlers

    name = str(params.get("name") or "retention.yml")
    out = handlers.show_policy(name=name)
    if not isinstance(out, dict):
        return _err("show_policy returned unexpected payload")
    # MCP returns found/yaml without ok — normalize for HTTP status mapping.
    if out.get("found"):
        return _ok(**out)
    return _err(str(out.get("error") or f"policy not found: {name}"), **out)


def _act_policy_plan(_db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.mcp import handlers

    root = str(params.get("root_prefix") or "").strip() or None
    out = handlers.policy_plan(
        policy=str(params.get("policy") or "retention.yml"),
        root_prefix=root,
    )
    if not isinstance(out, dict):
        return _err("policy_plan returned unexpected payload")
    if out.get("ok") is False or out.get("error"):
        return out if "ok" in out else _err(str(out.get("error") or "policy_plan failed"), **out)
    if "ok" not in out:
        out = {**out, "ok": True}
    return out


def _execute_handoff_for_apply(
    *,
    manifest_path: str,
    plan_token: str | None,
    plan_token_expires_at: str | None,
    dry_run_ok: bool,
    rows_errored: int,
) -> dict[str, Any]:
    """CLI/MCP handoff for apply execute — not offered as a dashboard action."""
    path = str(Path(manifest_path).expanduser())
    handoff: dict[str, Any] = {
        "gui_execute": False,
        "reason": (
            "Dashboard does not run apply --execute (ADR-0002 operator-in-the-loop). "
            "Use CLI or MCP write mode after a clean dry-run."
        ),
        "cli": f"steward apply --manifest {path} --execute",
        "cli_dry_run": f"steward apply --manifest {path} --dry-run",
        "mcp": None,
        "plan_token": plan_token,
        "plan_token_expires_at": plan_token_expires_at,
    }
    if plan_token and dry_run_ok and rows_errored == 0:
        handoff["mcp"] = {
            "mode": "STEWARD_MCP_MODE=write",
            "tool": "apply_execute",
            "manifest_path": path,
            "plan_token": plan_token,
            "note": "One-shot token from this dry-run (ADR-0016); expires at plan_token_expires_at.",
        }
    elif dry_run_ok and rows_errored:
        handoff["note"] = "No plan_token — dry-run had row errors; fix and re-run apply dry-run."
    return handoff


def _act_apply_dry_run(_db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.mcp import handlers

    path = str(params.get("manifest_path") or "").strip()
    if not path:
        return _err("manifest_path required")
    # issue_plan_token=True so operators get MCP handoff without a second dry-run.
    result = handlers.apply_dry_run(
        manifest_path=path,
        max_files=(int(params["max_files"]) if params.get("max_files") not in (None, "") else None),
        skip_verify=bool(params.get("skip_verify")),
        allow_store_path_unlink=bool(params.get("allow_store_path_unlink")),
        require_fp_healthy=params.get("require_fp_healthy", True) is not False,
        issue_plan_token=True,
    )
    if not isinstance(result, dict):
        return _err("apply_dry_run returned unexpected payload")
    manifest = str(result.get("manifest_path") or path)
    result["execute_handoff"] = _execute_handoff_for_apply(
        manifest_path=manifest,
        plan_token=result.get("plan_token") if isinstance(result.get("plan_token"), str) else None,
        plan_token_expires_at=(
            result.get("plan_token_expires_at")
            if isinstance(result.get("plan_token_expires_at"), str)
            else None
        ),
        dry_run_ok=bool(result.get("ok")),
        rows_errored=int(result.get("rows_errored") or 0),
    )
    return result


def _act_replicate_dry_run(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.db.admin import resolve_machine_id
    from steward.infra.replicate.orchestrate import resolve_policy_path, run_replicate

    policy = str(params.get("policy") or "replication.yml")
    report = run_replicate(
        db_path=db_path,
        policy_path=resolve_policy_path(policy),
        machine_id=resolve_machine_id(db_path),
        dry_run=True,
    )
    return _ok(
        dry_run=True,
        policy=policy,
        runs=getattr(report, "runs", None),
        successes=getattr(report, "successes", None),
        failures=getattr(report, "failures", None),
        bytes_transferred=getattr(report, "bytes_transferred", None),
        report=str(report),
    )


def _act_replicate_execute(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.db.admin import resolve_machine_id
    from steward.infra.replicate.orchestrate import resolve_policy_path, run_replicate

    policy = str(params.get("policy") or "replication.yml")
    report = run_replicate(
        db_path=db_path,
        policy_path=resolve_policy_path(policy),
        machine_id=resolve_machine_id(db_path),
        dry_run=False,
    )
    return _ok(
        dry_run=False,
        policy=policy,
        runs=getattr(report, "runs", None),
        successes=getattr(report, "successes", None),
        failures=getattr(report, "failures", None),
        bytes_transferred=getattr(report, "bytes_transferred", None),
        report=str(report),
    )


def _act_archive_dry_run(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.archive.orchestrate import resolve_policy_path, run_snapshot
    from steward.infra.db.admin import resolve_machine_id

    policy = str(params.get("policy") or "archive.yml")
    report = run_snapshot(
        db_path=db_path,
        policy_path=resolve_policy_path(policy),
        machine_id=resolve_machine_id(db_path),
        dry_run=True,
    )
    return _ok(dry_run=True, policy=policy, report=str(report), raw=getattr(report, "__dict__", {}))


def _act_archive_execute(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.archive.orchestrate import resolve_policy_path, run_snapshot
    from steward.infra.db.admin import resolve_machine_id

    policy = str(params.get("policy") or "archive.yml")
    report = run_snapshot(
        db_path=db_path,
        policy_path=resolve_policy_path(policy),
        machine_id=resolve_machine_id(db_path),
        dry_run=False,
    )
    return _ok(dry_run=False, policy=policy, report=str(report), raw=getattr(report, "__dict__", {}))


def _act_stash_finalize(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.db.admin import resolve_machine_id
    from steward.infra.db.stash_cmd import finalize_stash

    run_id = str(params.get("run_id") or params.get("manifest_run_id") or "").strip()
    if not run_id:
        return _err("run_id (manifest_run_id) required")
    result = finalize_stash(
        manifest_run_id=run_id,
        machine_id=resolve_machine_id(db_path),
        db_path=db_path,
        force=bool(params.get("force")),
    )
    return _ok(run_id=run_id, result=result)


def _act_stash_restore(db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    from steward.infra.db.admin import resolve_machine_id
    from steward.infra.db.stash_cmd import restore_stash

    run_id = str(params.get("run_id") or params.get("manifest_run_id") or "").strip()
    if not run_id:
        return _err("run_id (manifest_run_id) required")
    result = restore_stash(
        manifest_run_id=run_id,
        machine_id=resolve_machine_id(db_path),
        db_path=db_path,
    )
    return _ok(run_id=run_id, result=result)


def _act_schedule_list(_db_path: Path, _params: dict[str, Any]) -> dict[str, Any]:
    rows = _list_schedule_rows()
    if rows and rows[0].get("error"):
        return _err(str(rows[0]["error"]), schedules=rows)
    return _ok(schedules=rows)


_CLI_HINTS: dict[str, str] = {
    "scan": "steward scan <root> [--workers N]",
    "watch": "steward watch <root>",
    "classify": "steward classify [--reclassify-all]",
    "status": "steward status [--quick] [--refresh] [--json]",
    "health": "steward health show | check [--fail-on …]",
    "stats": "steward stats | by-tier | by-domain | by-volume | cross | extensions | classifications | duplicates",
    "surface": "steward surface tree --prefix <path> [--color-by domain]",
    "policy_plan": "steward policy plan --policy retention.yml",
    "plans": "steward plans list | show | filter-dual-presence",
    "apply": "steward apply --manifest <path> --dry-run | --execute",
    "replicate": "steward replicate run --dry-run | --execute",
    "archive": "steward archive snapshot --dry-run | --execute",
    "stash_finalize": "steward stash finalize --run-id <id> --execute",
    "stash_restore": "steward stash restore --run-id <id> --execute",
    "fp": "steward fp status | dual-presence",
    "fleet": "steward machines health [--include-imports]",
    "machines": "steward machines list | show | health",
    "embed": "steward embed",
    "search": "steward search <query>",
    "photos": "steward photos inventory | plan",
    "schedule": "steward schedule list | install <name> --execute",
    "mcp": "steward mcp",
    "db_backup": "steward db backup",
    "db_verify": "steward db verify",
    "db_export": "steward db export | import",
}


def _act_cli_hint(_db_path: Path, params: dict[str, Any]) -> dict[str, Any]:
    op = str(params.get("op") or "").strip()
    hint = _CLI_HINTS.get(op)
    if hint is None:
        return _err(f"unknown op for cli_hint: {op}", known=sorted(_CLI_HINTS))
    extra = str(params.get("args") or "").strip()
    cmd = f"{hint} {extra}".strip() if extra else hint
    return _ok(op=op, command=cmd)


_DISPATCH: dict[str, ActionFn] = {
    "refresh_rollups": _act_refresh_rollups,
    "refresh_health": _act_refresh_health,
    "status_full": _act_status_full,
    "verify_chain": _act_verify_chain,
    "fp_status": _act_fp_status,
    "list_machines": _act_list_machines,
    "stats_overview": _act_stats_overview,
    "stats_axis": _act_stats_axis,
    "surface_tree": _act_surface_tree,
    "fleet_health": _act_fleet_health,
    "dual_presence_sample": _act_dual_presence_sample,
    "filter_plan_dual_presence": _act_filter_plan_dual_presence,
    "plan_show": _act_plan_show,
    "search_path": _act_search_path,
    "search_hash": _act_search_hash,
    "inspect": _act_inspect,
    "list_policies": _act_list_policies,
    "show_policy": _act_show_policy,
    "policy_plan": _act_policy_plan,
    "apply_dry_run": _act_apply_dry_run,
    "replicate_dry_run": _act_replicate_dry_run,
    "replicate_execute": _act_replicate_execute,
    "archive_dry_run": _act_archive_dry_run,
    "archive_execute": _act_archive_execute,
    "stash_finalize": _act_stash_finalize,
    "stash_restore": _act_stash_restore,
    "schedule_list": _act_schedule_list,
    "cli_hint": _act_cli_hint,
}


def build_plans_payload(
    *,
    status: str | None = None,
    policy: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Plan backlog list for ``GET /api/plans`` (ADR-0019)."""
    try:
        from steward.core.plans.model import plan_record_to_compact_dict
        from steward.infra.plans import list_plans

        records = list_plans(status=status, policy=policy, limit=limit)
        return _ok(
            plans=[plan_record_to_compact_dict(r) for r in records],
            count=len(records),
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("dashboard.api.plans", exc, context={})
        return _err(str(exc), plans=[], count=0)


def build_plan_detail(plan_id: str) -> dict[str, Any]:
    """One plan backlog record for ``GET /api/plans/<id>``."""
    try:
        from steward.core.plans.model import plan_record_to_dict
        from steward.infra.plans import show_plan

        rec = show_plan(plan_id)
        if rec is None:
            return _err(f"plan not found: {plan_id}")
        return _ok(plan=plan_record_to_dict(rec))
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("dashboard.api.plan_detail", exc, context={"plan_id": plan_id})
        return _err(str(exc))


def build_schedule_reliability_payload(*, probe: bool = False) -> dict[str, Any]:
    """Schedule reliability for ``GET /api/schedule/reliability``."""
    rows = _list_schedule_rows(probe=probe)
    if rows and rows[0].get("error"):
        return _err(str(rows[0]["error"]), schedules=rows)
    return _ok(schedules=rows, probe=probe, count=len(rows))


def build_queues_payload(
    db_path: Path,
    *,
    include_imports: bool = False,
    quick: bool = True,
    plan_limit: int = 25,
) -> dict[str, Any]:
    """Queues pane: open plans + stash + overdue schedules (ADR-0019)."""
    t0 = time.time()
    plans_payload = build_plans_payload(limit=plan_limit)
    plans = list(plans_payload.get("plans") or [])

    stash: dict[str, Any] = {}
    try:
        report = collect_status(db_path=db_path, include_imports=include_imports, quick=quick)
        stash = {
            "in_flight_entries": report.stash.in_flight_entries,
            "distinct_run_ids": report.stash.distinct_run_ids,
            "oldest_ts_iso": report.stash.oldest_ts_iso,
            "newest_ts_iso": report.stash.newest_ts_iso,
        }
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("dashboard.api.queues_stash", exc, context={"db": str(db_path)})
        stash = {"error": str(exc)}

    schedules = _list_schedule_rows(probe=False)
    overdue = [
        s
        for s in schedules
        if isinstance(s, dict) and s.get("overdue") is True
    ]
    not_installed = [
        s
        for s in schedules
        if isinstance(s, dict) and s.get("installed") is False
    ]

    open_statuses = {"registered", "blocked", "dry_run_ok", "dry_run_failed", "partially_applied"}
    open_plans = [p for p in plans if str(p.get("status")) in open_statuses]
    blocked_plans = [p for p in plans if p.get("blocked_reasons")]

    return _ok(
        plans=plans,
        open_plans=open_plans,
        blocked_plans=blocked_plans,
        stash=stash,
        schedules=schedules,
        overdue_schedules=overdue,
        not_installed_schedules=not_installed,
        elapsed_ms=int((time.time() - t0) * 1000),
    )





def build_fleet_payload(
    db_path: Path,
    *,
    include_imports: bool = True,
    quick: bool = True,
) -> dict[str, Any]:
    """Fleet health matrix for ``GET /api/fleet`` (ADR-0021)."""
    t0 = time.time()
    try:
        from steward.infra.db.settings import data_dir
        from steward.infra.fleet import collect_fleet_health, fleet_health_to_dict

        matrix = collect_fleet_health(
            db_path=db_path,
            include_imports=include_imports,
            quick=quick,
            data_dir=data_dir(),
        )
        payload = fleet_health_to_dict(matrix)
        return _ok(
            **payload,
            elapsed_ms=int((time.time() - t0) * 1000),
            generated_at_epoch=time.time(),
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "dashboard.api.fleet",
            exc,
            context={"db_path": str(db_path)},
        )
        return _err(str(exc), elapsed_ms=int((time.time() - t0) * 1000))


def dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, default=str).encode("utf-8")


__all__ = [
    "CONFIRM_EXECUTE",
    "action_catalog",
    "build_analysis_bundle",
    "build_fleet_payload",
    "build_health_payload",
    "build_health_series",
    "build_plan_detail",
    "build_plans_payload",
    "build_queues_payload",
    "build_schedule_reliability_payload",
    "build_stats_axis",
    "build_stats_overview",
    "build_status_payload",
    "dumps",
    "posture_from_status_report",
    "run_action",
]
