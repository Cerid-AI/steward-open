"""rclone-backed replication adapter.

A :class:`ReplicationPolicy` lists one or more sources to replicate
off-machine. The runner walks the sources and invokes ``rclone copy``
(or ``rclone sync``) once per source, bracketing the work with
``replicate_start`` / ``replicate_end`` audit-log entries.

When ``rclone`` isn't on ``PATH`` we surface a friendly
:class:`RcloneNotInstalledError` (mirrors the ``hdiutil`` / ``unar``
pattern in the container walker).
"""

from steward.infra.replicate.rclone import (
    RcloneNotInstalledError,
    RcloneRunResult,
    rclone_available,
    run_rclone,
)
from steward.infra.replicate.runner import (
    ReplicationReport,
    SourceReport,
    run_replication,
)

__all__ = [
    "RcloneNotInstalledError",
    "RcloneRunResult",
    "ReplicationReport",
    "SourceReport",
    "rclone_available",
    "run_rclone",
    "run_replication",
]
