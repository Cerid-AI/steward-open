"""Smoke test — proves the package can be imported."""
from __future__ import annotations


def test_import() -> None:
    """Steward package imports without side effects."""
    import steward

    assert steward.__version__ == "0.3.17"


def test_version_string() -> None:
    """The version is a semver-shaped string."""
    import steward

    parts = steward.__version__.split(".")
    assert len(parts) == 3
    for p in parts:
        assert p.isdigit()
