# SPDX-License-Identifier: Apache-2.0

"""``steward db`` subcommand group — migrate / verify / integrity / backup."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console

from steward.infra.db.admin import (
    integrity_check,
    migrate,
    resolve_machine_id,
    verify_chain,
)
from steward.infra.db.backup import BackupError, backup_inventory_db
from steward.infra.db.settings import imports_dir as imports_dir_path
from steward.infra.db.settings import inventory_db_path
from steward.infra.status import _format_bytes
from steward.infra.sync import (
    ExportError,
    ImportError,
    ImportsAdminError,
    detach_import,
    export_inventory,
    import_inventory,
    list_imports,
    verify_imports,
)

app = typer.Typer(
    name="db",
    help="Inventory DB admin: migrate, verify, integrity, backup, audit-export, export, import, imports.",
    no_args_is_help=True,
)

imports_app = typer.Typer(
    name="imports",
    help="Manage attached cross-machine inventories (list / detach).",
    no_args_is_help=True,
)
app.add_typer(imports_app, name="imports")
console = Console()


@app.command("migrate")
def migrate_cmd() -> None:
    """Run alembic upgrade head against the configured inventory.db."""
    result = migrate()
    console.print(f"[green]✓[/green] Migrated [bold]{result.db_path}[/bold]")
    console.print(f"  schema_version    = {result.schema_version}")
    console.print(f"  machine_id        = {result.machine_id}")
    console.print(f"  sqlite_vec        = {result.vec_version or '(not loaded)'}")


@app.command("verify")
def verify_cmd(
    imports: bool = typer.Option(
        False,
        "--imports",
        help="Additionally walk every attached inventory's audit chain "
        "(ADR-0013). Each chain verifies independently; "
        "chain_verified_at is updated on success.",
    ),
) -> None:
    """Walk audit_log and verify the hash chain is intact.

    With ``--imports``, also runs ``verify_chain`` against every
    payload .db registered in ``attached_inventories``. The local
    chain is always checked first; if the local chain is broken the
    command exits non-zero without touching the imports.

    Exit codes:

    * 0 — local chain ok + (if ``--imports``) every attached
      inventory's chain ok.
    * 1 — local chain broken OR (with ``--imports``) one or more
      attached chains broken or payloads missing.
    """
    result = verify_chain()
    if result.ok:
        console.print(f"[green]✓[/green] chain ok ({result.rows_checked} rows)")
    else:
        console.print(f"[red]✗[/red] chain broken: {result.error}")
        console.print(f"  rows checked before failure: {result.rows_checked}")
        raise typer.Exit(1)

    if not imports:
        raise typer.Exit(0)

    # ── --imports: walk every attached inventory's chain. ──────────
    local_db = inventory_db_path()
    report = verify_imports(db_path=local_db)
    if report.total == 0:
        console.print("[dim]No attached inventories to verify.[/dim]")
        raise typer.Exit(0)

    from rich.table import Table

    table = Table(title="Attached inventory chain verification", show_lines=False)
    table.add_column("machine_id", overflow="fold")
    table.add_column("status")
    table.add_column("rows", justify="right")
    table.add_column("payload")
    table.add_column("detail")

    for v in report.verified:
        if not v.payload_exists:
            status = "[red]MISSING[/red]"
        elif v.chain_ok:
            status = "[green]ok[/green]"
        else:
            status = "[red]BROKEN[/red]"
        payload = "[green]ok[/green]" if v.payload_exists else "[red]MISSING[/red]"
        detail = v.error if v.error else ""
        table.add_row(
            v.machine_id[:18] + "…",
            status,
            str(v.rows_checked),
            payload,
            detail,
        )
    console.print(table)

    if report.all_ok:
        console.print(f"[green]✓[/green] all {report.total} attached inventories verified ok")
        raise typer.Exit(0)
    console.print(
        f"[red]✗[/red] {report.broken_count} of {report.total} attached "
        f"inventories failed verification "
        f"({report.missing_count} payload(s) missing)"
    )
    raise typer.Exit(1)


@app.command("integrity")
def integrity_cmd() -> None:
    """Run SQLite's PRAGMA integrity_check on the inventory DB."""
    ok, msg = integrity_check()
    if ok:
        console.print("[green]✓[/green] PRAGMA integrity_check ok")
        raise typer.Exit(0)
    console.print("[red]✗[/red] PRAGMA integrity_check failed:")
    console.print(msg)
    raise typer.Exit(1)


@app.command("backup")
def backup_cmd(
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Destination snapshot file. Default: <inventory_dir>/snapshots/inventory-<iso8601>.db.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Replace an existing snapshot at the target path.",
    ),
) -> None:
    """Snapshot the inventory.db via SQLite's online-backup API.

    The snapshot is a fully consistent copy — unlike ``cp``, which
    races writes and loses WAL contents. Useful before a risky
    ``apply --execute`` or a manual policy change.

    Per ADR-0003, the source database's audit chain records the
    backup (``db_backup_created``) so a future ``db verify`` attests
    that the snapshot existed.
    """
    source = inventory_db_path()
    if not source.exists():
        console.print(f"[red]inventory.db missing at {source} — nothing to back up.[/red]")
        raise typer.Exit(2)

    if out is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = source.parent / "snapshots" / f"inventory-{stamp}.db"
        out.parent.mkdir(parents=True, exist_ok=True)

    machine_id = resolve_machine_id(source)
    try:
        result = backup_inventory_db(
            source_path=source,
            target_path=out,
            machine_id=machine_id,
            overwrite=overwrite,
        )
    except BackupError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] snapshot written to {result.target_path}")
    console.print(f"  bytes_copied      = {_format_bytes(result.bytes_copied)}")
    console.print(f"  duration_seconds  = {result.duration_seconds:.2f}")


