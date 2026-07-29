# SPDX-License-Identifier: Apache-2.0

"""Lightweight Cloud File Provider / Dropbox layout + health probe.

Does **not** run ``fileproviderctl dump`` (can take minutes on large
domains). Surfaces path existence, device IDs, sample dual-presence,
FP domain metadata, top-level name divergence, and a structured
:class:`FPHealthVerdict`.

**Layout model (2026-07-28):** external-drive File Provider is a
*supported healthy* layout:

* Preferences / ``info.json`` path → store on ``/Volumes/DropboxStorage/...``
* Finder presentation → ``~/Library/CloudStorage/Dropbox``
* Different ``st_dev`` is **normal**, not a hard failure by itself
* Domains.plist may show residual ``unlinked`` / ``FPFS_SHOULD_NOT_BE_USED``
  while the Dropbox app reports healthy sync — treat as **warning**, not
  automatic re-link mandate

Hard failures for cloud-propagating retire are: missing/unstatable mount,
or dual-presence samples that are store-only with no mount twins.
"""

from __future__ import annotations

import json
import os
import plistlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

LayoutKind = Literal[
    "external_drive_fp",
    "unified_volume",
    "store_only",
    "mount_only",
    "missing",
]


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
class DomainProbe:
    """Dropbox File Provider domain metadata (from Domains.plist)."""

    provider_id: str
    domain_id: str | None
    connected: bool | None
    disconnected: bool | None
    disconnection_reason: str | None
    domain_path: str | None
    supports_syncing_trash: bool | None
    error: str | None = None

    @property
    def reports_disconnected(self) -> bool:
        if self.disconnected is True and self.connected is False:
            return True
        reason = (self.disconnection_reason or "").lower()
        return "unlinked" in reason

    @property
    def is_fpfs_placeholder(self) -> bool:
        """True when domain Path says materialization is not boot FPFS."""
        return self.domain_path == "FPFS_SHOULD_NOT_BE_USED"

    @property
    def is_unlinked(self) -> bool:
        """Backward-compatible: residual or hard disconnect metadata present."""
        if self.is_fpfs_placeholder:
            return True
        return self.reports_disconnected


@dataclass(frozen=True, slots=True)
class NameDivergence:
    """Top-level basename sets that exist on only one side."""

    store_only: tuple[str, ...] = ()
    mount_only: tuple[str, ...] = ()
    both_count: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class FPHealthVerdict:
    """Structured Dropbox layout + readiness for Steward intents."""

    layout: LayoutKind
    cloud_retire_ready: bool
    local_reclaim_ready: bool
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


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
    domain: DomainProbe | None = None
    dropbox_info_path: str | None = None
    name_divergence: NameDivergence | None = None
    verdict: FPHealthVerdict | None = None


_DEFAULT_SAMPLE_RELS: tuple[str, ...] = (
    "logo.jpg",
    "Books.xlsx",
    "Home",
    "Claims",
    "cerid-archive",
)

_DROPBOX_PROVIDER_ID = "com.getdropbox.dropbox.fileprovider"
_DEFAULT_STORE = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox"


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


def _sample_pair(*, store_root: Path, mount_root: Path, relative: str) -> DualSample:
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


def _read_dropbox_info_path(home: Path) -> str | None:
    info = home / ".dropbox" / "info.json"
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    personal = data.get("personal") if isinstance(data, dict) else None
    if isinstance(personal, dict):
        path = personal.get("path")
        if isinstance(path, str) and path:
            return path
    return None


