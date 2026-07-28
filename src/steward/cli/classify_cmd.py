# SPDX-License-Identifier: Apache-2.0

"""``steward classify`` — assign domain + classification labels to claims."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from steward.infra.db.classify import classify_claims

app = typer.Typer(name="classify", help="Assign domain + classification labels to claims.", invoke_without_command=True)
console = Console()

_BUNDLED_DIR = Path(__file__).resolve().parent.parent / "policies"


@app.callback(invoke_without_command=True)
def classify_cmd(
    policy: str = typer.Option(
        "classification.yml",
        "--policy",
        help="Classification policy YAML (bundled name or path).",
    ),
    reclassify_all: bool = typer.Option(
        False,
        "--reclassify-all",
        help="Re-classify every claim (default: only NULL fields).",
    ),
) -> None:
    """Walk claims + label them per the classification policy.

    Default: only claims with ``domain IS NULL`` or ``classification IS NULL``
    are touched. Pass ``--reclassify-all`` to refresh every row after
    editing the policy YAML.
    """
    p = Path(policy)
    if not p.exists():
        candidate = _BUNDLED_DIR / policy
        if candidate.exists():
            p = candidate
        else:
            console.print(f"[red]✗[/red] policy not found: {policy}")
            raise typer.Exit(1)
    result = classify_claims(policy_path=p, reclassify_all=reclassify_all)
    console.print(f"[green]✓[/green] classified {result.claims_scanned:,} claims")
    console.print(f"  domain updated         = {result.domain_updated:,}")
    console.print(f"  classification updated = {result.classification_updated:,}")
    console.print(f"  reclassify_all         = {result.reclassify_all}")