@app.command("audit-export")
def audit_export_cmd(
    out: Path = typer.Option(
        ...,
        "--out",
        help="Destination JSONL path (one audit row per line).",
    ),
    before: str | None = typer.Option(
        None,
        "--before",
        help="Only rows with timestamp < this ISO-8601 value.",
    ),
    after: str | None = typer.Option(
        None,
        "--after",
        help="Only rows with timestamp >= this ISO-8601 value.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Max rows to export (oldest-first within the filter).",
    ),
    action: list[str] | None = typer.Option(
        None,
        "--action",
        help="Filter to one or more action names (repeatable).",
    ),
) -> None:
    """Cold-export audit_log rows to JSONL for offsite archival.

    Read-only. Does **not** delete or shrink inventory.db (ADR-0003
    append-only). Use for forensics, off-box backup of audit history,
    or analysis — not as a vacuum substitute.
    """
    from steward.infra.db.audit_export import export_audit_log

    source = inventory_db_path()
    if not source.exists():
        console.print(f"[red]inventory.db missing at {source}[/red]")
        raise typer.Exit(2)
    result = export_audit_log(
        db_path=source,
        out_path=out,
        before=before,
        after=after,
        limit=limit,
        actions=list(action) if action else None,
    )
    console.print(f"[green]✓[/green] wrote {result.rows_written:,} audit rows → {result.out_path}")
    if result.first_id is not None:
        console.print(f"  id range          = {result.first_id} … {result.last_id}")
    if result.after:
        console.print(f"  after             = {result.after}")
    if result.before:
        console.print(f"  before            = {result.before}")


@app.command("export")
def export_cmd(
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Destination envelope. Default: <inventory_dir>/exports/inventory-<short_id>-<iso8601>.tar.xz.",
    ),
    with_embeddings: bool = typer.Option(
        False,
        "--with-embeddings",
        help="Include the embeddings + embeddings_vec tables in the payload. "
        "Default is to exclude them (large, model-version coupled).",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Replace an existing envelope at the target path.",
    ),
) -> None:
    """Export the local inventory as a portable cross-machine snapshot (ADR-0013).

    Produces a tar.xz envelope containing inventory.db, manifest.json,
    and checksums.txt. Another Steward instance can ``db import`` the
    envelope to attach the snapshot read-only.

    Per ADR-0009 (pull-don't-push), the exported inventory NEVER drives
    ``apply --execute`` on the importing machine — it's a query
    surface only.
    """
    source = inventory_db_path()
    if not source.exists():
        console.print(f"[red]inventory.db missing at {source} — nothing to export.[/red]")
        raise typer.Exit(2)

    machine_id = resolve_machine_id(source)
    if out is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        short = machine_id[:8]
        out = source.parent / "exports" / f"inventory-{short}-{stamp}.tar.xz"
        out.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = export_inventory(
            db_path=source,
            target_path=out,
            machine_id=machine_id,
            with_embeddings=with_embeddings,
            overwrite=overwrite,
        )
    except ExportError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] envelope written to {result.envelope_path}")
    console.print(f"  envelope_size     = {_format_bytes(result.envelope_size_bytes)}")
    console.print(f"  payload_size      = {_format_bytes(result.payload_size_bytes)}")
    console.print(f"  payload_blake3    = {result.payload_blake3[:16]}…")
    console.print(f"  audit_rows        = {result.audit_rows}")
    console.print(f"  claim_rows        = {result.claim_rows}")
    console.print(f"  permanode_rows    = {result.permanode_rows}")
    console.print(f"  with_embeddings   = {result.with_embeddings}")
    console.print(f"  duration_seconds  = {result.duration_seconds:.2f}")


