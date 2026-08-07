# SPDX-License-Identifier: Apache-2.0

"""Pure launchd cadence parse + overdue evaluation (ADR-0019 §5).

No FS / launchctl. Infra collectors feed plist dicts and last-run
timestamps into these helpers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping, Sequence

from steward.core.health.model import HealthLevel

CadenceKind = Literal["calendar", "interval", "unknown"]


@dataclass(frozen=True, slots=True)
class ScheduleCadence:
    """Normalized schedule cadence extracted from a launchd plist."""

    kind: CadenceKind
    weekday: int | None = None  # 0=Sun … 6=Sat (launchd convention)
    hour: int | None = None
    minute: int | None = None
    interval_seconds: int | None = None
    # Multi-window calendars: each entry is a partial dict of weekday/hour/minute
    windows: tuple[dict[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduleJobReliability:
    """Per-job reliability surface (installed / last exit / overdue)."""

    name: str
    label: str
    installed: bool
    loaded: bool | None
    cadence: ScheduleCadence
    last_exit_status: int | None
    last_exit_at: str | None
    last_start_at: str | None
    overdue: bool | None
    overdue_grace_hours: float
    level: HealthLevel
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def parse_cadence_from_plist_dict(d: Mapping[str, Any]) -> ScheduleCadence:
    """Extract :class:`ScheduleCadence` from a parsed launchd plist dict.

    Prefers ``StartCalendarInterval`` (dict or list of dicts), else
    ``StartInterval`` (seconds). Missing both → kind=unknown.
    """
    if not isinstance(d, Mapping):
        return ScheduleCadence(kind="unknown")

    cal = d.get("StartCalendarInterval")
    if cal is not None:
        return _parse_calendar(cal)

    interval = d.get("StartInterval")
    if interval is not None:
        try:
            secs = int(interval)
        except (TypeError, ValueError):
            return ScheduleCadence(kind="unknown")
        if secs <= 0:
            return ScheduleCadence(kind="unknown")
        return ScheduleCadence(kind="interval", interval_seconds=secs)

    return ScheduleCadence(kind="unknown")


def _parse_calendar(cal: Any) -> ScheduleCadence:
    windows: list[dict[str, int]] = []
    if isinstance(cal, Mapping):
        win = _window_from_dict(cal)
        if win:
            windows.append(win)
    elif isinstance(cal, Sequence) and not isinstance(cal, (str, bytes)):
        for item in cal:
            if isinstance(item, Mapping):
                win = _window_from_dict(item)
                if win:
                    windows.append(win)

    if not windows:
        return ScheduleCadence(kind="unknown")

    first = windows[0]
    return ScheduleCadence(
        kind="calendar",
        weekday=first.get("weekday"),
        hour=first.get("hour"),
        minute=first.get("minute"),
        windows=tuple(windows),
    )


def _window_from_dict(d: Mapping[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, out_key in (
        ("Weekday", "weekday"),
        ("Hour", "hour"),
        ("Minute", "minute"),
        ("Day", "day"),
        ("Month", "month"),
    ):
        if key in d and d[key] is not None:
            try:
                out[out_key] = int(d[key])
            except (TypeError, ValueError):
                continue
    return out


def default_grace_hours(cadence: ScheduleCadence) -> float:
    """Default overdue grace: 6h daily-ish, 24h weekly-ish."""
    if cadence.kind == "interval" and cadence.interval_seconds is not None:
        if cadence.interval_seconds <= 86400:
            return 6.0
        return 24.0
    if cadence.kind == "calendar":
        wins = cadence.windows or (
            {
                k: v
                for k, v in (
                    ("weekday", cadence.weekday),
                    ("hour", cadence.hour),
                    ("minute", cadence.minute),
                )
                if v is not None
            },
        )
        if any("weekday" in w for w in wins):
            return 24.0
        return 6.0
    return 12.0


def evaluate_overdue(
    cadence: ScheduleCadence,
    *,
    now: datetime,
    last_run_at: datetime | None,
    grace_hours: float | None = None,
    never_run: bool = False,
) -> bool | None:
    """Return whether the job is overdue vs cadence + grace.

    * ``None`` when cadence is unknown (cannot evaluate).
    * ``True`` when last run is older than period + grace, or when
      never run and the first expected window has already passed.
    * ``False`` when within cadence window.
    """
    if cadence.kind == "unknown":
        return None

    now_u = _as_utc(now)
    grace = grace_hours if grace_hours is not None else default_grace_hours(cadence)
    grace_td = timedelta(hours=float(grace))

    if cadence.kind == "interval":
        secs = cadence.interval_seconds or 0
        if secs <= 0:
            return None
        period = timedelta(seconds=secs)
        if last_run_at is None:
            return True if never_run else None
        last = _as_utc(last_run_at)
        return (now_u - last) > (period + grace_td)

    # calendar
    if last_run_at is None:
        # Look back one full period window; if a fire should already have
        # happened more than grace ago, treat as overdue when installed.
        lookback = now_u - timedelta(days=14)
        first = next_expected_fire(cadence, after=lookback)
        if first is None:
            return None
        # Overdue if that fire is in the past by more than grace.
        return first < (now_u - grace_td)

    last = _as_utc(last_run_at)
    nxt = next_expected_fire(cadence, after=last)
    if nxt is None:
        return None
    return nxt < (now_u - grace_td)


def next_expected_fire(
    cadence: ScheduleCadence,
    *,
    after: datetime,
    now: datetime | None = None,  # noqa: ARG001 — reserved for future
) -> datetime | None:
    """Next calendar fire strictly after ``after``.

    For multi-window calendars, returns the earliest next fire among windows.
    Searches up to ~400 days for weekday matches.
    """
    if cadence.kind == "interval" and cadence.interval_seconds:
        base = _as_utc(after)
        return base + timedelta(seconds=int(cadence.interval_seconds))
    if cadence.kind != "calendar":
        return None

    after_u = _as_utc(after)
    windows = list(cadence.windows)
    if not windows:
        win: dict[str, int] = {}
        if cadence.weekday is not None:
            win["weekday"] = cadence.weekday
        if cadence.hour is not None:
            win["hour"] = cadence.hour
        if cadence.minute is not None:
            win["minute"] = cadence.minute
        windows = [win] if win else []

    if not windows:
        return None

    candidates: list[datetime] = []
    for win in windows:
        fire = _next_window_fire(win, after=after_u)
        if fire is not None:
            candidates.append(fire)
    if not candidates:
        return None
    return min(candidates)


def _next_window_fire(win: Mapping[str, int], *, after: datetime) -> datetime | None:
    """Find next occurrence of hour/minute/(weekday) after ``after``."""
    hour = int(win.get("hour", 0))
    minute = int(win.get("minute", 0))
    weekday = win.get("weekday")  # 0=Sun … 6=Sat launchd; Python: 0=Mon … 6=Sun

    day = after.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(0, 400):
        candidate_day = day + timedelta(days=offset)
        if weekday is not None:
            # Convert launchd weekday (0=Sun) → Python weekday (0=Mon).
            py_wd = (int(weekday) - 1) % 7
            if candidate_day.weekday() != py_wd:
                continue
        if "day" in win and candidate_day.day != int(win["day"]):
            continue
        if "month" in win and candidate_day.month != int(win["month"]):
            continue
        fire = candidate_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if fire > after:
            return fire
    return None


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def reliability_to_dict(job: ScheduleJobReliability) -> dict[str, Any]:
    """JSON-stable serialization."""
    d = asdict(job)
    d["cadence"] = asdict(job.cadence)
    d["cadence"]["windows"] = [dict(w) for w in job.cadence.windows]
    return d


def level_for_job(
    *,
    installed: bool,
    loaded: bool | None,
    last_exit_status: int | None,
    overdue: bool | None,
    available: bool = True,
) -> tuple[HealthLevel, str]:
    """Derive level + message from reliability signals."""
    if not available:
        return "unknown", "schedule module / launchctl unavailable"
    if not installed:
        return "warn", "plist not installed"
    if last_exit_status is not None and last_exit_status != 0:
        return "fail", f"last exit status {last_exit_status}"
    if overdue is True:
        return "warn", "overdue vs cadence"
    if loaded is False:
        return "warn", "installed but not loaded"
    if overdue is None and loaded is None:
        return "ok" if installed else "unknown", "installed (exit/overdue not probed)"
    return "ok", "on cadence"


__all__ = [
    "CadenceKind",
    "ScheduleCadence",
    "ScheduleJobReliability",
    "default_grace_hours",
    "evaluate_overdue",
    "level_for_job",
    "next_expected_fire",
    "parse_cadence_from_plist_dict",
    "reliability_to_dict",
]
