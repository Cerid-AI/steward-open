# SPDX-License-Identifier: Apache-2.0

"""Wire-format manifest for cross-machine inventory snapshots (ADR-0013).

Every export envelope carries a ``manifest.json`` describing the
payload. The importer cross-checks the manifest against the actual
``inventory.db`` it unpacks: blake3 must match, audit row count must
match, and the ``schema_version`` must be compatible.

The format is versioned via :data:`WIRE_FORMAT_VERSION` — bump it
whenever the envelope shape or required fields change. v1 is the
shape ADR-0013 specifies.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

WIRE_FORMAT_VERSION = 1

#: Tables intentionally omitted from the wire format payload (ADR-0013).
#:
#: ``tiers`` is per-machine mount config; ``embeddings`` are large +
#: model-version-coupled; ``legacy_import_log`` is local provenance;
#: ``attached_inventories`` is local bookkeeping (would carry
#: machine A's view of machine X into a snapshot machine A hands to
#: machine B — meaningless).
EXCLUDED_TABLES_DEFAULT: tuple[str, ...] = (
    "tiers",
    "embeddings",
    "embeddings_vec",
    "legacy_import_log",
    "attached_inventories",
)

#: When ``--with-embeddings`` is passed, the embeddings tables stay in.
EXCLUDED_TABLES_WITH_EMBEDDINGS: tuple[str, ...] = (
    "tiers",
    "legacy_import_log",
    "attached_inventories",
)


class ExporterMetadata(BaseModel):
    """Identifies the producer of the snapshot."""

    model_config = ConfigDict(frozen=True)

    steward_version: str
    schema_version: str
    machine_id: str
    hostname: str | None = None


class PayloadMetadata(BaseModel):
    """Describes the embedded ``inventory.db`` payload."""

    model_config = ConfigDict(frozen=True)

    filename: str = "inventory.db"
    size_bytes: int = Field(ge=0)
    blake3: str
    audit_rows: int = Field(ge=0)
    claim_rows: int = Field(ge=0)
    permanode_rows: int = Field(ge=0)


class WireManifest(BaseModel):
    """Top-level manifest written as ``manifest.json`` in the envelope."""

    model_config = ConfigDict(frozen=True)

    wire_format_version: int = WIRE_FORMAT_VERSION
    exported_at: str
    exporter: ExporterMetadata
    payload: PayloadMetadata
    excluded_tables: list[str]

    def to_json(self) -> str:
        """Canonical JSON serialization — sorted keys + 2-space indent.

        Stable formatting matters for both human review and the
        blake3 included in the envelope's ``checksums.txt``.
        """
        return json.dumps(
            self.model_dump(),
            sort_keys=True,
            indent=2,
            separators=(",", ": "),
        ) + "\n"


@dataclass(frozen=True, slots=True)
class EnvelopeChecksums:
    """Pair of blake3 hashes that go in ``checksums.txt``.

    Format: one ``<hex>  <filename>`` line per file, sha256sum-style
    (two spaces between hash and filename).
    """

    manifest_blake3: str
    payload_blake3: str

    def to_text(self) -> str:
        return (
            f"{self.payload_blake3}  inventory.db\n"
            f"{self.manifest_blake3}  manifest.json\n"
        )


def load_manifest(path: Path) -> WireManifest:
    """Parse ``manifest.json`` from disk into a :class:`WireManifest`."""
    return WireManifest.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = [
    "EXCLUDED_TABLES_DEFAULT",
    "EXCLUDED_TABLES_WITH_EMBEDDINGS",
    "EnvelopeChecksums",
    "ExporterMetadata",
    "PayloadMetadata",
    "WIRE_FORMAT_VERSION",
    "WireManifest",
    "load_manifest",
]
