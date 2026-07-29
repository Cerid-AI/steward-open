# SPDX-License-Identifier: Apache-2.0
"""Steward MCP surface — inventory query, plan, and gated write tools.

Capability modes (ADR-0016): ``STEWARD_MCP_MODE=read|plan|write``
(default ``plan``). Destructive execute tools require ``write`` and
carry ``destructiveHint=True``. ``apply_execute`` also requires a
one-shot ``plan_token`` from ``apply_dry_run``.
"""

from steward.infra.mcp.handlers import (
    apply_dry_run,
    apply_execute,
    find_permanode_by_hash,
    find_permanode_by_path,
    get_machine,
    get_permanode,
    inspect_target,
    inventory_stats,
    list_machines,
    list_policies,
    mcp_capability,
    recent_scan_runs,
    scan_status,
    show_policy,
    status_snapshot,
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
    "apply_dry_run",
    "apply_execute",
    "archive_init_execute",
    "archive_snapshot_dry_run",
    "archive_snapshot_execute",
    "find_permanode_by_hash",
    "find_permanode_by_path",
    "get_machine",
    "get_permanode",
    "inspect_target",
    "inventory_stats",
    "list_machines",
    "list_policies",
    "mcp_capability",
    "recent_scan_runs",
    "replicate_dry_run",
    "replicate_execute",
    "scan_status",
    "show_policy",
    "stash_finalize_execute",
    "stash_restore_execute",
    "status_snapshot",
    "tail_audit_log",
]
