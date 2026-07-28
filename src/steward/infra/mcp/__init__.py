"""Read-only MCP server for Steward.

The handlers in :mod:`steward.infra.mcp.handlers` are pure functions
over an opened read-only ``inventory.db`` connection. The FastMCP
wiring in :mod:`steward.infra.mcp.server` exposes them as MCP tools.

Per ADR-0002 (operator-in-the-loop on destructive ops), nothing in this
package mutates state. The DB connection is opened with
``read_only=True`` and no ``UPDATE`` / ``INSERT`` / ``DELETE`` SQL ever
runs.
"""

from steward.infra.mcp.handlers import (
    find_permanode_by_hash,
    find_permanode_by_path,
    get_machine,
    get_permanode,
    inventory_stats,
    list_machines,
    list_policies,
    recent_scan_runs,
    show_policy,
    tail_audit_log,
)
from steward.infra.mcp.write_handlers import (
    archive_init_execute,
    archive_snapshot_dry_run,
    archive_snapshot_execute,
    replicate_dry_run,
    replicate_execute,
    stash_finalize_execute,
    stash_restore_execute,
)

__all__ = [
    "archive_init_execute",
    "archive_snapshot_dry_run",
    "archive_snapshot_execute",
    "find_permanode_by_hash",
    "find_permanode_by_path",
    "get_machine",
    "get_permanode",
    "inventory_stats",
    "list_machines",
    "list_policies",
    "recent_scan_runs",
    "replicate_dry_run",
    "replicate_execute",
    "show_policy",
    "stash_finalize_execute",
    "stash_restore_execute",
    "tail_audit_log",
]
