# SPDX-License-Identifier: Apache-2.0

"""Centralised Sentry init. No-op when ``SENTRY_DSN`` is unset — privacy-first default.

Adapted from Cerid's ``app.observability.sentry_init`` with the FastAPI /
Starlette / Httpx / Redis integrations stripped (v0.1 Steward is CLI-only;
those will return in v0.2 alongside the FastAPI dashboard + MCP server).
Kept:

- ``LoggingIntegration`` so structlog records become Sentry breadcrumbs
- ``EventScrubber`` extended with the same provider-key denylist
- Per-fingerprint rate limiter — useful even for CLI invocations because a
  pathological filesystem condition can fire the same error thousands of
  times in a single scan
- ``_traces_sampler`` — trivial for v0.1 (no transactions); kept as scaffold
  for v0.2 daemon traffic
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from threading import Lock
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.types import Event, Hint

# Provider API keys not covered by DEFAULT_DENYLIST (which only matches
# generic "api_key"). Steward proper does not call provider APIs in v0.1,
# but the denylist is cheap insurance against an operator script that
# happens to set one of these in the same process.
_EXTRA_DENYLIST = [
    "openrouter_api_key",
    "anthropic_api_key",
    "xai_api_key",
    "openai_api_key",
    "X-API-Key",
    "cookies",
    "set-cookie",
    "x-session-id",
]

# Per-fingerprint rate limit. Sentry server-side already groups duplicate
# events into one issue, but each event still costs quota — and a single
# filesystem regression in a tight scan loop can burn thousands of events
# in minutes. Cap each fingerprint at MAX events per WINDOW seconds.
_RATE_LIMIT_WINDOW_S = 60
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_TTL_S = 600  # drop fingerprints idle longer than this (memory cap)
_rate_limit_state: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = Lock()


def _event_fingerprint(event: Event) -> str:
    """Stable key per error class — mirrors Sentry's grouping, simplified."""
    custom = event.get("fingerprint")
    if custom:
        return ":".join(str(c) for c in custom)
    exc_values = (event.get("exception") or {}).get("values") or []
    exc_type = exc_values[0].get("type", "") if exc_values else ""
    return f"{exc_type}|{event.get('transaction', '')}"


def _rate_limited(event: Event) -> bool:
    """Return True when the event's fingerprint already fired
    ``_RATE_LIMIT_MAX`` times within the last ``_RATE_LIMIT_WINDOW_S``
    seconds. Side-effect: prunes expired timestamps and drops idle
    fingerprints so the state dict stays bounded.
    """
    fp = _event_fingerprint(event)
    now = time.time()
    with _rate_limit_lock:
        ts_list = _rate_limit_state[fp]
        ts_list[:] = [t for t in ts_list if now - t < _RATE_LIMIT_WINDOW_S]
        if len(ts_list) >= _RATE_LIMIT_MAX:
            return True
        ts_list.append(now)
        for stale in [
            k for k, v in _rate_limit_state.items() if v and now - v[-1] > _RATE_LIMIT_TTL_S
        ]:
            del _rate_limit_state[stale]
        return False


def _before_send(event: Event, hint: Hint) -> Event | None:
    """Drop events that exceed the per-fingerprint rate limit. Returning
    ``None`` from a Sentry ``before_send`` hook discards the event entirely
    (no transport, no quota cost).
    """
    del hint
    if _rate_limited(event):
        return None
    return event


def _traces_sampler(sampling_context: dict[str, Any]) -> float:
    """Per-transaction sample-rate decision.

    Trivial in v0.1 — there are no FastAPI transactions yet; this exists as
    a scaffold so the v0.2 daemon traffic can override without touching the
    init shape. Honors ``STEWARD_TRACES_SAMPLE_RATE`` (default 0.1).
    """
    del sampling_context
    return float(os.getenv("STEWARD_TRACES_SAMPLE_RATE", "0.1"))


def init_sentry() -> bool:
    """Initialise Sentry. No-op when ``SENTRY_DSN`` is empty.

    Returns ``True`` iff Sentry was actually initialised. Privacy-first
    default: a fresh Steward install with no DSN configured emits zero
    network traffic. Set ``SENTRY_DSN_STEWARD`` (preferred) or
    ``SENTRY_DSN`` to opt in.
    """
    dsn = os.getenv("SENTRY_DSN_STEWARD") or os.getenv("SENTRY_DSN")
    if not dsn:
        return False

    # Suppress noisy third-party loggers here when needed:
    _ignored_loggers: tuple[str, ...] = ()
    for logger_name in _ignored_loggers:
        ignore_logger(logger_name)

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("STEWARD_ENVIRONMENT")
        or os.getenv("SENTRY_ENVIRONMENT", "development"),
        release=os.getenv("STEWARD_VERSION") or os.getenv("SENTRY_RELEASE"),
        traces_sampler=_traces_sampler,
        before_send=_before_send,
        profiles_sample_rate=float(os.getenv("STEWARD_PROFILES_SAMPLE_RATE", "0.0")),
        send_default_pii=False,
        event_scrubber=EventScrubber(
            denylist=DEFAULT_DENYLIST + _EXTRA_DENYLIST,
            recursive=True,
        ),
        integrations=[
            LoggingIntegration(level=None, event_level=None),
        ],
        max_breadcrumbs=50,
        enable_logs=True,
    )
    return True
