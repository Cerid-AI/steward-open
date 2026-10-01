# SPDX-License-Identifier: Apache-2.0

"""File Provider per-domain sync health via ``fileproviderctl dump``.

Collector for the check born on 2026-08-16: the iCloud Drive domain had been
in a fetch-content retry loop since ~July 1 — ``error generation: 628``,
thousands of ``itemNotFound/missingLastKnownVersion`` errors, an upload
permanently in flight — invisibly staging transfer data into nsurlsessiond's
DataVault at ~10-20 GB/day until the boot volume was hours from full. Nothing
watched fileproviderd's own health counters, so a defect the daemon itself
was counting out loud went unread for 46 days.

Best-effort: fileproviderctl may be absent (non-macOS), slow, or its output
format may drift across OS releases. Every failure path returns ``None`` —
"not collected" — never an empty-but-healthy result. The caller renders
``None`` as a ``skipped`` check; an EMPTY parse of a dump that ran is
returned as ``[]`` and graded ``unknown`` by the evaluator, because a parser
that silently stopped matching a drifted format must not read as an estate
with no domains. See ``check_fp_sync_stuck``.
"""

from __future__ import annotations

import re
import subprocess

from steward.core.health.evaluate import fp_domain_level
from steward.core.health.model import FPDomainHealth
from steward.core.health.thresholds import DEFAULT_THRESHOLDS, HealthThresholds
from steward.infra.observability.swallowed import log_swallowed_error

# `fileproviderctl dump -l` line shapes this parser feeds on (real capture,
# 2026-08-16, macOS 26.x — obfuscated identifiers are how the tool prints):
#
#   domain: (default) (hidden)
#   domain: 9{34}9 (i{9}e)
#       + error generation: 628
#       pending-indexable-count: 4379
#       i:docID(3373) fetch-content: 🔶 last:'...' count:1 error:'NSError: ...'
_DOMAIN_RE = re.compile(r"^domain:\s+(\S+)\s*(?:\((.*?)\))?")
_ERROR_GEN_RE = re.compile(r"\berror generation:\s+(\d+)")
_PENDING_RE = re.compile(r"\bpending-indexable-count:\s+(\d+)")
_STUCK_RE = re.compile(r"\bfetch-content:.*\berror:")

_DUMP_TIMEOUT_S = 120


def collect_fp_domains(
    *,
    thresholds: HealthThresholds | None = None,
    timeout_s: int = _DUMP_TIMEOUT_S,
) -> list[FPDomainHealth] | None:
    """Parse per-domain sync health out of ``fileproviderctl dump -l``.

    Returns ``None`` when the dump could not be obtained at all (tool
    missing, timeout, crash) and a — possibly empty — list when it ran.
    """
    try:
        proc = subprocess.run(
            ["fileproviderctl", "dump", "-l"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired as exc:
        log_swallowed_error("health.fp_domains.timeout", exc, context={})
        return None
    except Exception as exc:  # noqa: BLE001 — best-effort health section
        log_swallowed_error("health.fp_domains.run", exc, context={})
        return None
    if proc.returncode != 0 and not proc.stdout:
        return None
    return parse_fp_dump(proc.stdout, thresholds=thresholds)


class _DomainAccumulator:
    """Mutable per-domain counters while streaming the dump."""

    def __init__(self, domain: str) -> None:
        self.domain = domain
        self.error_generation: int | None = None
        self.pending: int | None = None
        self.stuck = 0


def parse_fp_dump(
    text: str,
    *,
    thresholds: HealthThresholds | None = None,
) -> list[FPDomainHealth]:
    """Pure-ish parser split out so tests can feed it captured dumps."""
    thr = thresholds or DEFAULT_THRESHOLDS

    domains: list[_DomainAccumulator] = []
    current: _DomainAccumulator | None = None
    for line in text.splitlines():
        m = _DOMAIN_RE.match(line.strip()) if line.startswith("domain:") else None
        if m:
            current = _DomainAccumulator(str(m.group(2) or m.group(1)))
            domains.append(current)
            continue
        if current is None:
            continue
        gen = _ERROR_GEN_RE.search(line)
        if gen:
            # A domain section can print more than one generation counter
            # (per-backend); keep the WORST — the loop is whichever counter
            # is climbing.
            value = int(gen.group(1))
            if current.error_generation is None or value > current.error_generation:
                current.error_generation = value
            continue
        pending = _PENDING_RE.search(line)
        if pending:
            current.pending = int(pending.group(1))
            continue
        if _STUCK_RE.search(line):
            current.stuck += 1

    out: list[FPDomainHealth] = []
    for d in domains:
        level = fp_domain_level(d.error_generation, thresholds=thr)
        # The hidden default domain prints no counters; grading it "unknown"
        # forever would make every healthy host nag. No counters AND no stuck
        # errors is the quiet shape it always has — treat as ok.
        if d.error_generation is None and d.stuck == 0:
            level = "ok"
        out.append(
            FPDomainHealth(
                domain=d.domain,
                error_generation=d.error_generation,
                pending_indexable=d.pending,
                stuck_errors=d.stuck,
                level=level,
                message=(
                    f"error generation {d.error_generation}"
                    if d.error_generation is not None and level in ("warn", "fail")
                    else ""
                ),
            )
        )
    return out


__all__ = ["collect_fp_domains", "parse_fp_dump"]
