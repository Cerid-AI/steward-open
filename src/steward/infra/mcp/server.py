# SPDX-License-Identifier: Apache-2.0

"""FastMCP server wiring — read, plan, and gated write tools.

Read tools live in :mod:`steward.infra.mcp.handlers`. Write tools live
in :mod:`steward.infra.mcp.write_handlers` and are annotated with
``destructiveHint=True``. Capability modes (ADR-0016):

* ``STEWARD_MCP_MODE=read|plan|write`` (default ``plan``)
* ``STEWARD_MCP_ACTOR`` for audit attribution
* ``apply_execute`` requires a one-shot ``plan_token`` from ``apply_dry_run``
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from steward.infra.db.settings import inventory_db_path
from steward.infra.mcp import handlers, write_handlers
from steward.infra.mcp.capability import (
    McpCapabilityError,
    McpMode,
    mcp_mode_name,
    require_mode,
)


def _capability_error(exc: McpCapabilityError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
        "mode": mcp_mode_name(),
    }


def build_server(name: str = "steward") -> FastMCP:
    """Construct a FastMCP server populated with Steward's tools.

    Returned without starting — the caller picks the transport
    (``server.run(transport="stdio")`` or HTTP).
    """
    server = FastMCP(
        name,
        instructions=(
            "Steward filesystem stewardship inventory. "
            "Default STEWARD_MCP_MODE=plan (query + dry-run). "
            "Write/execute tools require STEWARD_MCP_MODE=write. "
            "apply_execute requires plan_token from apply_dry_run (ADR-0016). "
            "Start with inventory_stats or status, then narrow with "
            "find_permanode_by_path / inspect_target. "
            "For Dropbox cloud retires: fp_status + require_fp_healthy."
        ),
    )

    _READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
    # Plan tools write audit/token/plan artifacts — not FS-destructive (ADR-0016).
    _PLAN = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
    _WRITE_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)

    # ─────────────── read tools ───────────────

    @server.tool(annotations=_READ)
    def mcp_capability() -> dict[str, Any]:
        """Report STEWARD_MCP_MODE, actor, and max_files cap (ADR-0016)."""
        return handlers.mcp_capability()

    @server.tool(annotations=_READ)
    def inventory_stats(include_imports: bool = False) -> dict[str, Any]:
        """Aggregate counts: permanodes, claims, scan_runs, by tier/domain."""
        return handlers.inventory_stats(inventory_db_path(), include_imports=include_imports)

    @server.tool(annotations=_READ)
    def inventory_cross_stats(
        dim_a: str,
        dim_b: str | None = None,
        path_prefix: str | None = None,
        limit: int = 50,
        include_imports: bool = False,
    ) -> dict[str, Any]:
        """Cross-tab claims by one or two dimensions (ADR-0022 data matrix)."""
        return handlers.inventory_cross_stats(
            inventory_db_path(),
            dim_a=dim_a,
            dim_b=dim_b,
            path_prefix=path_prefix,
            limit=limit,
            include_imports=include_imports,
        )

    @server.tool(annotations=_READ)
    def inventory_path_tree(
        path_prefix: str = "",
        color_by: str = "none",
        tier: str | None = None,
        volume: str | None = None,
        child_limit: int = 100,
        include_imports: bool = False,
    ) -> dict[str, Any]:
        """Depth-1 path tree for inventory surface exploration (ADR-0022)."""
        return handlers.inventory_path_tree(
            inventory_db_path(),
            path_prefix=path_prefix,
            color_by=color_by,
            tier=tier,
            volume=volume,
            child_limit=child_limit,
            include_imports=include_imports,
        )

    @server.tool(annotations=_READ)
    def status(quick: bool = True, include_imports: bool = False) -> dict[str, Any]:
        """Operator status report (like `steward status --quick`)."""
        return handlers.status_snapshot(quick=quick, include_imports=include_imports)

    @server.tool(annotations=_READ)
    def scan_status(root: str | None = None, limit: int = 5) -> dict[str, Any]:
        """Recent scan_runs; set root to filter. Shows in_progress runs."""
        return handlers.scan_status(root=root, limit=limit)

    @server.tool(annotations=_READ)
    def find_permanode_by_path(path_substring: str, limit: int = 10) -> list[dict[str, Any]]:
        """Find current claims whose file_path contains the substring."""
        return handlers.find_permanode_by_path(
            inventory_db_path(),
            path_substring=path_substring,
            limit=limit,
        )

    @server.tool(annotations=_READ)
    def find_permanode_by_hash(hash_prefix: str, limit: int = 10) -> list[dict[str, Any]]:
        """Find permanodes whose canonical_hash starts with prefix."""
        return handlers.find_permanode_by_hash(
            inventory_db_path(),
            hash_prefix=hash_prefix,
            limit=limit,
        )

    @server.tool(annotations=_READ)
    def get_permanode(permanode_id: str, include_imports: bool = False) -> dict[str, Any]:
        """Full permanode details: header, claims, recent audit."""
        return handlers.get_permanode(
            inventory_db_path(),
            permanode_id=permanode_id,
            include_imports=include_imports,
        )

    @server.tool(annotations=_READ)
    def inspect_target(
        target: str,
        audit_limit: int = 20,
        include_imports: bool = False,
    ) -> dict[str, Any]:
        """Inspect by path, permanode id, or hash (`steward inspect`)."""
        return handlers.inspect_target(
            target,
            audit_limit=audit_limit,
            include_imports=include_imports,
        )

    @server.tool(annotations=_READ)
    def list_policies() -> list[dict[str, str]]:
        """List bundled policies under src/steward/policies/."""
        return handlers.list_policies()

    @server.tool(annotations=_READ)
    def show_policy(name: str) -> dict[str, Any]:
        """Return raw YAML of a bundled policy by filename."""
        return handlers.show_policy(name=name)

    @server.tool(annotations=_READ)
    def recent_scan_runs(limit: int = 10) -> list[dict[str, Any]]:
        """Most-recent scan_runs with summary counters."""
        return handlers.recent_scan_runs(inventory_db_path(), limit=limit)

    @server.tool(annotations=_READ)
    def tail_audit_log(limit: int = 20, action: str | None = None) -> list[dict[str, Any]]:
        """Last audit_log rows, newest first. Optional action filter."""
        return handlers.tail_audit_log(inventory_db_path(), limit=limit, action=action)

    @server.tool(annotations=_READ)
    def list_machines(include_imports: bool = False) -> list[dict[str, Any]]:
        """List machine_ids with claim/scan/audit counts."""
        return handlers.list_machines(inventory_db_path(), include_imports=include_imports)

    @server.tool(annotations=_READ)
    def get_machine(machine_id: str, include_imports: bool = False) -> dict[str, Any]:
        """Details for one machine_id including recent activity."""
        return handlers.get_machine(
            inventory_db_path(),
            machine_id=machine_id,
            include_imports=include_imports,
        )

    @server.tool(annotations=_READ)
    def fp_status() -> dict[str, Any]:
        """Dropbox store vs CloudStorage mount health probe."""
        return handlers.fp_status()

    @server.tool(annotations=_READ)
    def dual_presence_sample(
        sample: int = 32,
        rels: list[str] | None = None,
        use_db: bool = True,
    ) -> dict[str, Any]:
        """Bounded dual-presence stats (ADR-0020). Read-only; no claim rewrite."""
        return handlers.dual_presence_sample(sample=sample, rels=rels, use_db=use_db)


    @server.tool(annotations=_READ)
    def estate_health(
        quick: bool = True,
        include_imports: bool = False,
        probes: bool = False,
    ) -> dict[str, Any]:
        """Composite estate health (ADR-0017). Default quick; read-only."""
        return handlers.estate_health(
            quick=quick,
            include_imports=include_imports,
            probes=probes,
        )

    @server.tool(annotations=_READ)
    def estate_health_check(
        quick: bool = True,
        include_imports: bool = False,
        probes: bool = False,
        fail_on: list[str] | None = None,
        scan_max_age_hours: float | None = None,
        stash_grace_hours: float | None = None,
        rollup_max_age_hours: float | None = None,
    ) -> dict[str, Any]:
        """Evaluate estate health fail-on gates (ADR-0017). Read-only."""
        return handlers.estate_health_check(
            quick=quick,
            include_imports=include_imports,
            probes=probes,
            fail_on=fail_on,
            scan_max_age_hours=scan_max_age_hours,
            stash_grace_hours=stash_grace_hours,
            rollup_max_age_hours=rollup_max_age_hours,
        )



    @server.tool(annotations=_READ)
    def fleet_health(
        include_imports: bool = True,
        quick: bool = True,
    ) -> dict[str, Any]:
        """Multi-machine fleet health matrix (ADR-0021). Default include_imports."""
        return handlers.fleet_health(
            include_imports=include_imports,
            quick=quick,
        )

    @server.tool(annotations=_READ)
    def fleet_health_check(
        include_imports: bool = True,
        quick: bool = True,
        fail_on: list[str] | None = None,
        scan_max_age_hours: float | None = None,
        envelope_max_age_hours: float | None = None,
        attached_max_age_days: float | None = None,
        chain_verify_max_age_days: float | None = None,
    ) -> dict[str, Any]:
        """Evaluate fleet fail-on gates (ADR-0021). Read-only."""
        return handlers.fleet_health_check(
            include_imports=include_imports,
            quick=quick,
            fail_on=fail_on,
            scan_max_age_hours=scan_max_age_hours,
            envelope_max_age_hours=envelope_max_age_hours,
            attached_max_age_days=attached_max_age_days,
            chain_verify_max_age_days=chain_verify_max_age_days,
        )

    @server.tool(annotations=_READ)
    def plan_backlog_list(
        status: str | None = None,
        policy: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List plan backlog records (ADR-0019). Read-only data-dir registry."""
        return handlers.plan_backlog_list(status=status, policy=policy, limit=limit)

    @server.tool(annotations=_READ)
    def plan_backlog_show(plan_id: str) -> dict[str, Any]:
        """Show one plan backlog record by plan_id (manifest_run_id)."""
        return handlers.plan_backlog_show(plan_id=plan_id)

    # ─────────────── plan tools (STEWARD_MCP_MODE>=plan) ───────────────

    @server.tool(annotations=_PLAN)
    def policy_plan(
        policy: str = "retention.yml",
        out_path: str | None = None,
        root_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Generate a plan TSV from a policy (does not apply). Writes plan file."""
        try:
            require_mode(McpMode.PLAN, tool="policy_plan")
        except McpCapabilityError as exc:
            return _capability_error(exc)
        return handlers.policy_plan(policy=policy, out_path=out_path, root_prefix=root_prefix)


    @server.tool(annotations=_PLAN)
    def filter_plan_dual_presence(
        manifest_path: str,
        out_dir: str | None = None,
        limit: int = 0,
        path_col: str = "source_path",
        intent: str = "cloud_retire",
        register_with: str | None = None,
    ) -> dict[str, Any]:
        """Bucket plan TSV by dual-presence; write sidecars under data-dir (ADR-0020)."""
        try:
            require_mode(McpMode.PLAN, tool="filter_plan_dual_presence")
        except McpCapabilityError as exc:
            return _capability_error(exc)
        return handlers.filter_plan_dual_presence(
            manifest_path=manifest_path,
            out_dir=out_dir,
            limit=limit,
            path_col=path_col,
            intent=intent,
            register_with=register_with,
        )

    @server.tool(annotations=_PLAN)
    def apply_dry_run(
        manifest_path: str,
        max_files: int | None = None,
        skip_verify: bool = False,
        allow_store_path_unlink: bool = False,
        require_fp_healthy: bool = True,
        issue_plan_token: bool = True,
    ) -> dict[str, Any]:
        """Dry-run apply a plan. Returns plan_token for apply_execute (ADR-0016)."""
        try:
            require_mode(McpMode.PLAN, tool="apply_dry_run")
        except McpCapabilityError as exc:
            return _capability_error(exc)
        return handlers.apply_dry_run(
            manifest_path=manifest_path,
            max_files=max_files,
            skip_verify=skip_verify,
            allow_store_path_unlink=allow_store_path_unlink,
            require_fp_healthy=require_fp_healthy,
            issue_plan_token=issue_plan_token,
        )

    @server.tool(annotations=_PLAN)
    def replicate_dry_run(policy: str = "replication.yml") -> dict[str, Any]:
        """Plan a replication run (rclone --dry-run). May write audit marker."""
        try:
            return write_handlers.replicate_dry_run(policy=policy)
        except McpCapabilityError as exc:
            return _capability_error(exc)

    @server.tool(annotations=_PLAN)
    def archive_snapshot_dry_run(policy: str = "archive.yml") -> dict[str, Any]:
        """Plan a restic snapshot (--dry-run). May write audit marker."""
        try:
            return write_handlers.archive_snapshot_dry_run(policy=policy)
        except McpCapabilityError as exc:
            return _capability_error(exc)

    # ─────────────── write tools (STEWARD_MCP_MODE=write) ───────────────

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def apply_execute(
        manifest_path: str,
        plan_token: str,
        max_files: int,
        skip_verify: bool = False,
        allow_store_path_unlink: bool = False,
        require_fp_healthy: bool = True,
    ) -> dict[str, Any]:
        """**Destructive.** Execute a plan after apply_dry_run plan_token.

        Requires STEWARD_MCP_MODE=write. max_files is mandatory (capped).
        require_fp_healthy defaults True for cloud-safe posture.
        """
        return handlers.apply_execute(
            manifest_path=manifest_path,
            plan_token=plan_token,
            max_files=max_files,
            skip_verify=skip_verify,
            allow_store_path_unlink=allow_store_path_unlink,
            require_fp_healthy=require_fp_healthy,
        )

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def replicate_execute(policy: str = "replication.yml") -> dict[str, Any]:
        """**Destructive.** Run rclone for real. Requires MODE=write."""
        try:
            return write_handlers.replicate_execute(policy=policy)
        except McpCapabilityError as exc:
            return _capability_error(exc)

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def archive_snapshot_execute(policy: str = "archive.yml") -> dict[str, Any]:
        """**Destructive.** Create restic snapshots. Requires MODE=write."""
        try:
            return write_handlers.archive_snapshot_execute(policy=policy)
        except McpCapabilityError as exc:
            return _capability_error(exc)

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def archive_init_execute(policy: str = "archive.yml") -> dict[str, Any]:
        """**Destructive.** Initialize restic repositories. Requires MODE=write."""
        try:
            return write_handlers.archive_init_execute(policy=policy)
        except McpCapabilityError as exc:
            return _capability_error(exc)

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def stash_finalize_execute(
        run_id: str,
        cooling_off_days: int = 7,
        force: bool = False,
    ) -> dict[str, Any]:
        """**Destructive.** Permanently delete cooled-off stash. MODE=write."""
        try:
            return write_handlers.stash_finalize_execute(
                run_id=run_id,
                cooling_off_days=cooling_off_days,
                force=force,
            )
        except McpCapabilityError as exc:
            return _capability_error(exc)

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def stash_restore_execute(run_id: str) -> dict[str, Any]:
        """**Destructive.** Restore stashed files. Requires MODE=write."""
        try:
            return write_handlers.stash_restore_execute(run_id=run_id)
        except McpCapabilityError as exc:
            return _capability_error(exc)

    return server


__all__ = ["build_server"]
