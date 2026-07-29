# SPDX-License-Identifier: Apache-2.0

"""Thin subprocess wrapper around the ``restic`` CLI.

Steward invokes ``restic`` for three operations: ``init`` (one-time per
repository), ``backup`` (one per :class:`ArchiveSource`), and
``snapshots`` (listing). The wrapper owns argv construction, password
plumbing (via env vars — never on the command line), and result
parsing. The runner in :mod:`steward.infra.archive.runner` owns policy
iteration + audit bracketing.

Password handling:

* ``defaults.password_command`` → ``RESTIC_PASSWORD_COMMAND`` env var.
  restic shells out to it once per invocation.
* ``defaults.password_file`` → ``RESTIC_PASSWORD_FILE`` env var.
* Neither: the user gets a non-zero exit + restic's own complaint.
  Steward never reads / echoes / persists the actual password.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Literal

from steward.core.policy.schema import ArchiveDefaults, ArchiveSource


class ResticNotInstalledError(FileNotFoundError):
    """Raised when ``restic`` isn't on ``PATH``.

    The CLI converts this into an install hint
    (``brew install restic`` / ``apt install restic``).
    """


@dataclass(frozen=True, slots=True)
class ResticRunResult:
    """Outcome of one ``restic`` invocation.

    Attributes
    ----------
    op:
        Which restic subcommand was run (``init`` / ``backup`` /
        ``snapshots``).
    returncode:
        Subprocess exit code. 0 = success.
    duration_seconds:
        Wall-clock duration of the invocation.
    summary:
        Parsed final-message JSON for backups (keys like
        ``snapshot_id``, ``files_new``, ``data_added``,
        ``total_bytes_processed``). For ``init`` it's the parsed
        repository details. For ``snapshots`` callers should look at
        :attr:`snapshots` instead.
    snapshots:
        Parsed JSON for ``restic snapshots --json``. Empty for
        ``init`` / ``backup``.
    stdout:
        Captured stdout (restic emits JSON here when ``--json`` is set).
    stderr_tail:
        Last ~4 KiB of stderr.
    command:
        The exact argv that ran (passwords never appear on the command
        line, so this is safe to log).
    timed_out:
        True iff the subprocess was killed by the per-call timeout.
    """

    op: Literal["init", "backup", "snapshots"]
    returncode: int
    duration_seconds: float
    summary: dict[str, Any] = field(default_factory=dict)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    stdout: str = ""
    stderr_tail: str = ""
    command: tuple[str, ...] = ()
    timed_out: bool = False


def restic_available(restic_bin: str = "restic") -> bool:
    """Return ``True`` when ``restic_bin`` resolves on ``PATH``."""
    return shutil.which(restic_bin) is not None


def _build_env(defaults: ArchiveDefaults) -> dict[str, str]:
    """Compose the subprocess env: parent env + password plumbing."""
    env = dict(os.environ)
    if defaults.password_command:
        env["RESTIC_PASSWORD_COMMAND"] = defaults.password_command
    if defaults.password_file:
        env["RESTIC_PASSWORD_FILE"] = defaults.password_file
    return env


def _common_argv(defaults: ArchiveDefaults) -> list[str]:
    """Argv prefix for any restic invocation: binary + JSON output."""
    return [defaults.restic_bin]


def _execute(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: int,
    op: Literal["init", "backup", "snapshots"],
) -> ResticRunResult:
    """Run a restic argv with a hard timeout. Catches the timeout +
    returns a structured :class:`ResticRunResult` rather than re-raising.
    Parses the final-message JSON for ``backup`` and the snapshots list
    for ``snapshots`` (when ``--json`` was requested).
    """
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
            env=env,
        )
        rc = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        rc = -1
        stdout = (
            (exc.stdout or b"").decode("utf-8", errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr = (
            (exc.stderr or b"").decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
    duration = time.monotonic() - started

    summary: dict[str, Any] = {}
    snapshots: list[dict[str, Any]] = []

    if op == "backup":
        summary = _parse_backup_summary(stdout)
    elif op == "snapshots":
        snapshots = _parse_snapshots_list(stdout)
    elif op == "init":
        summary = _parse_init_summary(stdout)

    return ResticRunResult(
        op=op,
        returncode=rc,
        duration_seconds=duration,
        summary=summary,
        snapshots=snapshots,
        stdout=stdout,
        stderr_tail=stderr[-4096:],
        command=tuple(argv),
        timed_out=timed_out,
    )


def _parse_backup_summary(stdout: str) -> dict[str, Any]:
    """Extract the ``message_type=summary`` JSON object from
    ``restic backup --json`` output. Restic emits one JSON object per
    line; the summary is the last one.
    """
    last_summary: dict[str, Any] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("message_type") == "summary":
            last_summary = obj
    return last_summary


def _parse_snapshots_list(stdout: str) -> list[dict[str, Any]]:
    """Parse the JSON array emitted by ``restic snapshots --json``.

    Falls back to empty list when stdout isn't a JSON array.
    """
    stripped = stdout.strip()
    if not stripped:
        return []
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    if isinstance(obj, list):
        return [s for s in obj if isinstance(s, dict)]
    return []


def _parse_init_summary(stdout: str) -> dict[str, Any]:
    """Parse the JSON object from ``restic init --json``.

    Restic emits one object describing the new repo on success.
    """
    stripped = stdout.strip()
    if not stripped:
        return {}
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        # Some restic builds print plain text instead of JSON.
        return {}
    return obj if isinstance(obj, dict) else {}


# ─────────────────────── operations ──────────────────────────


def _require_restic(defaults: ArchiveDefaults) -> None:
    if not restic_available(defaults.restic_bin):
        raise ResticNotInstalledError(
            f"`{defaults.restic_bin}` not on PATH. "
            "Install with: brew install restic (or apt install restic; "
            "see https://restic.readthedocs.io/)."
        )


def run_restic_init(
    *,
    defaults: ArchiveDefaults,
    repository: str,
) -> ResticRunResult:
    """``restic init -r <repository>``. Creates the encrypted repo."""
    _require_restic(defaults)
    argv = [
        *_common_argv(defaults),
        "init",
        "-r",
        repository,
        "--json",
        *defaults.extra_args,
    ]
    env = _build_env(defaults)
    return _execute(
        argv,
        env=env,
        timeout=defaults.timeout_seconds,
        op="init",
    )


def run_restic_backup(
    *,
    defaults: ArchiveDefaults,
    source: ArchiveSource,
    dry_run: bool,
) -> ResticRunResult:
    """``restic backup <source> -r <repo> --json [--dry-run] --tag …``."""
    _require_restic(defaults)
    tags = list(source.tags)
    if source.name not in tags:
        tags = [source.name, *tags]
    argv: list[str] = [
        *_common_argv(defaults),
        "backup",
        source.source,
        "-r",
        source.repository,
        "--json",
    ]
    if dry_run:
        argv.append("--dry-run")
    if source.exclude_caches:
        argv.append("--exclude-caches")
    for pat in source.excludes:
        argv.extend(["--exclude", pat])
    for tag in tags:
        argv.extend(["--tag", tag])
    argv.extend(defaults.extra_args)

    env = _build_env(defaults)
    return _execute(
        argv,
        env=env,
        timeout=defaults.timeout_seconds,
        op="backup",
    )


def run_restic_snapshots(
    *,
    defaults: ArchiveDefaults,
    repository: str,
    tags: list[str] | None = None,
) -> ResticRunResult:
    """``restic snapshots -r <repo> --json [--tag …]``."""
    _require_restic(defaults)
    argv: list[str] = [
        *_common_argv(defaults),
        "snapshots",
        "-r",
        repository,
        "--json",
    ]
    for tag in tags or []:
        argv.extend(["--tag", tag])
    argv.extend(defaults.extra_args)

    env = _build_env(defaults)
    return _execute(
        argv,
        env=env,
        timeout=defaults.timeout_seconds,
        op="snapshots",
    )


__all__ = [
    "ResticNotInstalledError",
    "ResticRunResult",
    "restic_available",
    "run_restic_backup",
    "run_restic_init",
    "run_restic_snapshots",
]
