# SPDX-License-Identifier: Apache-2.0

"""Thin subprocess wrapper around the ``rclone`` CLI.

Steward shells out to ``rclone`` once per :class:`ReplicationSource`.
This module owns the command-building + the subprocess invocation; the
runner in :mod:`steward.infra.replicate.runner` owns the policy walk
and audit-log bracketing.

``rclone`` is expected on ``PATH``. When it isn't, the wrapper raises
:class:`RcloneNotInstalledError` so the CLI can surface a friendly
install hint instead of a confusing ``FileNotFoundError``.

The command-builder explicitly sets ``--use-json-log`` whenever rclone
is invoked so the runner can parse structured stats from stderr (rclone
writes its JSON log lines to stderr by default).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from steward.core.policy.schema import ReplicationDefaults, ReplicationSource


class RcloneNotInstalledError(FileNotFoundError):
    """Raised when ``rclone`` isn't on ``PATH``.

    Convert to a one-liner hint in the CLI: ``brew install rclone`` or
    ``curl https://rclone.org/install.sh | sudo bash``.
    """


@dataclass(frozen=True, slots=True)
class RcloneRunResult:
    """Outcome of one ``rclone`` invocation.

    Attributes
    ----------
    returncode:
        The subprocess exit code. 0 = success; non-zero = the wrapper
        propagates it without re-running.
    duration_seconds:
        Wall-clock duration of the invocation.
    stdout:
        Captured stdout (usually empty for a copy/sync — rclone logs to
        stderr by default).
    stderr_tail:
        Last ~4 KiB of stderr. Full stderr can be very large for a
        substantial sync; the tail is enough for operator triage and
        keeps audit-log entries reasonable.
    stats:
        Parsed ``rclone --stats`` summary from the final JSON log line,
        when present. Keys include ``bytes``, ``transfers``, ``errors``,
        ``checks``, ``elapsedTime``. Empty dict if rclone produced no
        parseable stats (e.g. immediate failure).
    command:
        The exact argv that ran. Useful for audit-log replay.
    timed_out:
        True iff the subprocess was killed by the per-call timeout.
    """

    returncode: int
    duration_seconds: float
    stdout: str
    stderr_tail: str
    stats: dict[str, int | float]
    command: tuple[str, ...]
    timed_out: bool = False


def rclone_available(rclone_bin: str = "rclone") -> bool:
    """Return ``True`` when ``rclone_bin`` resolves on ``PATH``.

    The runner uses this to decide between "skip with a structured log
    entry" (Linux CI without rclone) and "fail loudly" (operator asked
    for rclone but we don't have it). The CLI uses it to short-circuit
    with the install hint before any side-effects.
    """
    return shutil.which(rclone_bin) is not None


def _build_argv(
    *,
    defaults: ReplicationDefaults,
    source: ReplicationSource,
    dry_run: bool,
    mode: Literal["copy", "sync"] | None = None,
) -> list[str]:
    """Compose the argv for one rclone invocation.

    ``mode`` overrides the source's mode when supplied (useful for
    testing). When omitted, ``source.mode`` decides.
    """
    chosen_mode = mode or source.mode
    argv: list[str] = [
        defaults.rclone_bin,
        chosen_mode,
        source.source,
        source.destination,
        "--use-json-log",
        "--stats",
        "1m",  # emit stats every minute
        "--stats-one-line",
        "--stats-log-level",
        "NOTICE",
        "--transfers",
        str(defaults.transfers),
        "--checkers",
        str(defaults.checkers),
    ]
    if dry_run:
        argv.append("--dry-run")
    for pat in source.includes:
        argv.extend(["--include", pat])
    for pat in source.excludes:
        argv.extend(["--exclude", pat])
    argv.extend(defaults.extra_args)
    return argv


def _parse_stats(stderr_text: str) -> dict[str, int | float]:
    """Extract the last successful rclone JSON-log stats summary.

    rclone with ``--use-json-log`` emits one JSON object per line on
    stderr. The final-stats object has a ``"stats"`` key whose value is
    a mapping. We scan backwards and pick the most recent ``stats``
    block.
    """
    for line in reversed(stderr_text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        stats = obj.get("stats")
        if isinstance(stats, dict):
            # Only keep numeric fields — rclone occasionally embeds
            # arrays (e.g. errors_list) that aren't useful here.
            cleaned: dict[str, int | float] = {}
            for k, v in stats.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    cleaned[str(k)] = v
            return cleaned
    return {}


def run_rclone(
    *,
    defaults: ReplicationDefaults,
    source: ReplicationSource,
    dry_run: bool,
) -> RcloneRunResult:
    """Run one rclone invocation. Raises :class:`RcloneNotInstalledError`
    if the binary isn't on ``PATH``.
    """
    if not rclone_available(defaults.rclone_bin):
        raise RcloneNotInstalledError(
            f"`{defaults.rclone_bin}` not on PATH. "
            "Install with: brew install rclone "
            "(or see https://rclone.org/install/)."
        )
    argv = _build_argv(defaults=defaults, source=source, dry_run=dry_run)
    return _execute(argv, timeout=defaults.timeout_seconds)


def _execute(
    argv: Sequence[str], *, timeout: int
) -> RcloneRunResult:
    """Run an rclone argv with a hard timeout. Catches the timeout and
    returns a structured result rather than propagating the exception."""
    import time

    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        rc = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        rc = -1
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    duration = time.monotonic() - started

    # Keep stderr tail short for audit-log payload friendliness.
    tail = stderr[-4096:]
    stats = _parse_stats(stderr)
    return RcloneRunResult(
        returncode=rc,
        duration_seconds=duration,
        stdout=stdout,
        stderr_tail=tail,
        stats=stats,
        command=tuple(argv),
        timed_out=timed_out,
    )


__all__ = [
    "RcloneNotInstalledError",
    "RcloneRunResult",
    "rclone_available",
    "run_rclone",
]
