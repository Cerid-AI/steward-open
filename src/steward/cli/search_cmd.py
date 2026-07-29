# SPDX-License-Identifier: Apache-2.0

"""``steward search`` — semantic search over permanode embeddings.

Top-k retrieval via :func:`steward.infra.embed.search.semantic_search`.
Output is a table of canonical paths sorted by cosine distance
(closest first). When no embeddings have been written yet, exits with
a hint to run ``steward embed``.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from steward.core.embed import EmbedderProtocol
from steward.infra.db.settings import inventory_db_path
from steward.infra.embed import StubEmbedder
from steward.infra.embed.onnx import OnnxE5Embedder, OnnxModelNotFoundError
from steward.infra.embed.orchestrate import run_semantic_search

app = typer.Typer(
    name="search",
    help="Semantic search over permanode embeddings.",
    invoke_without_command=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def search_cmd(
    query: str = typer.Argument(..., help="Free-text query."),
    k: int = typer.Option(10, "--k", min=1, help="Number of results to return."),
    backend: str = typer.Option(
        "stub",
        "--backend",
        help="Embedder backend: must match the one used during `steward embed`.",
    ),
    model_name: str = typer.Option(
        "multilingual-e5-small",
        "--model-name",
        help="Model name for the onnx backend.",
    ),
    model_dir: Path | None = typer.Option(
        None,
        "--model-dir",
        help="Override location of the ONNX model files.",
    ),
) -> None:
    """Return the ``--k`` permanodes nearest to ``query`` in embedding space."""
    target = inventory_db_path()
    if not target.exists():
        console.print(
            f"[red]inventory.db not found at {target}; run `steward db migrate` and `steward embed` first.[/red]"
        )
        raise typer.Exit(2)

    embedder: EmbedderProtocol
    if backend == "stub":
        embedder = StubEmbedder()
    elif backend == "onnx":
        try:
            embedder = OnnxE5Embedder(
                model_name=model_name,
                model_dir=model_dir,
            )
        except OnnxModelNotFoundError as exc:  # pragma: no cover
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc
    else:
        console.print(f"[red]unknown backend: {backend}[/red]")
        raise typer.Exit(2)

    results = run_semantic_search(db_path=target, embedder=embedder, query=query, k=k)

    if not results:
        console.print("[yellow]no results — run `steward embed` to populate embeddings, or widen --k.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("rank", style="dim", width=4, justify="right")
    table.add_column("distance", style="dim", width=10, justify="right")
    table.add_column("permanode")
    table.add_column("path")
    for i, r in enumerate(results, start=1):
        table.add_row(
            str(i),
            f"{r.distance:.4f}",
            r.permanode_id[:10] + "…",
            r.canonical_path,
        )
    console.print(table)


__all__ = ["app"]
