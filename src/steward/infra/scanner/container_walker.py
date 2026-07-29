# SPDX-License-Identifier: Apache-2.0

"""Container walker — extracts member-level claims from container files.

When ``steward scan --include-containers`` is set, the main walker calls
this module for each file whose extension matches a supported container
type. The walker streams each member, computes Steward's hash ladder
(xxh3-128 fast / blake3 archive), upserts a permanode keyed on member
content, and inserts a claim with ``container_path`` and
``container_sha256`` populated.

Format coverage:

* ZIP and tar (including .tar.gz / .tar.bz2 / .tar.xz) — handled
  natively by Python's stdlib (v0.1.x).
* Disk images (.dmg / .sparseimage / .iso) — mounted read-only via
  ``hdiutil attach`` and walked, then detached (v0.2; macOS only).
* 7z / RAR — extracted into a temporary directory via ``unar``, walked,
  then the directory is removed (v0.2; requires The Unarchiver).

When the required external tool isn't on ``PATH`` (e.g. Linux CI runners
have neither ``hdiutil`` nor ``unar``), the container is counted in
``containers_skipped`` and the surrounding scan continues. The behaviour
is identical to v0.1.x for those archives.

Errors per member are counted in :class:`ContainerStats` and surfaced via
``log_swallowed_error`` (the policy: a single bad member must not abort
the scan).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import IO

from steward.core.hashing import HashResult
from steward.core.tiers import classify_tier
from steward.infra.db import repo_claims, repo_permanodes
from steward.infra.observability import log_swallowed_error
from steward.infra.scanner.skiplist import (
    filter_dirs,
    filter_files,
    is_skipped_file,
)

logger = logging.getLogger("steward.infra.scanner.container_walker")


# ZIP-like containers.
ZIP_EXTS: frozenset[str] = frozenset({".zip"})

# Tar-like containers — including the multi-extension forms.
TAR_EXTS: frozenset[str] = frozenset(
    {
        ".tar",
        ".tgz",
        ".tar.gz",
        ".tbz2",
        ".tar.bz2",
        ".txz",
        ".tar.xz",
    }
)

# Disk images — mounted via ``hdiutil`` on macOS (v0.2). Linux falls
# through to ``containers_skipped`` because the tool isn't available.
DISK_IMAGE_EXTS: frozenset[str] = frozenset(
    {
        ".dmg",
        ".sparseimage",
        ".sparsebundle",
        ".iso",
        ".img",
        ".cdr",
    }
)

# 7z / RAR — extracted via ``unar`` (The Unarchiver) on macOS (v0.2).
# Linux falls through to ``containers_skipped`` likewise.
UNAR_EXTS: frozenset[str] = frozenset({".7z", ".rar"})

# Union of formats that need an external CLI tool. Kept as an alias so
# the original v0.1.x symbol remains importable for downstream code.
EXTERNAL_TOOL_EXTS: frozenset[str] = DISK_IMAGE_EXTS | UNAR_EXTS

# Subprocess timeouts (seconds) — guard against a hung mount or extract
# wedging the entire scan. Large archives finish faster than this in
# practice; the cap is for the pathological hang.
_HDIUTIL_TIMEOUT_S: int = 600
_UNAR_TIMEOUT_S: int = 1800

# Internal container paths Steward never inventories (created by the
# archiving tool, not the user). Same shape as the filesystem skiplist.
INTERNAL_SKIP_PREFIXES: tuple[str, ...] = ("__MACOSX/",)

CHUNK = 8 * 1024 * 1024


@dataclass
class ContainerStats:
    containers_walked: int = 0
    containers_skipped: int = 0  # unsupported / requires external tool
    containers_errored: int = 0
    members_walked: int = 0
    members_errored: int = 0
    bytes_hashed: int = 0
    permanodes_touched: set[str] = field(default_factory=set)


def _container_ext(path: str) -> str:
    """Return the longest matching extension, lowercased. Handles
    multi-part extensions like ``.tar.gz``.
    """
    p = path.lower()
    for e in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if p.endswith(e):
            return e
    _, e = os.path.splitext(p)
    return e


def is_container_path(path: str) -> bool:
    """Return True iff this path is a recognised container type.

    Includes types Steward can walk natively (zip / tar*) AND types that
    require external tools (hdiutil / unar). The walker uses this for
    detection; the per-format dispatcher decides whether to skip.
    """
    e = _container_ext(path)
    return e in ZIP_EXTS or e in TAR_EXTS or e in EXTERNAL_TOOL_EXTS


def _has_tool(name: str) -> bool:
    """Return True iff ``name`` resolves on ``PATH``. Wrapped so tests
    can patch :func:`shutil.which` to simulate the Linux CI environment.
    """
    return shutil.which(name) is not None


def _container_sha256(path: str) -> str | None:
    """Stream-hash the container file with sha256 (schema requires it for
    the ``container_sha256`` column). Returns None on read errors —
    container has already been hashed by the main walker for its own
    permanode, so a None here just means we skip member-level recording.
    """
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                buf = f.read(CHUNK)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()
    except OSError as exc:
        log_swallowed_error("scanner.container_walker.sha256", exc, context={"path": path})
        return None


def _is_internal_skip(member_path: str) -> bool:
    """Return True iff a container-internal path matches the noise set."""
    if any(member_path.startswith(p) for p in INTERNAL_SKIP_PREFIXES):
        return True
    # Use the same basename-level skip rules as the filesystem walker
    # (._foo, .DS_Store, etc.).
    base = os.path.basename(member_path)
    return is_skipped_file(base)


def _hash_member_stream(fh: IO[bytes]) -> HashResult:
    """Hash a streaming file-like object with the ladder's fast algo (xxh3-128).

    The ladder typically reads from a path; for in-archive members we have
    only a file-like object. We replicate ``HashLadder.fast`` semantics
    inline so we don't have to write the member to a temp file.
    """
    import xxhash

    h = xxhash.xxh3_128()
    total = 0
    while True:
        buf = fh.read(CHUNK)
        if not buf:
            break
        h.update(buf)
        total += len(buf)
    return HashResult(algo="xxh3_128", hex=h.hexdigest(), size_bytes=total)


def _record_member(
    con: sqlite3.Connection,
    *,
    container_path: str,
    container_sha256: str,
    member_path: str,
    member_hash: HashResult,
    machine_id: str,
    scan_run_id: int,
    now: str,
) -> str:
    """Upsert a permanode for the member and insert its claim. Returns the
    permanode_id."""
    tier, volume = classify_tier(container_path)
    pid = repo_permanodes.upsert(
        con,
        canonical_hash=member_hash.hex,
        size_bytes=member_hash.size_bytes,
        algo=member_hash.algo,
    )
    con.execute(
        """
        INSERT OR IGNORE INTO hashes (permanode_id, algo, hex, computed_at)
        VALUES (?, ?, ?, ?)
        """,
        (pid, member_hash.algo, member_hash.hex, now),
    )
    repo_claims.insert(
        con,
        permanode_id=pid,
        machine_id=machine_id,
        file_path=member_path,
        tier=tier,
        volume=volume,
        size_bytes=member_hash.size_bytes,
        scan_run_id=scan_run_id,
        container_path=container_path,
        container_sha256=container_sha256,
    )
    return pid


def _walk_zip(
    con: sqlite3.Connection,
    *,
    container_path: str,
    container_sha256: str,
    machine_id: str,
    scan_run_id: int,
    stats: ContainerStats,
    now: str,
) -> None:
    """Walk each non-directory member of a ZIP, record permanodes + claims."""
    try:
        zf = zipfile.ZipFile(container_path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        stats.containers_errored += 1
        log_swallowed_error(
            "scanner.container_walker.zip.open",
            exc,
            context={"path": container_path},
        )
        return
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if _is_internal_skip(info.filename):
                continue
            try:
                with zf.open(info, "r") as member_fh:
                    member_hash = _hash_member_stream(member_fh)
            except (zipfile.BadZipFile, OSError) as exc:
                stats.members_errored += 1
                log_swallowed_error(
                    "scanner.container_walker.zip.member",
                    exc,
                    context={
                        "path": container_path,
                        "internal": info.filename,
                    },
                )
                continue
            pid = _record_member(
                con,
                container_path=container_path,
                container_sha256=container_sha256,
                member_path=info.filename,
                member_hash=member_hash,
                machine_id=machine_id,
                scan_run_id=scan_run_id,
                now=now,
            )
            stats.members_walked += 1
            stats.bytes_hashed += member_hash.size_bytes
            stats.permanodes_touched.add(pid)


def _walk_tar(
    con: sqlite3.Connection,
    *,
    container_path: str,
    container_sha256: str,
    machine_id: str,
    scan_run_id: int,
    stats: ContainerStats,
    now: str,
) -> None:
    """Walk each regular-file member of a tar (any compression), record
    permanodes + claims."""
    try:
        tf = tarfile.open(container_path, "r:*")
    except (tarfile.TarError, OSError) as exc:
        stats.containers_errored += 1
        log_swallowed_error(
            "scanner.container_walker.tar.open",
            exc,
            context={"path": container_path},
        )
        return
    with tf:
        for member in tf:
            if not member.isfile():
                continue
            if _is_internal_skip(member.name):
                continue
            try:
                tar_fh = tf.extractfile(member)
                if tar_fh is None:
                    continue
                member_hash = _hash_member_stream(tar_fh)
            except (tarfile.TarError, OSError) as exc:
                stats.members_errored += 1
                log_swallowed_error(
                    "scanner.container_walker.tar.member",
                    exc,
                    context={
                        "path": container_path,
                        "internal": member.name,
                    },
                )
                continue
            pid = _record_member(
                con,
                container_path=container_path,
                container_sha256=container_sha256,
                member_path=member.name,
                member_hash=member_hash,
                machine_id=machine_id,
                scan_run_id=scan_run_id,
                now=now,
            )
            stats.members_walked += 1
            stats.bytes_hashed += member_hash.size_bytes
            stats.permanodes_touched.add(pid)


# ───────────────────────── external-tool handlers ─────────────────────────


def _hash_file_xxh3(path: str) -> HashResult | None:
    """Stream-hash a path with xxh3-128. Returns None on read error."""
    try:
        with open(path, "rb") as fh:
            return _hash_member_stream(fh)
    except OSError as exc:
        log_swallowed_error("scanner.container_walker.member_hash", exc, context={"path": path})
        return None


def _walk_extracted_tree(
    con: sqlite3.Connection,
    *,
    container_path: str,
    container_sha256: str,
    extracted_root: str,
    machine_id: str,
    scan_run_id: int,
    stats: ContainerStats,
    now: str,
) -> None:
    """Walk every regular file under an extracted/mounted tree and record
    it as a member-level claim.

    Used by both the disk-image and the unar handlers. ``extracted_root``
    is the on-disk path we mounted/extracted to; ``container_path`` is
    what gets recorded in the claim so downstream queries can still tie
    members back to the .dmg / .7z they came from.
    """
    for dirpath, dirnames, filenames in os.walk(extracted_root):
        dirnames[:] = filter_dirs(dirnames)
        for fname in filter_files(filenames):
            full = os.path.join(dirpath, fname)
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            member_hash = _hash_file_xxh3(full)
            if member_hash is None:
                stats.members_errored += 1
                continue
            # Store member_path RELATIVE to the extracted root so it's
            # the same after the temp directory is gone. Always
            # forward-slash separators — these are virtual paths.
            rel = os.path.relpath(full, extracted_root).replace(os.sep, "/")
            pid = _record_member(
                con,
                container_path=container_path,
                container_sha256=container_sha256,
                member_path=rel,
                member_hash=member_hash,
                machine_id=machine_id,
                scan_run_id=scan_run_id,
                now=now,
            )
            stats.members_walked += 1
            stats.bytes_hashed += member_hash.size_bytes
            stats.permanodes_touched.add(pid)


@contextmanager
def _mounted_disk_image(container_path: str) -> Iterator[str]:
    """Context manager that mounts ``container_path`` read-only via
    ``hdiutil attach`` and yields the mountpoint string.

    Detach is best-effort on exit. If the mount itself fails, an
    :class:`OSError` is raised so the caller can record the error.
    """
    mountpoint = tempfile.mkdtemp(prefix="steward-dmg-mount-")
    try:
        # -nobrowse: don't show in Finder. -noverify: skip the slow
        # blockwise checksum (we hash the container file separately).
        # -readonly: never write to the source image.
        # -plist not strictly needed since we pass -mountpoint.
        proc = subprocess.run(
            [
                "hdiutil",
                "attach",
                "-nobrowse",
                "-noverify",
                "-readonly",
                "-mountpoint",
                mountpoint,
                container_path,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_HDIUTIL_TIMEOUT_S,
        )
        if proc.returncode != 0:
            raise OSError(
                f"hdiutil attach failed (rc={proc.returncode}): {(proc.stderr or proc.stdout or '').strip()[:300]}"
            )
        yield mountpoint
    finally:
        # Best-effort detach. If the mount never succeeded, mountpoint is
        # an empty dir we can rmtree directly.
        if os.path.ismount(mountpoint):
            try:
                subprocess.run(
                    ["hdiutil", "detach", mountpoint, "-force"],
                    check=False,
                    capture_output=True,
                    timeout=120,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                log_swallowed_error(
                    "scanner.container_walker.dmg.detach",
                    exc,
                    context={"mountpoint": mountpoint},
                )
        try:
            shutil.rmtree(mountpoint, ignore_errors=True)
        except OSError as exc:  # pragma: no cover - rmtree with ignore rarely raises
            log_swallowed_error(
                "scanner.container_walker.dmg.rmtree",
                exc,
                context={"mountpoint": mountpoint},
            )


@contextmanager
def _unar_extracted(container_path: str) -> Iterator[str]:
    """Context manager that extracts ``container_path`` via ``unar`` into
    a temporary directory and yields its path. Cleanup is best-effort.
    """
    workdir = tempfile.mkdtemp(prefix="steward-unar-")
    try:
        proc = subprocess.run(
            [
                "unar",
                "-force-overwrite",
                "-no-recursion",
                "-quiet",
                "-output-directory",
                workdir,
                container_path,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_UNAR_TIMEOUT_S,
        )
        if proc.returncode != 0:
            raise OSError(f"unar failed (rc={proc.returncode}): {(proc.stderr or proc.stdout or '').strip()[:300]}")
        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _walk_disk_image(
    con: sqlite3.Connection,
    *,
    container_path: str,
    container_sha256: str,
    machine_id: str,
    scan_run_id: int,
    stats: ContainerStats,
    now: str,
) -> None:
    """Mount a .dmg / .sparseimage / .iso read-only and walk its contents."""
    try:
        with _mounted_disk_image(container_path) as mountpoint:
            _walk_extracted_tree(
                con,
                container_path=container_path,
                container_sha256=container_sha256,
                extracted_root=mountpoint,
                machine_id=machine_id,
                scan_run_id=scan_run_id,
                stats=stats,
                now=now,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        stats.containers_errored += 1
        stats.containers_walked = max(0, stats.containers_walked - 1)
        log_swallowed_error(
            "scanner.container_walker.dmg.walk",
            exc,
            context={"path": container_path},
        )


def _walk_unar_archive(
    con: sqlite3.Connection,
    *,
    container_path: str,
    container_sha256: str,
    machine_id: str,
    scan_run_id: int,
    stats: ContainerStats,
    now: str,
) -> None:
    """Extract a .7z / .rar via ``unar`` and walk the extracted tree."""
    try:
        with _unar_extracted(container_path) as workdir:
            _walk_extracted_tree(
                con,
                container_path=container_path,
                container_sha256=container_sha256,
                extracted_root=workdir,
                machine_id=machine_id,
                scan_run_id=scan_run_id,
                stats=stats,
                now=now,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        stats.containers_errored += 1
        stats.containers_walked = max(0, stats.containers_walked - 1)
        log_swallowed_error(
            "scanner.container_walker.unar.walk",
            exc,
            context={"path": container_path},
        )


# ───────────────────────── dispatcher ─────────────────────────


def walk_container(
    con: sqlite3.Connection,
    *,
    container_path: str,
    machine_id: str,
    scan_run_id: int,
) -> ContainerStats:
    """Dispatch a container file to the right format handler.

    * .zip / .tar* → walked natively via stdlib.
    * .dmg / .sparseimage / .iso → mounted read-only via ``hdiutil`` and
      walked (macOS only).
    * .7z / .rar → extracted via ``unar`` and walked (macOS only by
      default; works anywhere The Unarchiver is installed).

    When an external tool isn't on ``PATH`` (typically Linux CI),
    the container is counted in ``containers_skipped``; the surrounding
    scan continues. Any I/O or format error is also contained: the
    container is marked errored, member error counters tick up, but
    the scan continues.
    """
    stats = ContainerStats()
    stats.containers_walked = 1
    ext = _container_ext(container_path)

    if ext not in ZIP_EXTS and ext not in TAR_EXTS and ext not in EXTERNAL_TOOL_EXTS:
        # Caller should gate via is_container_path; defensive return.
        stats.containers_walked = 0
        stats.containers_skipped = 1
        return stats

    # External-tool availability check — skip if the required tool isn't
    # installed (e.g. Linux CI). We do this BEFORE hashing the container
    # so a missing tool doesn't cost a sha256 read.
    if ext in DISK_IMAGE_EXTS and not _has_tool("hdiutil"):
        stats.containers_walked = 0
        stats.containers_skipped = 1
        logger.info(
            "container_walker.skip-no-hdiutil",
            extra={"path": container_path, "ext": ext},
        )
        return stats
    if ext in UNAR_EXTS and not _has_tool("unar"):
        stats.containers_walked = 0
        stats.containers_skipped = 1
        logger.info(
            "container_walker.skip-no-unar",
            extra={"path": container_path, "ext": ext},
        )
        return stats

    container_sha = _container_sha256(container_path)
    if container_sha is None:
        stats.containers_walked = 0
        stats.containers_errored = 1
        return stats

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if ext in ZIP_EXTS:
        _walk_zip(
            con,
            container_path=container_path,
            container_sha256=container_sha,
            machine_id=machine_id,
            scan_run_id=scan_run_id,
            stats=stats,
            now=now,
        )
    elif ext in TAR_EXTS:
        _walk_tar(
            con,
            container_path=container_path,
            container_sha256=container_sha,
            machine_id=machine_id,
            scan_run_id=scan_run_id,
            stats=stats,
            now=now,
        )
    elif ext in DISK_IMAGE_EXTS:
        _walk_disk_image(
            con,
            container_path=container_path,
            container_sha256=container_sha,
            machine_id=machine_id,
            scan_run_id=scan_run_id,
            stats=stats,
            now=now,
        )
    else:  # UNAR_EXTS
        _walk_unar_archive(
            con,
            container_path=container_path,
            container_sha256=container_sha,
            machine_id=machine_id,
            scan_run_id=scan_run_id,
            stats=stats,
            now=now,
        )
    return stats


__all__ = [
    "ContainerStats",
    "DISK_IMAGE_EXTS",
    "EXTERNAL_TOOL_EXTS",
    "TAR_EXTS",
    "UNAR_EXTS",
    "ZIP_EXTS",
    "is_container_path",
    "walk_container",
]
