# SPDX-License-Identifier: Apache-2.0

"""Lightweight Cloud File Provider / Dropbox fork probe.

Does **not** run ``fileproviderctl dump`` (can take minutes on large
domains). Surfaces path existence, device IDs, and sample dual-presence
so operators can choose mount cloud-retire vs store local reclaim
without a full Dropbox rectification pass.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PathProbe:
    path: str
    exists: bool
    is_dir: bool
    st_dev: int | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DualSample:
    relative: str
    store_exists: bool
    mount_exists: bool
    store_size: int | None
    mount_size: int | None
    size_match: bool | None


@dataclass(frozen=True, slots=True)
class FPStatusReport:
    mount_root: str
    store_root: str
    mount: PathProbe
    store: PathProbe
    forked_devices: bool
    """True when both paths exist and st_dev differs (or one missing)."""
    dual_samples: list[DualSample] = field(default_factory=list)
    sample_both: int = 0
    sample_store_only: int = 0
    sample_mount_only: int = 0
    sample_neither: int = 0
    recommendations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_DEFAULT_SAMPLE_RELS: tuple[str, ...] = (
    "logo.jpg",
    "Books.xlsx",
    "Home",
    "Claims",
    "cerid-archive",
)


def _probe(path: Path) -> PathProbe:
    try:
        exists = path.exists()
        is_dir = path.is_dir() if exists else False
        st_dev = path.stat().st_dev if exists else None
        return PathProbe(
            path=str(path),
            exists=exists,
            is_dir=is_dir,
            st_dev=int(st_dev) if st_dev is not None else None,
        )
    except OSError as exc:
        return PathProbe(
            path=str(path),
            exists=False,
            is_dir=False,
            st_dev=None,
            error=repr(exc),
        )


def _sample_pair(
    *, store_root: Path, mount_root: Path, relative: str
) -> DualSample:
    sp = store_root / relative
    mp = mount_root / relative
    se = me = False
    ss = ms = None
    try:
        se = sp.exists()
        if se and sp.is_file():
            ss = sp.stat().st_size
        elif se and sp.is_dir():
            ss = -1  # directory marker
    except OSError:
        se = False
    try:
        me = mp.exists()
        if me and mp.is_file():
            ms = mp.stat().st_size
        elif me and mp.is_dir():
            ms = -1
    except OSError:
        me = False
    match: bool | None
    if se and me and ss is not None and ms is not None:
        match = ss == ms
    else:
        match = None
    return DualSample(
        relative=relative,
        store_exists=se,
        mount_exists=me,
        store_size=ss,
        mount_size=ms,
        size_match=match,
    )


def collect_fp_status(
    *,
    home: Path | None = None,
    store_root: Path | None = None,
    mount_root: Path | None = None,
    sample_rels: tuple[str, ...] = _DEFAULT_SAMPLE_RELS,
) -> FPStatusReport:
    """Probe Dropbox store + mount without heavy FP dumps."""
    home = home or Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    mount = mount_root or (home / "Library/CloudStorage/Dropbox")
    store = store_root or Path(
        "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox"
    )

    m_probe = _probe(mount)
    s_probe = _probe(store)

    forked = False
    if m_probe.exists and s_probe.exists:
        forked = m_probe.st_dev != s_probe.st_dev
    elif m_probe.exists != s_probe.exists:
        forked = True

    samples = [
        _sample_pair(store_root=store, mount_root=mount, relative=rel)
        for rel in sample_rels
    ]
    both = store_only = mount_only = neither = 0
    for s in samples:
        if s.store_exists and s.mount_exists:
            both += 1
        elif s.store_exists:
            store_only += 1
        elif s.mount_exists:
            mount_only += 1
        else:
            neither += 1

    recs: list[str] = []
    notes: list[str] = [
        "Dropbox tree rectification is deferred — needs history + API review.",
        "This probe does not call fileproviderctl dump.",
    ]
    if forked:
        recs.append(
            "Devices differ or one root missing: treat store and mount as "
            "possibly forked materializations."
        )
        recs.append(
            "Cloud-propagating retire: ensure objects exist on the mount "
            "(rescan ~/Library/CloudStorage/Dropbox); default retire_direct."
        )
        recs.append(
            "Local free space on external volume only: "
            "apply --allow-store-path-unlink (no cloud trash guarantee)."
        )
    else:
        recs.append(
            "Mount and store share a device id — still verify dual presence "
            "before multi-TiB bulk."
        )
    if store_only and not both:
        recs.append(
            "Sample is store-heavy: inventory scanned from store paths may "
            "refuse default mount unlink until mount rescan."
        )
    if m_probe.error or s_probe.error:
        recs.append(
            "Path stat errors (often FP timeout): settle Dropbox, retry "
            "with caffeinate; defer bulk apply."
        )

    return FPStatusReport(
        mount_root=str(mount),
        store_root=str(store),
        mount=m_probe,
        store=s_probe,
        forked_devices=forked,
        dual_samples=samples,
        sample_both=both,
        sample_store_only=store_only,
        sample_mount_only=mount_only,
        sample_neither=neither,
        recommendations=recs,
        notes=notes,
    )


def fp_status_to_dict(report: FPStatusReport) -> dict[str, Any]:
    return asdict(report)


__all__ = [
    "DualSample",
    "FPStatusReport",
    "PathProbe",
    "collect_fp_status",
    "fp_status_to_dict",
]
