# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the bundled launchd plist templates.

The plists are static XML; substitution is string-based. Tests verify:

1. Every bundled template parses as valid XML + has a `<Label>`.
2. The label discovery surface returns the expected templates.
3. Substitution replaces `{HOME}` / `{STEWARD_BIN}` / `{LOG_DIR}`.
4. `write_resolved_plist` writes the materialized text + sets mode 0644.
5. Unknown template names raise `TemplateNotFoundError`.
"""
from __future__ import annotations

import plistlib
import stat
from pathlib import Path

import pytest

from steward.infra.schedule.templates import (
    TemplateNotFoundError,
    list_templates,
    materialize_template,
    resolve_template,
    write_resolved_plist,
)

# ─────────────────────── bundled templates ──────────────────────────


_EXPECTED_TEMPLATES = {
    "nightly-archive",
    "nightly-replicate",
    "weekly-verify",
    "weekly-inventory-export",
}


def test_bundled_templates_include_expected_set() -> None:
    names = {t.name for t in list_templates()}
    assert _EXPECTED_TEMPLATES <= names


def test_every_bundled_template_parses_as_valid_plist() -> None:
    for tmpl in list_templates():
        body = tmpl.path.read_bytes()
        # Must parse without errors and produce a dict root.
        parsed = plistlib.loads(body)
        assert isinstance(parsed, dict), f"{tmpl.name}: not a plist dict"
        assert "Label" in parsed, f"{tmpl.name}: missing Label"


def test_every_bundled_template_has_a_label() -> None:
    for tmpl in list_templates():
        assert tmpl.label.startswith("com.cerid.steward."), (
            f"{tmpl.name}: label {tmpl.label!r} should be in the "
            f"com.cerid.steward.* domain"
        )


# ─────────────────────── substitution ──────────────────────────


def test_materialize_replaces_all_three_placeholders(tmp_path: Path) -> None:
    text, tmpl = materialize_template(
        "nightly-archive",
        home="/home/operator",
        steward_bin="/opt/steward/bin/steward",
        log_dir="/var/log/steward",
    )
    assert "{HOME}" not in text
    assert "{STEWARD_BIN}" not in text
    assert "{LOG_DIR}" not in text
    assert "/home/operator" in text
    assert "/opt/steward/bin/steward" in text
    assert "/var/log/steward" in text
    # And the resulting plist must still parse cleanly.
    parsed = plistlib.loads(text.encode("utf-8"))
    assert isinstance(parsed, dict)
    assert tmpl.name == "nightly-archive"


def test_materialize_with_defaults_substitutes_real_home() -> None:
    """When no explicit home is supplied, the operator's $HOME is used."""
    text, _ = materialize_template("weekly-verify")
    assert "{HOME}" not in text
    assert "{LOG_DIR}" not in text
    assert str(Path.home()) in text


def test_unknown_template_raises_friendly_error() -> None:
    with pytest.raises(TemplateNotFoundError) as exc:
        resolve_template("does-not-exist")
    assert "does-not-exist" in str(exc.value)
    # Error message lists what IS available.
    assert "nightly-archive" in str(exc.value)


# ─────────────────────── write_resolved_plist ──────────────────────────


def test_write_resolved_plist_creates_file_and_sets_mode(
    tmp_path: Path,
) -> None:
    target = tmp_path / "Library" / "LaunchAgents" / "x.plist"
    written, tmpl = write_resolved_plist(
        "nightly-archive",
        target_path=target,
        home="/home/operator",
        steward_bin="/usr/local/bin/steward",
        log_dir=str(tmp_path / "logs"),
    )
    assert written == target
    assert target.exists()
    # Mode 0644 (rw-r--r--) — launchd refuses world-writable plists.
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o644
    # Content is the substituted text.
    body = target.read_text(encoding="utf-8")
    assert "/home/operator" in body
    assert "/usr/local/bin/steward" in body
    assert "{HOME}" not in body
    # Parses cleanly as a plist (not just generic XML).
    parsed = plistlib.loads(body.encode("utf-8"))
    assert isinstance(parsed, dict)
    assert tmpl.label == "com.cerid.steward.nightly-archive"


def test_write_resolved_plist_creates_parent_directories(
    tmp_path: Path,
) -> None:
    """The function must mkdir-parents so the operator doesn't have to
    pre-create ``~/Library/LaunchAgents/``."""
    deep = tmp_path / "a" / "b" / "c" / "out.plist"
    write_resolved_plist(
        "weekly-verify",
        target_path=deep,
        home="/home/operator",
        steward_bin="/x/steward",
        log_dir="/tmp/logs",
    )
    assert deep.exists()


# ─────────────────────── installed_plist_path ──────────────────────────


def test_installed_plist_path_uses_home_library_launchagents() -> None:
    """Convention: ``~/Library/LaunchAgents/<label>.plist``."""
    tmpl = resolve_template("weekly-verify")
    expected = (
        Path.home() / "Library" / "LaunchAgents" /
        "com.cerid.steward.weekly-verify.plist"
    )
    assert tmpl.installed_plist_path == expected
