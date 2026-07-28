# SPDX-License-Identifier: Apache-2.0

"""``steward scan`` — walk a root, hash files, insert claims."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from steward.infra.db.admin import migrate, resolve_machine_id
from steward.infra.db.settings import inventory_db_path
from steward.infra.scanner.orchestrate import run_scan

app = typer.Typer(name="scan", help="Walk a root, hash every file, insert claims.", invoke_without_command=True)
console = Console()


@app.callback(invoke_without_command=True)
def scan_cmd(
    root: Path = typer.Option(..., "--root", help="Filesystem root to scan."),
    workers: int = typer.Option(
        1,
        "--workers",
        min=1,
        help="Worker processes. With >=2, the walker partitions ``root`` by "
        "top-level subdir and walks each in its own process. Default 1 (serial).",
    ),
    include_containers: bool = typer.Option(
        False,
        "--include-containers",
        help="Walk archive members and record per-member claims "
        "(zip / tar* always; .dmg/.sparseimage via hdiutil and .7z/.rar "
        "via unar when those tools are on PATH).",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Reuse permanode_id for paths unchanged since the latest "
        "finished scan_run on this root (matched by size + mtime). "
        "Files that changed or didn't exist before are hashed normally.",
    ),
) -> None:
    """Walk ``root``, compute xxh3 / blake3, upsert permanodes + claims."""
    target = inventory_db_path()
    if not target.exists():
        console.print(f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]")
        migrate(target)
    machine_id = resolve_machine_id(target)

    stats = run_scan(
        root=root,
        db_path=target,
        machine_id=machine_id,
        resume=resume,
        include_containers=include_containers,
        workers=workers,
    )

    console.print(f"[green]✓[/green] scan of {root} complete")
    console.print(f"  files_walked        = {stats.files_walked:,}")
    console.print(f"  files_hashed        = {stats.files_hashed:,}")
    if resume:
        console.print(f"  files_reused        = {stats.files_reused:,}")
    console.print(f"  files_errored       = {stats.files_errored:,}")
    console.print(f"  bytes_hashed        = {stats.bytes_hashed:,}")
    console.print(f"  permanodes_touched  = {len(stats.permanodes_touched):,}")
    if include_containers:
        console.print(f"  containers_walked   = {stats.containers_walked:,}")
        console.print(f"  containers_skipped  = {stats.containers_skipped:,}")
        console.print(f"  containers_errored  = {stats.containers_errored:,}")
        console.print(f"  container_members   = {stats.container_members_walked:,}")
