# SPDX-License-Identifier: Apache-2.0
"""Plan tokens for MCP apply_execute (ADR-0016).

``apply_dry_run`` issues a short-lived token bound to the manifest
content digest. ``apply_execute`` validates the token before mutation
and only consumes it after a successful execute path (or after FS
mutation begins). Preflight failures leave the token usable.

Tokens live under ``<data_dir>/runs/mcp-plan-tokens/`` as mode-600
JSON files (not in the inventory DB — avoid write contention with
long scans).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from steward.infra.db.settings import data_dir

_DEFAULT_TTL_S = 2 * 60 * 60  # 2 hours
_TOKEN_DIR_NAME = "mcp-plan-tokens"


@dataclass(frozen=True)
class PlanTokenRecord:
    token: str
    manifest_path: str
    manifest_sha256: str
    machine_id: str
    issued_at: float
    expires_at: float
    rows_total: int
    rows_applied: int
    max_files: int | None
    dry_run_errors: int


class PlanTokenError(Exception):
    """Token missing, expired, or does not match the manifest."""


def _token_dir() -> Path:
    d = data_dir() / "runs" / _TOKEN_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def manifest_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _record_from_dict(data: dict[str, Any]) -> PlanTokenRecord:
    return PlanTokenRecord(
        token=str(data["token"]),
        manifest_path=str(data["manifest_path"]),
        manifest_sha256=str(data["manifest_sha256"]),
        machine_id=str(data["machine_id"]),
        issued_at=float(data["issued_at"]),
        expires_at=float(data["expires_at"]),
        rows_total=int(data["rows_total"]),
        rows_applied=int(data["rows_applied"]),
        max_files=data.get("max_files"),
        dry_run_errors=int(data.get("dry_run_errors", 0)),
    )


def issue_plan_token(
    *,
    manifest_path: Path,
    machine_id: str,
    rows_total: int,
    rows_applied: int,
    max_files: int | None,
    dry_run_errors: int,
    ttl_s: int = _DEFAULT_TTL_S,
) -> PlanTokenRecord:
    """Create and persist a plan token for a successful dry-run."""
    resolved = manifest_path.resolve()
    digest = manifest_sha256(resolved)
    now = time.time()
    token = secrets.token_urlsafe(24)
    rec = PlanTokenRecord(
        token=token,
        manifest_path=str(resolved),
        manifest_sha256=digest,
        machine_id=machine_id,
        issued_at=now,
        expires_at=now + max(60, int(ttl_s)),
        rows_total=rows_total,
        rows_applied=rows_applied,
        max_files=max_files,
        dry_run_errors=dry_run_errors,
    )
    path = _token_dir() / f"{token}.json"
    path.write_text(json.dumps(asdict(rec), indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return rec


def validate_plan_token(
    *,
    token: str,
    manifest_path: Path,
    machine_id: str,
    max_files: int,
) -> PlanTokenRecord:
    """Validate token without consuming it.

    Enforces content binding and that execute ``max_files`` does not
    exceed the dry-run ``max_files`` when the dry-run set a bound.
    """
    if not token or not token.strip():
        raise PlanTokenError("plan_token is required for apply_execute")
    token = token.strip()
    path = _token_dir() / f"{token}.json"
    if not path.exists():
        raise PlanTokenError("plan_token not found or already consumed — re-run apply_dry_run")
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanTokenError(f"plan_token unreadable: {exc}") from exc

    rec = _record_from_dict(data)
    now = time.time()
    if now > rec.expires_at:
        path.unlink(missing_ok=True)
        raise PlanTokenError("plan_token expired — re-run apply_dry_run (default TTL 2h)")
    if rec.machine_id != machine_id:
        raise PlanTokenError("plan_token machine_id mismatch")

    resolved = manifest_path.resolve()
    if str(resolved) != rec.manifest_path:
        raise PlanTokenError(f"plan_token bound to {rec.manifest_path!r}, not {str(resolved)!r}")
    digest = manifest_sha256(resolved)
    if digest != rec.manifest_sha256:
        raise PlanTokenError("manifest content changed since dry-run — re-run apply_dry_run")
    if rec.max_files is not None and int(max_files) > int(rec.max_files):
        raise PlanTokenError(
            f"max_files={max_files} exceeds dry-run bound "
            f"max_files={rec.max_files} — re-run apply_dry_run with a "
            f"higher max_files or lower execute max_files"
        )
    return rec


def consume_plan_token(*, token: str) -> None:
    """Atomically consume a validated token (one-shot).

    Uses rename-to-``.used`` then unlink so concurrent consumers race
    on rename rather than both validating the same file.
    """
    if not token or not token.strip():
        raise PlanTokenError("plan_token is required")
    token = token.strip()
    path = _token_dir() / f"{token}.json"
    claimed = _token_dir() / f"{token}.used"
    try:
        os.rename(path, claimed)
    except FileNotFoundError as exc:
        raise PlanTokenError("plan_token not found or already consumed — re-run apply_dry_run") from exc
    except OSError as exc:
        raise PlanTokenError(f"plan_token consume failed: {exc}") from exc
    claimed.unlink(missing_ok=True)


__all__ = [
    "PlanTokenError",
    "PlanTokenRecord",
    "consume_plan_token",
    "issue_plan_token",
    "manifest_sha256",
    "validate_plan_token",
]