@app.command("import")
def import_cmd(
    envelope: Path = typer.Argument(
        ...,
        help="Path to the tar.xz envelope produced by `steward db export`.",
    ),
) -> None:
    """Import a cross-machine inventory snapshot (ADR-0013).

    Unpacks the envelope, verifies blake3 + audit chain, copies the
    payload .db into ``<data_dir>/imports/<machine_id>/<iso>.db``, and
    upserts a row into ``attached_inventories``. The payload is
    NEVER attached writeable — read-side query surfaces (v0.3.5+)
    open it via ``ATTACH DATABASE ... ?mode=ro``.

    Refuses if:

    * the envelope is malformed or its wire-format-version is newer
      than this Steward supports;
    * blake3 or audit-chain verification fails;
    * the envelope was exported from the LOCAL machine
      (you cannot import your own inventory).

    Re-importing an envelope from the same exporter machine_id is
    allowed — the new payload replaces the previous one. The replace
    is recorded as an ``inventory_attached`` audit row with
    ``replaced_existing=True``.
    """
    local_db = inventory_db_path()
    if not local_db.exists():
        console.print(f"[red]local inventory.db missing at {local_db}. Run `steward db migrate` first.[/red]")
        raise typer.Exit(2)

    try:
        result = import_inventory(
            envelope_path=envelope,
            db_path=local_db,
            imports_dir=imports_dir_path(),
        )
    except ImportError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc

    verb = "re-attached" if result.replaced_existing else "attached"
    console.print(f"[green]✓[/green] inventory {verb} from {result.machine_id[:8]}…")
    console.print(f"  exporter_hostname = {result.exporter_hostname or '(unknown)'}")
    console.print(f"  exporter_version  = {result.exporter_version}")
    console.print(f"  payload_path      = {result.payload_path}")
    console.print(f"  payload_blake3    = {result.payload_blake3[:16]}…")
    console.print(f"  audit_rows        = {result.audit_rows}")
    console.print(f"  claim_rows        = {result.claim_rows}")
    console.print(f"  permanode_rows    = {result.permanode_rows}")
    console.print(f"  duration_seconds  = {result.duration_seconds:.2f}")


# ─────────────────────── `steward db imports {list, detach}` ──────────────


@imports_app.command("list")
def imports_list_cmd() -> None:
    """List every attached cross-machine inventory (ADR-0013).

    Reads ``attached_inventories`` and decorates each row with a
    payload-exists flag — a stale row whose .db file is missing
    is highlighted so the operator notices and runs ``detach``.
    """
    from rich.table import Table

    local_db = inventory_db_path()
    if not local_db.exists():
        console.print(f"[red]local inventory.db missing at {local_db}. Run `steward db migrate` first.[/red]")
        raise typer.Exit(2)

    rows = list_imports(db_path=local_db)
    if not rows:
        console.print("[dim]No attached inventories. Use `steward db import <envelope>` to attach one.[/dim]")
        return

    table = Table(title="Attached inventories", show_lines=False)
    table.add_column("machine_id", overflow="fold")
    table.add_column("hostname")
    table.add_column("version")
    table.add_column("imported_at")
    table.add_column("audit_rows", justify="right")
    table.add_column("payload")

    for row in rows:
        payload_marker = "[green]ok[/green]" if row.payload_exists else "[red]MISSING[/red]"
        table.add_row(
            row.machine_id[:18] + "…",
            row.exporter_hostname or "[dim](unknown)[/dim]",
            row.exporter_version,
            row.imported_at,
            str(row.audit_rows),
            payload_marker,
        )
    console.print(table)


@imports_app.command("detach")
def imports_detach_cmd(
    machine_id_prefix: str = typer.Argument(
        ...,
        help="Exporter machine_id (or any unique prefix) of the inventory to detach.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be removed; make no changes.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Actually remove the row + unlink the payload + audit-log it.",
    ),
) -> None:
    """Detach an attached inventory (ADR-0013).

    Removes the ``attached_inventories`` row, unlinks the payload .db
    file, and appends an ``inventory_detached`` audit row to the
    LOCAL chain. Best-effort cleanup of the now-empty
    ``<imports>/<machine_id>/`` directory.

    Destructive — per ADR-0002 requires ``--dry-run`` or
    ``--execute``. Default behavior with neither flag is exit 2.
    """
    from steward.infra.sync import get_import

    if not dry_run and not execute:
        console.print("[red]✗[/red] --dry-run or --execute required for `imports detach`.")
        raise typer.Exit(2)
    if dry_run and execute:
        console.print("[red]✗[/red] --dry-run and --execute are mutually exclusive.")
        raise typer.Exit(2)

    local_db = inventory_db_path()
    if not local_db.exists():
        console.print(f"[red]local inventory.db missing at {local_db}.[/red]")
        raise typer.Exit(2)

    try:
        target = get_import(db_path=local_db, machine_id_or_prefix=machine_id_prefix)
    except ImportsAdminError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc

    if dry_run:
        console.print(f"[yellow]would detach[/yellow] {target.machine_id[:18]}…")
        console.print(f"  payload_path      = {target.file_path}")
        console.print(f"  payload_exists    = {target.payload_exists}")
        console.print(f"  exporter_hostname = {target.exporter_hostname or '(unknown)'}")
        console.print(f"  imported_at       = {target.imported_at}")
        console.print(f"  audit_rows        = {target.audit_rows}")
        console.print("[dim]Re-run with --execute to apply.[/dim]")
        return

    try:
        result = detach_import(
            db_path=local_db,
            machine_id_or_prefix=machine_id_prefix,
        )
    except ImportsAdminError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] detached {result.machine_id[:18]}…")
    console.print(f"  payload_path      = {result.payload_path}")
    console.print(f"  payload_existed   = {result.payload_existed}")
    console.print(f"  payload_unlinked  = {result.payload_unlinked}")
    console.print(f"  audit_row_id      = {result.audit_row_id}")
