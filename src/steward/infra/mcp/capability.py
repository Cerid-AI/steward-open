# SPDX-License-Identifier: Apache-2.0
"""MCP capability mode + actor identity (ADR-0016).

``STEWARD_MCP_MODE``:
  * ``read``  — inventory/query tools only
  * ``plan``  — + policy_plan, apply_dry_run, transport dry-runs (default)
  * ``write`` — + destructive execute tools including apply_execute

``STEWARD_MCP_ACTOR`` overrides the audit actor (default ``steward-mcp``).
Cerid agents should set e.g. ``steward-mcp:boardroom-agent``.
"""

from __future__ import annotations

import os
from enum import IntEnum
from pathlib import Path
from typing import Any


class McpMode(IntEnum):
    READ = 0
    PLAN = 1
    WRITE = 2


_MODE_NAMES: dict[str, McpMode] = {
    "read": McpMode.READ,
    "plan": McpMode.PLAN,
    "write": McpMode.WRITE,
}


class McpCapabilityError(Exception):
    """Raised when the current MCP mode forbids a tool."""


def mcp_mode() -> McpMode:
    raw = (os.environ.get("STEWARD_MCP_MODE") or "plan").strip().lower()
    if not raw:
        return McpMode.PLAN
    if raw not in _MODE_NAMES:
        raise McpCapabilityError(
            f"invalid STEWARD_MCP_MODE={raw!r}; expected one of {sorted(_MODE_NAMES)} (default plan)"
        )
    return _MODE_NAMES[raw]


def mcp_mode_name() -> str:
    mode = mcp_mode()
    for name, value in _MODE_NAMES.items():
        if value == mode:
            return name
    return "plan"


def require_mode(minimum: McpMode, *, tool: str) -> None:
    """Raise :class:`McpCapabilityError` if env mode is below ``minimum``."""
    current = mcp_mode()
    if current < minimum:
        raise McpCapabilityError(
            f"MCP tool {tool!r} requires STEWARD_MCP_MODE>="
            f"{minimum.name.lower()} (current={mcp_mode_name()!r}). "
            f"Set STEWARD_MCP_MODE=write for execute tools; plan is the "
            f"safe default for external Cerid agents (ADR-0016)."
        )


def mcp_actor() -> str:
    """Audit actor string for MCP-driven mutations."""
    raw = (os.environ.get("STEWARD_MCP_ACTOR") or "").strip()
    if raw:
        # Keep audit payloads compact and single-line friendly.
        return raw[:128]
    return "steward-mcp"


def mcp_max_files_cap() -> int:
    """Hard cap for MCP apply_execute max_files (default 50)."""
    raw = (os.environ.get("STEWARD_MCP_MAX_FILES_CAP") or "50").strip()
    try:
        n = int(raw)
    except ValueError:
        return 50
    return max(1, min(n, 10_000))


def record_mcp_write_invoked(
    *,
    db_path: Path,
    machine_id: str,
    tool: str,
    args: dict[str, Any],
) -> None:
    """Append ``mcp_write_invoked`` audit row (public; ADR-0011/0016).

    Shared by plan dry-runs and execute tools so callers never import
    private ``write_handlers`` helpers.
    """
    from steward.infra.db import repo_audit
    from steward.infra.db.connect import connect

    path = Path(db_path)
    con = connect(path)
    try:
        repo_audit.append(
            con,
            machine_id=machine_id,
            actor=mcp_actor(),
            action="mcp_write_invoked",
            payload={"tool": tool, "args": args},
        )
        con.commit()
    finally:
        con.close()


__all__ = [
    "McpCapabilityError",
    "McpMode",
    "mcp_actor",
    "mcp_max_files_cap",
    "mcp_mode",
    "mcp_mode_name",
    "record_mcp_write_invoked",
    "require_mode",
]
