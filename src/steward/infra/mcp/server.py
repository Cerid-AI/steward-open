# SPDX-License-Identifier: Apache-2.0

"""FastMCP server wiring — exposes the read-only + write tools as MCP tools.

Read tools live in :mod:`steward.infra.mcp.handlers`. Write tools live
in :mod:`steward.infra.mcp.write_handlers` and are annotated with
``destructiveHint=True`` so real MCP clients (Claude Desktop, etc.)
surface a confirmation UI before invocation. The tool body still
delegates to the existing CLI orchestrators — the MCP layer adds
``actor=steward-mcp`` audit-trail context, nothing more.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from steward.infra.db.settings import inventory_db_path
from steward.infra.mcp import handlers, write_handlers


def build_server(name: str = "steward") -> FastMCP:
    """Construct a FastMCP server populated with Steward's read-only tools.

    Returned without starting — the caller picks the transport
    (``server.run(transport="stdio")`` or HTTP).
    """
    server = FastMCP(
        name,
        instructions=(
            "Steward filesystem stewardship inventory. Read tools answer "
            "questions about permanodes, claims, policies, machines, and "
            "audit. Write tools (replicate, archive, stash finalize/restore) "
            "are annotated destructiveHint=True — confirm before invoking. "
            "Start with `inventory_stats`, then narrow with "
            "`find_permanode_by_path` or `find_permanode_by_hash`."
        ),
    )

    @server.tool()
    def inventory_stats(include_imports: bool = False) -> dict[str, Any]:
        """Aggregate counts for the inventory: permanodes, current claims,
        scan_runs, audit entries, plus breakdown by tier and domain.

        With `include_imports=True` (ADR-0013) the aggregates span
        attached inventories too."""
        return handlers.inventory_stats(
            inventory_db_path(), include_imports=include_imports
        )

    @server.tool()
    def find_permanode_by_path(
        path_substring: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Find current claims whose file_path contains the given substring.
        Returns permanode_id + canonical_hash + path + tier per match."""
        return handlers.find_permanode_by_path(
            inventory_db_path(),
            path_substring=path_substring,
            limit=limit,
        )

    @server.tool()
    def find_permanode_by_hash(
        hash_prefix: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Find permanodes whose canonical_hash starts with the given
        prefix. Useful when you have a partial hash from an audit row."""
        return handlers.find_permanode_by_hash(
            inventory_db_path(),
            hash_prefix=hash_prefix,
            limit=limit,
        )

    @server.tool()
    def get_permanode(
        permanode_id: str, include_imports: bool = False
    ) -> dict[str, Any]:
        """Full details for a permanode: header, all claims, recent audit.

        With `include_imports=True` (ADR-0013) the lookup spans
        attached inventories; each claim/audit row carries a
        `source` field tagging local vs attached."""
        return handlers.get_permanode(
            inventory_db_path(),
            permanode_id=permanode_id,
            include_imports=include_imports,
        )

    @server.tool()
    def list_policies() -> list[dict[str, str]]:
        """List bundled policies under src/steward/policies/."""
        return handlers.list_policies()

    @server.tool()
    def show_policy(name: str) -> dict[str, Any]:
        """Return the raw YAML of a bundled policy by filename."""
        return handlers.show_policy(name=name)

    @server.tool()
    def recent_scan_runs(limit: int = 10) -> list[dict[str, Any]]:
        """Most-recent scan_runs with their summary counters."""
        return handlers.recent_scan_runs(inventory_db_path(), limit=limit)

    @server.tool()
    def tail_audit_log(
        limit: int = 20, action: str | None = None
    ) -> list[dict[str, Any]]:
        """Last `limit` audit_log rows, newest first. Optional `action`
        filter (e.g. 'scan_end', 'stash', 'promote')."""
        return handlers.tail_audit_log(
            inventory_db_path(), limit=limit, action=action
        )

    @server.tool()
    def list_machines(include_imports: bool = False) -> list[dict[str, Any]]:
        """List every machine_id that has touched this inventory,
        with claim/scan_run/audit counts and first/last seen
        timestamps. On a single-machine setup, returns one row.

        With `include_imports=True` (ADR-0013) attached inventories'
        machine_ids appear too, each tagged with a `source` field."""
        return handlers.list_machines(
            inventory_db_path(), include_imports=include_imports
        )

    @server.tool()
    def get_machine(
        machine_id: str, include_imports: bool = False
    ) -> dict[str, Any]:
        """Full details for one machine_id (accepts the full UUID).
        Includes recent scan_runs and recent audit entries.

        With `include_imports=True` (ADR-0013) the lookup spans
        attached inventories; recent activity is pulled from the
        schema where the machine was found."""
        return handlers.get_machine(
            inventory_db_path(),
            machine_id=machine_id,
            include_imports=include_imports,
        )

    @server.tool()
    def fp_status() -> dict[str, Any]:
        """Dropbox store vs CloudStorage mount fork probe (lightweight).
        Does not dump fileproviderctl. See docs/OPEN_DEVELOPMENT.md for
        deferred Dropbox rectification."""
        return handlers.fp_status()

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    def policy_plan(
        policy: str = "retention.yml",
        out_path: str | None = None,
        root_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Generate a plan TSV from a policy (does not apply). Writes only
        the manifest file. Returns row counts including retire_direct."""
        return handlers.policy_plan(
            policy=policy, out_path=out_path, root_prefix=root_prefix
        )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    def apply_dry_run(
        manifest_path: str,
        max_files: int | None = None,
        skip_verify: bool = False,
        allow_store_path_unlink: bool = False,
    ) -> dict[str, Any]:
        """Dry-run apply a plan manifest (ADR-0002). Never mutates the
        filesystem. Does not support --execute."""
        return handlers.apply_dry_run(
            manifest_path=manifest_path,
            max_files=max_files,
            skip_verify=skip_verify,
            allow_store_path_unlink=allow_store_path_unlink,
        )

    # ─────────────── write tools (annotated destructiveHint=True) ───────────────
    #
    # MCP tool annotations are *hints* — real clients (Claude Desktop, etc.)
    # surface destructive tools with a confirmation UI; the operator stays
    # in the loop per ADR-0002. The dry-run siblings are marked
    # ``readOnlyHint=True`` so clients can call them freely.

    _READ_DRY_RUN = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
    _WRITE_DESTRUCTIVE = ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False
    )

    @server.tool(annotations=_READ_DRY_RUN)
    def replicate_dry_run(policy: str = "replication.yml") -> dict[str, Any]:
        """Plan a replication run. Passes --dry-run to rclone so nothing
        mutates. Returns the report (per-source byte counts, durations)."""
        return write_handlers.replicate_dry_run(policy=policy)

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def replicate_execute(policy: str = "replication.yml") -> dict[str, Any]:
        """**Destructive.** Run rclone for real — moves bytes to every
        enabled destination in the policy. Pair with a prior
        ``replicate_dry_run`` so the operator sees what will change."""
        return write_handlers.replicate_execute(policy=policy)

    @server.tool(annotations=_READ_DRY_RUN)
    def archive_snapshot_dry_run(policy: str = "archive.yml") -> dict[str, Any]:
        """Plan a restic snapshot. Passes --dry-run to restic; no
        repository mutations. Returns the report (per-source data_added)."""
        return write_handlers.archive_snapshot_dry_run(policy=policy)

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def archive_snapshot_execute(policy: str = "archive.yml") -> dict[str, Any]:
        """**Destructive.** Create new restic snapshots in each repository.
        Pair with a prior ``archive_snapshot_dry_run`` for the diff."""
        return write_handlers.archive_snapshot_execute(policy=policy)

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def archive_init_execute(policy: str = "archive.yml") -> dict[str, Any]:
        """**Destructive.** Initialize new encrypted restic repositories
        (one-time setup per repo). Requires the password command in the
        policy to be configured."""
        return write_handlers.archive_init_execute(policy=policy)

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def stash_finalize_execute(
        run_id: str,
        cooling_off_days: int = 7,
        force: bool = False,
    ) -> dict[str, Any]:
        """**Destructive.** Permanently delete stashed files for the
        given ``run_id`` (after the configured cooling-off window).
        ``force=True`` overrides the cooling-off check."""
        return write_handlers.stash_finalize_execute(
            run_id=run_id, cooling_off_days=cooling_off_days, force=force
        )

    @server.tool(annotations=_WRITE_DESTRUCTIVE)
    def stash_restore_execute(run_id: str) -> dict[str, Any]:
        """**Destructive.** Move each stashed file BACK to its original
        location. Recovery operation; mutates the filesystem."""
        return write_handlers.stash_restore_execute(run_id=run_id)

    return server


__all__ = ["build_server"]