def _probe_dropbox_domain(home: Path) -> DomainProbe:
    """Read FileProvider Domains.plist for Dropbox (no dump)."""
    plist_path = home / "Library" / "Application Support" / "FileProvider" / _DROPBOX_PROVIDER_ID / "Domains.plist"
    if not plist_path.is_file():
        return DomainProbe(
            provider_id=_DROPBOX_PROVIDER_ID,
            domain_id=None,
            connected=None,
            disconnected=None,
            disconnection_reason=None,
            domain_path=None,
            supports_syncing_trash=None,
            error=f"missing {plist_path}",
        )
    try:
        with plist_path.open("rb") as fh:
            data = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError) as exc:
        return DomainProbe(
            provider_id=_DROPBOX_PROVIDER_ID,
            domain_id=None,
            connected=None,
            disconnected=None,
            disconnection_reason=None,
            domain_path=None,
            supports_syncing_trash=None,
            error=repr(exc),
        )
    if not isinstance(data, dict):
        return DomainProbe(
            provider_id=_DROPBOX_PROVIDER_ID,
            domain_id=None,
            connected=None,
            disconnected=None,
            disconnection_reason=None,
            domain_path=None,
            supports_syncing_trash=None,
            error="Domains.plist root is not a dict",
        )

    domain_id: str | None = None
    entry: dict[str, Any] | None = None
    for key, val in data.items():
        if key == "NSFileProviderDomainDefaultIdentifier":
            continue
        if isinstance(val, dict):
            domain_id = str(key)
            entry = val
            break
    if entry is None:
        raw = data.get("NSFileProviderDomainDefaultIdentifier")
        if isinstance(raw, dict):
            domain_id = "NSFileProviderDomainDefaultIdentifier"
            entry = raw

    if entry is None:
        return DomainProbe(
            provider_id=_DROPBOX_PROVIDER_ID,
            domain_id=None,
            connected=None,
            disconnected=None,
            disconnection_reason=None,
            domain_path=None,
            supports_syncing_trash=None,
            error="no domain entries in Domains.plist",
        )

    connected = entry.get("Connected")
    disconnected = entry.get("Disconnected")
    reason = entry.get("DisconnectionReason")
    path = entry.get("Path")
    trash = entry.get("SupportsSyncingTrash")
    return DomainProbe(
        provider_id=_DROPBOX_PROVIDER_ID,
        domain_id=domain_id,
        connected=bool(connected) if connected is not None else None,
        disconnected=bool(disconnected) if disconnected is not None else None,
        disconnection_reason=str(reason) if reason is not None else None,
        domain_path=str(path) if path is not None else None,
        supports_syncing_trash=bool(trash) if trash is not None else None,
    )


def _top_level_names(root: Path) -> set[str] | str:
    try:
        if not root.is_dir():
            return "not a directory"
        return {p.name for p in root.iterdir() if not p.name.startswith(".")}
    except OSError as exc:
        return repr(exc)


def _name_divergence(store: Path, mount: Path) -> NameDivergence:
    s = _top_level_names(store)
    m = _top_level_names(mount)
    if isinstance(s, str) or isinstance(m, str):
        return NameDivergence(
            error=f"store={s if isinstance(s, str) else 'ok'}; mount={m if isinstance(m, str) else 'ok'}"
        )
    both = s & m
    return NameDivergence(
        store_only=tuple(sorted(s - m)),
        mount_only=tuple(sorted(m - s)),
        both_count=len(both),
    )


def _info_points_at_store(info_path: str | None, store_root: str) -> bool:
    if not info_path:
        return False
    a = info_path.rstrip("/")
    b = store_root.rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _info_is_external_store(info_path: str | None) -> bool:
    if not info_path:
        return False
    return info_path.rstrip("/").startswith("/Volumes/DropboxStorage")


def classify_layout(report: FPStatusReport) -> LayoutKind:
    """Classify Dropbox store/mount layout for health policy."""
    if not report.mount.exists and not report.store.exists:
        return "missing"
    if report.store.exists and not report.mount.exists:
        return "store_only"
    if report.mount.exists and not report.store.exists:
        return "mount_only"
    # both exist
    external_signals = (
        _info_is_external_store(report.dropbox_info_path)
        or (report.domain is not None and report.domain.is_fpfs_placeholder)
        or report.forked_devices
    )
    if external_signals:
        # Preferences on /Volumes/DropboxStorage, FPFS placeholder, or
        # different st_dev → external-drive (or dual-materialization) FP.
        return "external_drive_fp"
    return "unified_volume"


