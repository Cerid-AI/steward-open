# SPDX-License-Identifier: Apache-2.0

"""Guards that the operator-facing docs stay in sync with the CLI.

If you add a new typer subcommand under ``src/steward/cli/``, this
test forces you to document it in README.md and QUICKSTART.md. Cheap
insurance against the v0.1.0 → v0.2.6 drift that left the README
claiming "v0.1 in flight" for seven patch releases.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from steward.cli import main as cli_main

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
QUICKSTART = REPO_ROOT / "docs" / "QUICKSTART.md"


def _registered_subcommands() -> set[str]:
    """Return the set of subcommand names registered on the root typer app.

    typer composes apps via ``add_typer`` and the registered groups
    carry their name on ``TyperInfo.name``. We pull that surface
    directly rather than parsing ``--help`` output (which would couple
    to terminal width).
    """
    names: set[str] = set()
    for group in cli_main.app.registered_groups:
        if group.name:
            names.add(group.name)
    return names


def test_every_subcommand_appears_in_readme() -> None:
    """Every typer subcommand must be referenced in README.md.

    We grep for ``steward <name>`` anywhere in the README body — that's
    how every subcommand is documented (either in the surface table or
    in a code block).
    """
    text = README.read_text(encoding="utf-8")
    missing = [
        name for name in sorted(_registered_subcommands())
        if f"steward {name}" not in text
    ]
    assert not missing, (
        f"README.md is missing references to: {missing}. "
        f"Add each as `steward <name>` somewhere in the doc."
    )


def test_every_subcommand_appears_in_quickstart() -> None:
    """Every typer subcommand must be exercised in docs/QUICKSTART.md."""
    text = QUICKSTART.read_text(encoding="utf-8")
    missing = [
        name for name in sorted(_registered_subcommands())
        if f"steward {name}" not in text
    ]
    assert not missing, (
        f"docs/QUICKSTART.md is missing walkthroughs for: {missing}. "
        f"Add each as `steward <name>` somewhere in the doc."
    )


def test_readme_does_not_claim_obsolete_version_status() -> None:
    """Guards against the v0.1 / v0.2 ROADMAP drift that left README
    behind for seven patch releases."""
    text = README.read_text(encoding="utf-8")
    forbidden = [
        "Status: v0.1 in flight",
        "v0.1.0 in flight",
        "[v0.2]",  # the old "[v0.2]" pending markers in the architecture section
    ]
    found = [s for s in forbidden if s in text]
    assert not found, (
        f"README.md contains obsolete status markers: {found}. "
        f"These were once-true claims about future work that have "
        f"since shipped."
    )


def test_changelog_head_matches_pkg_version() -> None:
    """``CHANGELOG.md`` should open with the version we publish — a
    crude check that we don't tag a release without a changelog entry.
    """
    from steward._version import __version__

    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # The latest ## header should be the current __version__.
    headers = [
        line for line in text.splitlines() if line.startswith("## [")
    ]
    assert headers, "CHANGELOG.md has no ## [version] headers"
    first = headers[0]
    assert __version__ in first, (
        f"CHANGELOG.md's first version header ({first!r}) does not "
        f"contain {__version__!r}. Did you forget to add a section?"
    )


# ─────────────────────── ROADMAP.md sanity ──────────────────────────


def test_roadmap_exists_and_is_non_empty() -> None:
    p = REPO_ROOT / "docs" / "ROADMAP.md"
    assert p.exists(), "docs/ROADMAP.md is required (referenced from README)"
    assert len(p.read_text(encoding="utf-8")) > 200


@pytest.mark.parametrize(
    "version_tag",
    [
        "v0.1.0",
        "v0.2.0",
        "v0.2.6",
    ],
)
def test_roadmap_acknowledges_shipped_releases(version_tag: str) -> None:
    """The ROADMAP should mention every milestone-level shipped release."""
    p = REPO_ROOT / "docs" / "ROADMAP.md"
    assert version_tag in p.read_text(encoding="utf-8"), (
        f"docs/ROADMAP.md should mention {version_tag}"
    )
