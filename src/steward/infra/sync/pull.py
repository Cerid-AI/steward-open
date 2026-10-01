# SPDX-License-Identifier: Apache-2.0

"""Pull another estate host's published inventory export over ssh (ADR-0009, ADR-0013).

The estate primary pulls; clients never push, and a pull never writes on
the client. For a host with a ``publish`` block:

1. ``rsync`` the published envelope, and the published health directory,
   into ``<data_dir>/inbox/<host>/``.
2. Refuse (and audit ``fleet_pull_refused``) unless the envelope manifest's
   exporter ``machine_id`` is the one the estate pins for that host.
3. Hand the envelope to :func:`import_inventory` — blake3 + audit-chain
   verification, payload attached read-only.
4. Append a ``fleet_pull`` audit row.
"""

from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404 — fixed rsync argv, no shell
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from steward.core.estate import Estate
from steward.infra.db import repo_audit
from steward.infra.db.admin import read_machine_id
from steward.infra.db.connect import connect
from steward.infra.sync.importer import ImportResult, import_inventory
from steward.infra.sync.manifest import WireManifest

RSYNC = "rsync"
SSH_TRANSPORT = "ssh -o BatchMode=yes -o ConnectTimeout=20"
DEFAULT_TIMEOUT_SECONDS = 3600.0
ACTOR = "steward-fleet"
INBOX_DIRNAME = "inbox"
HEALTH_DIRNAME = "health"


class PullError(RuntimeError):
    """The pull could not complete (transport, unreadable envelope, no local DB)."""


class PullRefused(PullError):
    """The estate does not allow this pull, or the envelope is not from the expected machine."""


def inbox_dir(data_dir: Path, host_id: str) -> Path:
    return Path(data_dir) / INBOX_DIRNAME / host_id


def inbox_health_dir(data_dir: Path, host_id: str) -> Path:
    return inbox_dir(data_dir, host_id) / HEALTH_DIRNAME


@dataclass(frozen=True, slots=True)
class PullPlan:
    host_id: str
    ssh: str
    machine_id: str
    """The exporter machine_id the estate pins for ``host_id``."""
    remote_envelope: str
    remote_health_dir: str | None
    inbox: Path
    local_envelope: Path
    local_health_dir: Path | None
    envelope_command: tuple[str, ...]
    health_command: tuple[str, ...] | None

    @property
    def commands(self) -> tuple[tuple[str, ...], ...]:
        return (self.envelope_command,) + ((self.health_command,) if self.health_command else ())


@dataclass(frozen=True, slots=True)
class PullResult:
    plan: PullPlan
    imported: ImportResult | None
    """``None`` when ``unchanged``: the envelope's payload was already attached."""
    unchanged: bool
    envelope_bytes: int
    health_pulled: bool
    health_error: str | None
    duration_seconds: float


def rsync_command(ssh: str, remote: str, local: Path, *, directory: bool = False) -> tuple[str, ...]:
    """``rsync`` argv pulling ``ssh:remote`` to ``local``.

    The remote path is shell-quoted because rsync up to 3.2.3, and macOS's
    openrsync, hand it to the remote shell unprotected (estate paths contain
    spaces); :func:`run_rsync` sets ``RSYNC_OLD_ARGS`` so newer rsync agrees.
    """
    if directory:
        source = f"{ssh}:{shlex.quote(remote.rstrip('/') + '/')}"
        return (RSYNC, "-rt", "--partial", "-e", SSH_TRANSPORT, source, f"{local}/")
    return (RSYNC, "-t", "--partial", "-e", SSH_TRANSPORT, f"{ssh}:{shlex.quote(remote)}", str(local))


def plan_pull(estate: Estate, *, me: str | None, host_id: str, data_dir: Path) -> PullPlan:
    """What a pull of ``host_id`` would do from host ``me``; :class:`PullRefused` when not allowed."""
    if me is None:
        raise PullRefused("this machine is not an estate host; only the primary pulls")
    if estate.hosts[me].role != "primary":
        raise PullRefused(f"host {me!r} is a {estate.hosts[me].role}; only the estate primary pulls")
    if host_id not in estate.hosts:
        raise PullRefused(f"{host_id!r} is not a declared host (declared: {', '.join(sorted(estate.hosts))})")
    if host_id == me:
        raise PullRefused(f"{host_id!r} is this host; there is nothing to pull")
    host = estate.hosts[host_id]
    if host.publish is None:
        raise PullRefused(f"host {host_id!r} has no publish block in the estate")
    if host.machine_id is None:
        raise PullRefused(f"host {host_id!r} pins no machine_id; the pulled envelope could not be verified")
    publish = host.publish
    inbox = inbox_dir(data_dir, host_id)
    local_envelope = inbox / PurePosixPath(publish.envelope).name
    local_health = inbox_health_dir(data_dir, host_id) if publish.health_dir else None
    return PullPlan(
        host_id=host_id,
        ssh=publish.ssh,
        machine_id=host.machine_id,
        remote_envelope=publish.envelope,
        remote_health_dir=publish.health_dir,
        inbox=inbox,
        local_envelope=local_envelope,
        local_health_dir=local_health,
        envelope_command=rsync_command(publish.ssh, publish.envelope, local_envelope),
        health_command=(
            rsync_command(publish.ssh, publish.health_dir, local_health, directory=True)
            if publish.health_dir and local_health is not None
            else None
        ),
    )