def evaluate_fp_health(report: FPStatusReport) -> FPHealthVerdict:
    """Derive hard problems vs warnings for Steward intents.

    Cloud-propagating retire cares about **mount** presence and dual-
    presence quality. Different ``st_dev`` and residual Domains.plist
    "unlinked" metadata on external-drive FP are **warnings**, not
    automatic hard fails.
    """
    layout = classify_layout(report)
    problems: list[str] = []
    warnings: list[str] = []
    notes: list[str] = [
        "Layout model: external-drive FP (store on external volume + "
        "CloudStorage mount) is supported; forked st_dev alone is not a "
        "failure.",
        "See docs/field-notes-2026-07-28-dropbox-rectification.md.",
    ]

    local_ok = report.store.exists and not report.store.error
    if not local_ok:
        if not report.store.exists:
            problems_local = f"Dropbox store root missing: {report.store_root}" + (
                f" ({report.store.error})" if report.store.error else ""
            )
        else:
            problems_local = f"Store stat error: {report.store.error}"
    else:
        problems_local = None

    # --- cloud-propagating problems ---
    if not report.mount.exists:
        problems.append(
            f"CloudStorage mount missing or unstatable: {report.mount_root}"
            + (f" ({report.mount.error})" if report.mount.error else "")
        )
    if report.mount.error:
        problems.append(f"Mount stat error (FP may be congested): {report.mount.error}")
    if report.sample_store_only and not report.sample_both:
        problems.append(
            "Sample dual-presence is store-only with no mount twins — "
            "store→mount mapping for cloud unlink is unsafe for sampled "
            "paths. Rescan mount or use --allow-store-path-unlink for "
            "local reclaim only."
        )

    # --- warnings (never alone hard-fail cloud if samples dual-present) ---
    if layout == "external_drive_fp":
        notes.append(
            "external_drive_fp: Preferences/info.json materialization on "
            "external volume; Finder uses CloudStorage mount."
        )
    if report.forked_devices and layout == "external_drive_fp":
        warnings.append(
            "Store and mount are on different devices (expected for "
            "external-drive File Provider). Path identity is dual — "
            "verify dual presence per object before bulk cloud retire."
        )
    elif report.forked_devices:
        warnings.append(
            "Store and mount appear on different devices. Treat as dual "
            "materializations until dual-presence is confirmed."
        )

    domain = report.domain
    healthy_dual_roots = report.mount.exists and report.store.exists
    residual_external = (
        layout == "external_drive_fp"
        or _info_is_external_store(report.dropbox_info_path)
        or (domain is not None and domain.is_fpfs_placeholder and healthy_dual_roots)
    )
    if domain is not None and domain.error:
        warnings.append(f"Could not read FP Domains.plist: {domain.error}")
    elif domain is not None and (domain.reports_disconnected or domain.is_fpfs_placeholder):
        if residual_external and healthy_dual_roots:
            warnings.append(
                "Domains.plist reports disconnected/unlinked or "
                f"Path={domain.domain_path!r} "
                f"({domain.disconnection_reason or 'no reason'}). "
                "On external-drive FP this is often residual metadata while "
                "the Dropbox app stays healthy — confirm tray sync status; "
                "do not re-link solely because of this flag."
            )
        else:
            problems.append(
                "Dropbox File Provider domain is disconnected/unlinked"
                + (f" ({domain.disconnection_reason})" if domain.disconnection_reason else "")
                + " without a healthy external-drive layout (mount+store). "
                "Repair Dropbox before cloud-propagating retires."
            )

    if report.dropbox_info_path and not _info_points_at_store(report.dropbox_info_path, report.store_root):
        warnings.append(
            f"~/.dropbox/info.json path ({report.dropbox_info_path}) "
            f"differs from probe store root ({report.store_root})."
        )

    nd = report.name_divergence
    if nd is not None and nd.error is None and (nd.store_only or nd.mount_only):
        warnings.append(
            "Top-level store/mount basenames diverge "
            f"(store_only={len(nd.store_only)}, "
            f"mount_only={len(nd.mount_only)}, both={nd.both_count}). "
            "Often '(Selective Sync Conflict)' renames — store-relative "
            "paths under those names may not map to live mount objects. "
            "Scope cloud retires to dual-present paths only."
        )
    if nd is not None and nd.error:
        warnings.append(f"Name divergence probe failed: {nd.error}")

    if problems_local and layout in ("store_only", "missing"):
        # surface store issues for incomplete layouts in problems list too
        # when cloud can't proceed either
        pass

    cloud_ok = len(problems) == 0 and report.mount.exists
    local_reclaim_ready = report.store.exists and not report.store.error

    # Attach store-missing as problem for local intent callers separately;
    # still list it when store is gone so status is honest.
    if not local_reclaim_ready and problems_local:
        if layout != "mount_only":
            # Don't duplicate into cloud problems unless relevant
            warnings.append(problems_local)

    return FPHealthVerdict(
        layout=layout,
        cloud_retire_ready=cloud_ok,
        local_reclaim_ready=local_reclaim_ready,
        problems=tuple(problems),
        warnings=tuple(warnings),
        notes=tuple(notes),
    )


