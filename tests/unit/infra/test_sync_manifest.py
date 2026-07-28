# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the cross-machine wire-format manifest."""
from __future__ import annotations

import json

from steward.infra.sync.manifest import (
    EXCLUDED_TABLES_DEFAULT,
    EXCLUDED_TABLES_WITH_EMBEDDINGS,
    WIRE_FORMAT_VERSION,
    EnvelopeChecksums,
    ExporterMetadata,
    PayloadMetadata,
    WireManifest,
)


def _make_manifest() -> WireManifest:
    return WireManifest(
        exported_at="2026-05-16T15:30:00+00:00",
        exporter=ExporterMetadata(
            steward_version="0.3.0",
            schema_version="0002_attached_inventories",
            machine_id="f3c2a1d4-1111-2222-3333-444455556666",
            hostname="mac-pro",
        ),
        payload=PayloadMetadata(
            size_bytes=5_242_880,
            blake3="abc123",
            audit_rows=12345,
            claim_rows=178000,
            permanode_rows=56000,
        ),
        excluded_tables=list(EXCLUDED_TABLES_DEFAULT),
    )


def test_wire_format_version_is_one() -> None:
    """v1 is the format ADR-0013 specifies. Bumping requires an ADR."""
    assert WIRE_FORMAT_VERSION == 1


def test_default_exclusion_set() -> None:
    """The default-exclude set drops tiers + embeddings + legacy + attached."""
    assert "embeddings" in EXCLUDED_TABLES_DEFAULT
    assert "embeddings_vec" in EXCLUDED_TABLES_DEFAULT
    assert "tiers" in EXCLUDED_TABLES_DEFAULT
    assert "legacy_import_log" in EXCLUDED_TABLES_DEFAULT
    assert "attached_inventories" in EXCLUDED_TABLES_DEFAULT


def test_with_embeddings_keeps_embeddings_tables() -> None:
    """--with-embeddings keeps the embeddings + vec tables."""
    assert "embeddings" not in EXCLUDED_TABLES_WITH_EMBEDDINGS
    assert "embeddings_vec" not in EXCLUDED_TABLES_WITH_EMBEDDINGS
    # The other excludes still apply.
    assert "tiers" in EXCLUDED_TABLES_WITH_EMBEDDINGS
    assert "legacy_import_log" in EXCLUDED_TABLES_WITH_EMBEDDINGS


def test_manifest_to_json_is_sorted_and_pretty() -> None:
    """``to_json`` produces deterministic, human-readable output."""
    m = _make_manifest()
    text = m.to_json()
    # Sorted keys
    assert text.index("excluded_tables") < text.index("exported_at")
    assert text.index("exported_at") < text.index("exporter")
    # Pretty-printed
    assert "  " in text
    # Trailing newline (so the blake3 over the bytes is stable)
    assert text.endswith("\n")
    # Parseable
    data = json.loads(text)
    assert data["wire_format_version"] == WIRE_FORMAT_VERSION


def test_manifest_roundtrips_via_model_validate_json() -> None:
    """A manifest written + parsed yields the same object."""
    m = _make_manifest()
    text = m.to_json()
    m2 = WireManifest.model_validate_json(text)
    assert m2 == m


def test_envelope_checksums_format() -> None:
    """``checksums.txt`` matches sha256sum-style two-space-separated layout."""
    c = EnvelopeChecksums(
        manifest_blake3="aaaa",
        payload_blake3="bbbb",
    )
    text = c.to_text()
    assert text == "bbbb  inventory.db\naaaa  manifest.json\n"
