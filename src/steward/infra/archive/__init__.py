"""restic-backed archive adapter.

An :class:`ArchivePolicy` lists one or more sources to snapshot into
encrypted restic repositories. The runner walks the sources, invokes
``restic backup`` per source, and audit-logs the result. Encryption
is restic's; Steward never reads or echoes the repository password.

When ``restic`` isn't on ``PATH`` we surface
:class:`ResticNotInstalledError` (mirrors the ``rclone`` pattern).
"""

from steward.infra.archive.restic import (
    ResticNotInstalledError,
    ResticRunResult,
    restic_available,
    run_restic_backup,
    run_restic_init,
    run_restic_snapshots,
)
from steward.infra.archive.runner import (
    ArchiveListReport,
    ArchiveSnapshotReport,
    SnapshotReport,
    run_archive_init,
    run_archive_list,
    run_archive_snapshot,
)

__all__ = [
    "ArchiveListReport",
    "ArchiveSnapshotReport",
    "ResticNotInstalledError",
    "ResticRunResult",
    "SnapshotReport",
    "restic_available",
    "run_archive_init",
    "run_archive_list",
    "run_archive_snapshot",
    "run_restic_backup",
    "run_restic_init",
    "run_restic_snapshots",
]
