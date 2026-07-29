"""Smoke test — proves the package can be imported."""

from __future__ import annotations


def test_import() -> None:
    """Steward package imports without side effects."""
    import steward
    from steward._version import __version__ as version_module

    assert steward.__version__ == version_module


def test_version_string() -> None:
    """The version is a semver-shaped string."""
    import steward

    parts = steward.__version__.split(".")
    assert len(parts) == 3
    for p in parts:
        assert p.isdigit()