def collect_fp_status(
    *,
    home: Path | None = None,
    store_root: Path | None = None,
    mount_root: Path | None = None,
    sample_rels: tuple[str, ...] = _DEFAULT_SAMPLE_RELS,
    probe_domain: bool = True,
    probe_name_divergence: bool = True,
) -> FPStatusReport:
    """Probe Dropbox store + mount without heavy FP dumps."""
    home = home or Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    mount = mount_root or (home / "Library/CloudStorage/Dropbox")
    store = store_root or Path(_DEFAULT_STORE)

    m_probe = _probe(mount)
    s_probe = _probe(store)

    forked = False
    if m_probe.exists and s_probe.exists:
        forked = m_probe.st_dev != s_probe.st_dev
    elif m_probe.exists != s_probe.exists:
        forked = True

    samples = [_sample_pair(store_root=store, mount_root=mount, relative=rel) for rel in sample_rels]
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

    domain = _probe_dropbox_domain(home) if probe_domain else None
    info_path = _read_dropbox_info_path(home)
    divergence = _name_divergence(store, mount) if probe_name_divergence else None

    report = FPStatusReport(
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
        recommendations=[],
        notes=[],
        domain=domain,
        dropbox_info_path=info_path,
        name_divergence=divergence,
        verdict=None,
    )
    verdict = evaluate_fp_health(report)
    # Human recs from verdict for CLI compatibility
    recs: list[str] = []
    if verdict.cloud_retire_ready:
        recs.append(
            "Cloud-propagating retire: layout OK for mount-path "
            "retire_direct (still verify dual presence per object; "
            "use --require-fp-healthy on apply)."
        )
    else:
        recs.extend(verdict.problems)
    if verdict.local_reclaim_ready:
        recs.append("Local free space on external volume: apply --allow-store-path-unlink (no cloud trash guarantee).")
    if verdict.warnings:
        recs.append(
            "Review warnings above (fork/residual domain/name split); "
            "they do not block --require-fp-healthy by themselves."
        )

    return FPStatusReport(
        mount_root=report.mount_root,
        store_root=report.store_root,
        mount=report.mount,
        store=report.store,
        forked_devices=report.forked_devices,
        dual_samples=report.dual_samples,
        sample_both=report.sample_both,
        sample_store_only=report.sample_store_only,
        sample_mount_only=report.sample_mount_only,
        sample_neither=report.sample_neither,
        recommendations=recs,
        notes=list(verdict.notes),
        domain=report.domain,
        dropbox_info_path=report.dropbox_info_path,
        name_divergence=report.name_divergence,
        verdict=verdict,
    )


def fp_status_to_dict(report: FPStatusReport) -> dict[str, Any]:
    return asdict(report)


__all__ = [
    "DomainProbe",
    "DualSample",
    "FPHealthVerdict",
    "FPStatusReport",
    "LayoutKind",
    "NameDivergence",
    "PathProbe",
    "classify_layout",
    "collect_fp_status",
    "evaluate_fp_health",
    "fp_status_to_dict",
]
