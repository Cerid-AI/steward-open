# SPDX-License-Identifier: Apache-2.0

"""``steward policy`` subcommand group — lint, show, plan."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from steward.core.errors import PolicyError
from steward.core.policy import load_policy
from steward.infra.db.plan import plan

app = typer.Typer(name="policy", help="Validate, display, and run policies.", no_args_is_help=True)
console = Console()

# Bundled policies live next to the source so they ship in the wheel.
_BUNDLED_DIR = Path(__file__).resolve().parent.parent / "policies"


@app.command("lint")
def lint_cmd(
    path: Path = typer.Argument(..., help="Path to a policy YAML to validate."),
) -> None:
    """Parse + validate a policy YAML; exits 0 if valid, 1 otherwise."""
    try:
        policy = load_policy(path)
    except PolicyError as exc:
        console.print(f"[red]✗[/red] {path}: {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓[/green] {path}: valid {policy.kind} v{policy.version}")


@app.command("show")
def show_cmd(
    name: str = typer.Argument(..., help="Bundled policy name (e.g. 'retention.yml') OR a filesystem path."),
) -> None:
    """Print the bundled policy YAML; useful when the operator wants to
    copy it as a starting point for an override under
    ``~/.config/steward/policies.d/``."""
    if "/" in name or name.endswith(".yml") and (Path.cwd() / name).exists():
        candidate = Path(name)
    else:
        candidate = _BUNDLED_DIR / name
    if not candidate.exists():
        console.print(f"[red]✗[/red] policy not found: {candidate}")
        raise typer.Exit(1)
    console.print(candidate.read_text())


def _resolve_policy_path(name_or_path: str) -> Path:
    """Resolve ``name_or_path`` to either a CWD file or a bundled policy."""
    p = Path(name_or_path)
    if p.exists():
        return p
    bundled = _BUNDLED_DIR / name_or_path
    if bundled.exists():
        return bundled
    raise typer.BadParameter(f"policy not found: {name_or_path} (looked at {p} and {bundled})")


@app.command("plan")
def plan_cmd(
    policy: str = typer.Option(
        ...,
        "--policy",
        help="Policy YAML (bundled name like 'retention.yml' or a path).",
    ),
    out: Path = typer.Option(..., "--out", help="Where to write the manifest TSV."),
    root: str | None = typer.Option(
        None,
        "--root",
        help="Path-prefix filter (RetentionPolicy only).",
    ),
    phase: str | None = typer.Option(
        None,
        "--phase",
        help="Phase-name filter (PromotionPolicy only).",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Cap the number of rows in the produced manifest (PromotionPolicy only).",
    ),
) -> None:
    """Generate a plan manifest by reconciling the policy against current claims.

    Dispatch by policy kind:
      * ``RetentionPolicy`` → dedup-retire manifest (stash + nas_manifest rows)
      * ``PromotionPolicy`` → promote manifest (one row per Backup-only permanode
        matching a phase)
    """
    try:
        policy_path = _resolve_policy_path(policy)
    except typer.BadParameter as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc

    summary = plan(
        policy_path=policy_path,
        out_path=out,
        root_prefix=root,
        phase_name=phase,
        max_files=limit,
    )
    console.print(f"[green]✓[/green] wrote {summary.out_path}")
    console.print(f"  manifest_run_id   = {summary.manifest_run_id}")
    console.print(f"  rows total        = {summary.rows:,}")
    if summary.stash_rows or summary.nas_manifest_rows:
        console.print(f"  stash rows        = {summary.stash_rows:,}")
        console.print(f"  nas_manifest rows = {summary.nas_manifest_rows:,}")
    if summary.promote_rows:
        console.print(f"  promote rows      = {summary.promote_rows:,}")
    if root:
        console.print(f"  root prefix       = {root}")
    if phase:
        console.print(f"  phase             = {phase}")
