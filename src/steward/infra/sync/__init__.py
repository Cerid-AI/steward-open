# SPDX-License-Identifier: Apache-2.0

"""Cross-machine inventory sync — wire format + export/import (ADR-0013).

v0.3.0 ships the **export** side: take the local ``inventory.db`` and
write a portable ``tar.xz`` envelope another Steward instance can
attach. Import / detach / verify land in subsequent v0.3.x sprints.
"""

from steward.infra.sync.attach import (
    AttachContext,
    AttachedSchema,
    attach_imports,
)
from steward.infra.sync.exporter import (
    ExportError,
    ExportResult,
    export_inventory,
)
from steward.infra.sync.importer import (
    ImportError_ as ImportError,
)
from steward.infra.sync.importer import (
    ImportResult,
    import_inventory,
)
from steward.infra.sync.imports_admin import (
    AttachedInventoryRow,
    DetachResult,
    ImportsAdminError,
    ImportVerification,
    VerifyImportsReport,
    detach_import,
    get_import,
    list_imports,
    verify_imports,
)
from steward.infra.sync.manifest import (
    EXCLUDED_TABLES_DEFAULT,
    EXCLUDED_TABLES_WITH_EMBEDDINGS,
    WIRE_FORMAT_VERSION,
    EnvelopeChecksums,
    ExporterMetadata,
    PayloadMetadata,
    WireManifest,
    load_manifest,
)

__all__ = [
    "EXCLUDED_TABLES_DEFAULT",
    "EXCLUDED_TABLES_WITH_EMBEDDINGS",
    "AttachContext",
    "AttachedInventoryRow",
    "AttachedSchema",
    "DetachResult",
    "EnvelopeChecksums",
    "ExportError",
    "ExportResult",
    "ExporterMetadata",
    "ImportError",
    "ImportResult",
    "ImportVerification",
    "ImportsAdminError",
    "PayloadMetadata",
    "VerifyImportsReport",
    "WIRE_FORMAT_VERSION",
    "WireManifest",
    "attach_imports",
    "detach_import",
    "export_inventory",
    "get_import",
    "import_inventory",
    "list_imports",
    "load_manifest",
    "verify_imports",
]
