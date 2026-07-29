# SPDX-License-Identifier: Apache-2.0

"""``steward embed`` — compute and store permanode embeddings.

Two backends:

* ``--backend stub`` (default) — deterministic hash-based embedder; no
  model required. Useful for smoke-testing the embed pipeline + for
  CI. Produces non-semantic vectors (don't expect meaningful similarity).
* ``--backend onnx`` — the real ONNX e5 embedder. Requires
  ``onnxruntime`` and a downloaded model under
  ``~/.cache/steward/models/<model>/`` (see :mod:`steward.infra.embed.onnx`).
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from steward.core.embed import EmbedderProtocol
from steward.infra.db.admin import migrate
from steward.infra.db.settings import inventory_db_path
from steward.infra.embed import StubEmbedder
from steward.infra.embed.onnx import OnnxE5Embedder, OnnxModelNotFoundError
from steward.infra.embed.orchestrate import run_embed_batch

app = typer.Typer(
    name="embed",
    help="Compute permanode embeddings into the vec0 inventory.",
    invoke_without_command=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def embed_cmd(
    backend: str = typer.Option(
        "stub",
        "--backend",
        help="Embedder backend: 'stub' (deterministic, no model) or 'onnx'.",
    ),
    model_name: str = typer.Option(
        "multilingual-e5-small",
        "--model-name",
        help="Model identifier for the onnx backend.",
    ),
    model_dir: Path | None = typer.Option(
        None,
        "--model-dir",
        help="Override location of the ONNX model files. Defaults to ~/.cache/steward/models/<model-name>/.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Limit to N permanodes (None = process all candidates).",
    ),
    reembed_all: bool = typer.Option(
        False,
        "--reembed-all",
        help="Re-embed every permanode, regardless of existing rows.",
    ),
    batch_size: int = typer.Option(
        32,
        "--batch-size",
        min=1,
        help="Mini-batch size handed to the embedder.",
    ),
) -> None:
    """Embed permanodes lacking an embedding for the chosen backend."""
    target = inventory_db_path()
    if not target.exists():
        console.print(f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]")
        migrate(target)

    embedder: EmbedderProtocol
    if backend == "stub":
        embedder = StubEmbedder()
    elif backend == "onnx":
        try:
            embedder = OnnxE5Embedder(
                model_name=model_name,
                model_dir=model_dir,
            )
            # Force a load now so missing-model errors surface before
            # any database side-effects.
            _ = embedder.info
        except OnnxModelNotFoundError as exc:  # pragma: no cover
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc
    else:
        console.print(f"[red]unknown backend: {backend}[/red]")
        raise typer.Exit(2)

    report = run_embed_batch(
        db_path=target,
        embedder=embedder,
        limit=limit,
        reembed_all=reembed_all,
        batch_size=batch_size,
    )

    console.print(
        f"[green]✓[/green] embed complete "
        f"(backend={backend}, model={embedder.info.model_name}@{embedder.info.model_version})"
    )
    console.print(f"  candidates       = {report.candidates:,}")
    console.print(f"  embedded         = {report.embedded:,}")
    console.print(f"  skipped_existing = {report.skipped_existing:,}")
    console.print(f"  errors           = {report.errors:,}")


__all__ = ["app"]