def run_rsync(argv: tuple[str, ...], *, timeout: float) -> None:
    """Run one rsync; :class:`PullError` on a non-zero exit, a timeout, or no rsync binary."""
    env = {**os.environ, "RSYNC_OLD_ARGS": "1"}
    try:
        proc = subprocess.run(  # nosec B603 — argv built by rsync_command, no shell
            list(argv), check=False, capture_output=True, text=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired as exc:
        raise PullError(f"rsync timed out after {timeout:g}s: {argv[-2]}") from exc
    except OSError as exc:
        raise PullError(f"cannot run {argv[0]}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else "no output"
        raise PullError(f"rsync exited {proc.returncode} pulling {argv[-2]}: {tail}")


def envelope_manifest(envelope: Path) -> WireManifest:
    """An envelope's ``manifest.json``, read without unpacking the payload."""
    try:
        with tarfile.open(envelope, "r:xz") as tar:
            member = tar.extractfile("manifest.json")
            if member is None:
                raise PullError(f"{envelope}: manifest.json is not a regular file")
            raw = member.read()
    except (OSError, KeyError, tarfile.TarError) as exc:
        raise PullError(f"{envelope}: cannot read manifest.json: {exc}") from exc
    try:
        return WireManifest.model_validate_json(raw)
    except ValueError as exc:
        raise PullError(f"{envelope}: manifest.json failed to parse: {exc}") from exc


def envelope_machine_id(envelope: Path) -> str:
    """The exporter ``machine_id`` recorded in an envelope's ``manifest.json``."""
    return envelope_manifest(envelope).exporter.machine_id


def _already_attached(db_path: Path, manifest: WireManifest) -> bool:
    """True when this exact payload is the one attached for its exporter and still on disk."""
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        row = con.execute(
            "SELECT payload_blake3, file_path FROM attached_inventories WHERE machine_id = ?",
            (manifest.exporter.machine_id,),
        ).fetchone()
    finally:
        con.close()
    return row is not None and row[0] == manifest.payload.blake3 and Path(row[1]).is_file()


def _audit(db_path: Path, *, local_machine_id: str, action: str, payload: dict[str, Any]) -> None:
    con = connect(db_path, read_only=False, load_vec=False)
    try:
        repo_audit.append(con, machine_id=local_machine_id, actor=ACTOR, action=action, payload=payload)
        con.commit()
    finally:
        con.close()


def execute_pull(
    plan: PullPlan,
    *,
    db_path: Path,
    imports_dir: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> PullResult:
    """Pull, verify the exporter, import. Writes only under ``plan.inbox``, ``imports_dir`` and ``db_path``.

    A failed health-directory pull does not block the inventory import; it is
    reported on the result and in the audit row.
    """
    local_machine_id = read_machine_id(db_path)
    if local_machine_id is None:
        raise PullError(f"local inventory.db missing at {db_path}. Run `steward db migrate` first.")
    started = time.monotonic()
    plan.inbox.mkdir(parents=True, exist_ok=True)
    run_rsync(plan.envelope_command, timeout=timeout)

    health_error: str | None = None
    if plan.health_command is not None and plan.local_health_dir is not None:
        plan.local_health_dir.mkdir(parents=True, exist_ok=True)
        try:
            run_rsync(plan.health_command, timeout=timeout)
        except PullError as exc:
            health_error = str(exc)

    manifest = envelope_manifest(plan.local_envelope)
    exporter = manifest.exporter.machine_id
    if exporter != plan.machine_id:
        _audit(
            db_path,
            local_machine_id=local_machine_id,
            action="fleet_pull_refused",
            payload={
                "host": plan.host_id,
                "ssh": plan.ssh,
                "envelope": plan.remote_envelope,
                "expected_machine_id": plan.machine_id,
                "envelope_machine_id": exporter,
                "reason": "machine_id_mismatch",
            },
        )
        raise PullRefused(
            f"envelope pulled from {plan.host_id!r} was exported by machine_id {exporter}, "
            f"but the estate pins {plan.machine_id}; not imported"
        )

    # The client exports weekly and the primary pulls daily: an envelope whose
    # payload is already attached is not unpacked and re-imported again.
    unchanged = _already_attached(db_path, manifest)
    imported = (
        None
        if unchanged
        else import_inventory(envelope_path=plan.local_envelope, db_path=db_path, imports_dir=imports_dir)
    )
    envelope_bytes = plan.local_envelope.stat().st_size
    duration = time.monotonic() - started
    _audit(
        db_path,
        local_machine_id=local_machine_id,
        action="fleet_pull",
        payload={
            "host": plan.host_id,
            "ssh": plan.ssh,
            "envelope": plan.remote_envelope,
            "local_envelope": str(plan.local_envelope),
            "envelope_bytes": envelope_bytes,
            "exporter_machine_id": exporter,
            "unchanged": unchanged,
            "payload_path": str(imported.payload_path) if imported else None,
            "payload_blake3": manifest.payload.blake3,
            "replaced_existing": imported.replaced_existing if imported else None,
            "health_pulled": plan.health_command is not None and health_error is None,
            "health_error": health_error,
            "duration_seconds": round(duration, 3),
        },
    )
    return PullResult(
        plan=plan,
        imported=imported,
        unchanged=unchanged,
        envelope_bytes=envelope_bytes,
        health_pulled=plan.health_command is not None and health_error is None,
        health_error=health_error,
        duration_seconds=duration,
    )


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "PullError",
    "PullPlan",
    "PullRefused",
    "PullResult",
    "envelope_machine_id",
    "execute_pull",
    "inbox_dir",
    "inbox_health_dir",
    "plan_pull",
    "rsync_command",
    "run_rsync",
]
