# SPDX-License-Identifier: Apache-2.0

"""``steward watch`` — fsevents-driven incremental scanner.

Subscribes to one or more roots, debounces filesystem events, and on
each batch invokes :func:`steward.infra.scanner.incremental.scan_paths`
to refresh claims for the affected files.

Per ADR-0009 (pull-don't-push), this command NEVER mutates the
filesystem and NEVER auto-applies a policy plan. It only keeps the
inventory fresh; ``steward apply`` is the only path that writes to
disk.

Two run modes:

* default (long-lived) — runs until ``SIGINT``; reports a per-batch
  one-liner.
* ``--once`` — waits for the first non-empty batch (bounded by
  ``--idle-seconds``), processes it, and exits. Used by tests and
  scheduled runs.
"""
from __future__ import annotations

import signal
from pathlib import Path
from types import FrameType

import typer
from rich.console import Console

from steward.infra.db.admin import migrate, resolve_machine_id
from steward.infra.db.settings import inventory_db_path
from steward.infra.scanner.fsevents_watcher import FSEventsWatcher
from steward.infra.scanner.orchestrate import run_incremental_scan

app = typer.Typer(
    name="watch",
    help="Watch a root for filesystem changes and refresh claims incrementally.",
    invoke_without_command=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def watch_cmd(
    root: list[Path] = typer.Option(
        ...,
        "--root",
        help="Root to watch. Repeat for multiple roots.",
    ),
    debounce_ms: int = typer.Option(
        750,
        "--debounce-ms",
        min=50,
        help="Quiet period after the last event before flushing a batch.",
    ),
    idle_seconds: float = typer.Option(
        5.0,
        "--idle-seconds",
        min=0.5,
        help="Max wait per drain cycle. In --once mode, the command "
        "returns after a single drain that produced events OR this many "
        "seconds of complete silence.",
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="Process the first non-empty batch and exit. Used by tests "
        "and scheduled runs.",
    ),
    include_containers: bool = typer.Option(
        False,
        "--include-containers",
        help="Treat .zip / .tar* arrivals as containers and record "
        "member-level claims.",
    ),
) -> None:
    """Watch ``--root`` and refresh inventory claims as files change."""
    target = inventory_db_path()
    if not target.exists():
        console.print(f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]")
        migrate(target)
    machine_id = resolve_machine_id(target)

    watcher = FSEventsWatcher(
        roots=list(root),
        recursive=True,
        debounce_seconds=debounce_ms / 1000.0,
    )

    stop_requested = {"flag": False}

    def _on_sigint(signum: int, frame: FrameType | None) -> None:  # pragma: no cover
        del signum, frame
        stop_requested["flag"] = True
        console.print("[yellow]stopping watcher (SIGINT)…[/yellow]")

    signal.signal(signal.SIGINT, _on_sigint)

    watcher.start()
    roots_str = ", ".join(str(r) for r in root)
    console.print(
        f"[green]watching[/green] {roots_str} "
        f"(debounce={debounce_ms}ms, idle={idle_seconds:g}s, "
        f"mode={'once' if once else 'long-lived'})"
    )

    total_batches = 0
    total_files_seen = 0
    try:
        while not stop_requested["flag"]:
            batch = watcher.drain(max_wait_seconds=idle_seconds)
            if batch.is_empty():
                if once:
                    console.print("[dim]no events within idle window — exiting[/dim]")
                    break
                continue

            paths = batch.unique_paths(drop_deleted=True)
            total_batches += 1
            total_files_seen += len(paths)
            if not paths:
                console.print(
                    f"  batch {total_batches}: {len(batch)} events (all deletes — skipping scan)"
                )
                if once:
                    break
                continue

            stats = run_incremental_scan(
                paths=paths,
                db_path=target,
                machine_id=machine_id,
                include_containers=include_containers,
            )

            console.print(
                f"  batch {total_batches}: "
                f"{len(batch)} events → {len(paths)} files "
                f"(hashed={stats.files_hashed}, "
                f"reused={stats.files_reused}, "
                f"errored={stats.files_errored})"
            )
            if once:
                break
    finally:
        watcher.stop()
        console.print(
            f"[green]✓[/green] watcher stopped "
            f"(batches={total_batches}, files_seen={total_files_seen})"
        )


__all__ = ["app"]
