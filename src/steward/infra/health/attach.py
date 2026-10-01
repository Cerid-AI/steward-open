# SPDX-License-Identifier: Apache-2.0

"""Volumes the estate reserves for another host, found mounted or attached here.

Feeds the ``foreign_attach`` check: a ``forbid`` mount that is present, or a
disk image with ``exclusive_attach`` set to another host that ``hdiutil info``
lists as attached. A sparsebundle attached by two hosts at once is corrupted,
so this is a hard failure, not a warning.

The image probe is macOS only; elsewhere, or without ``hdiutil``, it is a no-op
and only the mount side is checked.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from typing import Any

from steward.core.estate import Estate
from steward.core.health.model import ForeignAttachment
from steward.infra.estate.check import check_mounts
from steward.infra.observability.swallowed import log_swallowed_error

_HDIUTIL_TIMEOUT_S = 30.0


def _hdiutil() -> str | None:
    return shutil.which("hdiutil") if sys.platform == "darwin" else None


def parse_hdiutil_info(raw: bytes) -> list[str]:
    """``image-path`` of every image in ``hdiutil info -plist`` output."""
    data: Any = plistlib.loads(raw)
    images = data.get("images", []) if isinstance(data, dict) else []
    return [str(img["image-path"]) for img in images if isinstance(img, dict) and img.get("image-path")]


def attached_images() -> list[str] | None:
    """Disk images attached on this machine; ``None`` when that cannot be asked."""
    exe = _hdiutil()
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, "info", "-plist"], capture_output=True, timeout=_HDIUTIL_TIMEOUT_S, check=False
        )
        if proc.returncode != 0:
            return None
        return parse_hdiutil_info(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, plistlib.InvalidFileException, ValueError) as exc:
        log_swallowed_error("health.attach.hdiutil_info", exc)
        return None


def foreign_attachments(
    estate: Estate,
    host_id: str,
    *,
    images: list[str] | None,
    live: set[str] | None = None,
) -> list[ForeignAttachment]:
    """``forbid`` mounts present on ``host_id``, and exclusive images attached on a non-owner.

    ``images`` is :func:`attached_images` (``None``: image side not checked);
    ``live`` the mounted ``/Volumes`` paths, as for :func:`check_mounts`.
    """
    found = [
        ForeignAttachment("forbid_mount", f.volume_id or "", f.path or "", f.message)
        for f in check_mounts(estate, host_id, live=live)
        if f.kind == "forbidden_mount"
    ]
    if images is None:
        return found
    attached = {os.path.normpath(p) for p in images}
    for vol_id, vol in estate.volumes.items():
        if vol.image is None or vol.exclusive_attach in (None, host_id):
            continue
        if os.path.normpath(vol.image) in attached:
            found.append(
                ForeignAttachment(
                    "exclusive_image",
                    vol_id,
                    vol.image,
                    f"{vol.image} is attached on {host_id!r} but only {vol.exclusive_attach!r} may attach it",
                )
            )
    return found


__all__ = ["attached_images", "foreign_attachments", "parse_hdiutil_info"]
