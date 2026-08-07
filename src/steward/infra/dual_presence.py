# SPDX-License-Identifier: Apache-2.0

"""FS probes + plan dual-presence filters (ADR-0020).

Pre-apply plan hygiene only — never mutates inventory claims or tier trees.
Filter artefacts land under operator ``--out-dir`` / data-dir plans sidecars.
Apply path remains ``resolve_fp_paths`` + verify==unlink (ADR-0015).
"""

from __future__ import annotations

import csv
import json
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from steward.core.dual_presence import (
    ALL_PRESENCE_KINDS,
    CLOUD_SAFE_KINDS,
    LOCAL_RECLAIM_KINDS,
    DualPresenceIntent,
    PresenceKind,
    classify_presence_kind,
    cloud_safe_ratio,
    is_conflict_relative,
    map_claim_to_pair,
)
from steward.core.fp_paths import dropbox_mount_root
from steward.infra.observability.swallowed import log_swallowed_error

DEFAULT_STORE_ROOT = Path("/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox")
DEFAULT_SAMPLE_LIMIT = 32
DEFAULT_HEALTH_SAMPLE_LIMIT = 32
DEFAULT_DUAL_RATIO_THRESHOLD = 0.5

_FIXED_SAMPLE_RELS: tuple[str, ...] = (
    "logo.jpg",
    "Books.xlsx",
    "Home",
    "Claims",
    "cerid-archive",
)


@dataclass(frozen=True, slots=True)
class PresenceProbe:
    """One store/mount pair probe result."""

    kind: PresenceKind
    relative: str | None
    store_path: str | None
    mount_path: str | None
    store_exists: bool | None
    mount_exists: bool | None
    store_size: int | None = None
    mount_size: int | None = None
    store_error: str | None = None
    mount_error: str | None = None
    latency_ms: float | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DualPresenceStats:
    """Bucket counts for a sample or plan filter (no per-path lists)."""

    counted: int
    dual: int = 0
    store_only: int = 0
    mount_only: int = 0
    missing_store: int = 0
    conflict_name_path: int = 0
    outside_store_root: int = 0
    mount_error: int = 0
    unknown: int = 0
    dual_bytes: int | None = None
    store_only_bytes: int | None = None
    sample_limit: int | None = None
    truncated: bool = False
    store_root: str = ""
    mount_root: str = ""
    intent: DualPresenceIntent = "observe"
    elapsed_s: float | None = None

    def cloud_safe_sample_ratio(self) -> float | None:
        return cloud_safe_ratio(dual=self.dual, store_only=self.store_only)

    def count_for(self, kind: PresenceKind) -> int:
        return int(getattr(self, kind, 0))


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Plan rows bucketed by :class:`PresenceKind` (no inventory mutation)."""

    buckets: dict[PresenceKind, list[dict[str, str]]]
    stats: DualPresenceStats
    fieldnames: tuple[str, ...]
    comments: tuple[str, ...] = ()
    path_col: str = "source_path"
    input_plan: str | None = None


@dataclass(frozen=True, slots=True)
class FilterArtifacts:
    """Written plan-*.tsv paths + filter-stats.json under out_dir."""

    out_dir: str
    stats_path: str
    bucket_paths: dict[str, str] = field(default_factory=dict)
    stats: DualPresenceStats | None = None


def default_mount_root() -> Path:
    return Path(dropbox_mount_root().rstrip("/"))


def default_store_root() -> Path:
    return DEFAULT_STORE_ROOT


def _path_exists(path: Path) -> tuple[bool | None, str | None, int | None]:
    """Return (exists, error, size_if_file)."""
    try:
        exists = path.exists()
        size: int | None = None
        if exists:
            try:
                if path.is_file():
                    size = path.stat().st_size
                elif path.is_dir():
                    size = -1
            except OSError as exc:
                return exists, repr(exc), None
        return exists, None, size
    except OSError as exc:
        return None, repr(exc), None


def probe_pair(
    store_path: Path | str | None,
    mount_path: Path | str | None,
    *,
    relative: str | None = None,
    timeout_s: float | None = None,  # reserved; sequential exists only in v1
) -> PresenceProbe:
    """Cheap exists/stat on store + mount; classify PresenceKind.

    ``timeout_s`` is accepted for API stability; v1 uses synchronous
    ``Path.exists`` (mount congestion surfaces as OSError → mount_error).
    """
    del timeout_s  # unused in v1
    t0 = time.perf_counter()
    sp = Path(store_path) if store_path else None
    mp = Path(mount_path) if mount_path else None

    store_exists: bool | None = None
    mount_exists: bool | None = None
    store_size: int | None = None
    mount_size: int | None = None
    store_err: str | None = None
    mount_err: str | None = None
    store_error_flag = False
    mount_error_flag = False
    outside = sp is None and mp is None

    if relative is not None and is_conflict_relative(relative):
        latency = (time.perf_counter() - t0) * 1000.0
        return PresenceProbe(
            kind="conflict_name_path",
            relative=relative,
            store_path=str(sp) if sp else None,
            mount_path=str(mp) if mp else None,
            store_exists=None,
            mount_exists=None,
            latency_ms=latency,
        )

    if sp is not None:
        se, serr, ssize = _path_exists(sp)
        store_exists = se
        store_err = serr
        store_size = ssize
        if serr is not None and se is not True:
            store_error_flag = True
    else:
        outside = True

    if mp is not None:
        me, merr, msize = _path_exists(mp)
        mount_exists = me
        mount_err = merr
        mount_size = msize
        if merr is not None and me is not True:
            mount_error_flag = True
    elif not outside:
        # Mapped store but no mount path → treat mount as missing
        mount_exists = False

    kind = classify_presence_kind(
        store_exists=store_exists,
        mount_exists=mount_exists,
        relative=relative,
        store_error=store_error_flag,
        mount_error=mount_error_flag,
        outside_store_root=outside and sp is None,
    )
    latency = (time.perf_counter() - t0) * 1000.0
    return PresenceProbe(
        kind=kind,
        relative=relative,
        store_path=str(sp) if sp else None,
        mount_path=str(mp) if mp else None,
        store_exists=store_exists,
        mount_exists=mount_exists,
        store_size=store_size,
        mount_size=mount_size,
        store_error=store_err,
        mount_error=mount_err,
        latency_ms=latency,
    )


def _empty_buckets() -> dict[PresenceKind, list[dict[str, str]]]:
    return {k: [] for k in ALL_PRESENCE_KINDS}


def _stats_from_counts(
    counts: Mapping[str, int],
    *,
    counted: int,
    store_root: str,
    mount_root: str,
    intent: DualPresenceIntent,
    sample_limit: int | None,
    truncated: bool,
    dual_bytes: int | None = None,
    store_only_bytes: int | None = None,
    elapsed_s: float | None = None,
) -> DualPresenceStats:
    return DualPresenceStats(
        counted=counted,
        dual=int(counts.get("dual", 0)),
        store_only=int(counts.get("store_only", 0)),
        mount_only=int(counts.get("mount_only", 0)),
        missing_store=int(counts.get("missing_store", 0)),
        conflict_name_path=int(counts.get("conflict_name_path", 0)),
        outside_store_root=int(counts.get("outside_store_root", 0)),
        mount_error=int(counts.get("mount_error", 0)),
        unknown=int(counts.get("unknown", 0)),
        dual_bytes=dual_bytes,
        store_only_bytes=store_only_bytes,
        sample_limit=sample_limit,
        truncated=truncated,
        store_root=store_root,
        mount_root=mount_root,
        intent=intent,
        elapsed_s=elapsed_s,
    )


def collect_dual_presence_stats(
    paths: Sequence[str],
    *,
    store_root: Path | str | None = None,
    mount_root: Path | str | None = None,
    intent: DualPresenceIntent = "observe",
    limit: int = 0,
) -> DualPresenceStats:
    """Probe claim/store paths and return aggregate DualPresenceStats."""
    sroot = Path(store_root) if store_root is not None else default_store_root()
    mroot = Path(mount_root) if mount_root is not None else default_mount_root()
    sroot_s = str(sroot)
    mroot_s = str(mroot)
    t0 = time.time()
    counts: dict[str, int] = {k: 0 for k in ALL_PRESENCE_KINDS}
    dual_bytes = 0
    store_only_bytes = 0
    saw_dual_size = False
    saw_store_only_size = False
    n = 0
    truncated = False
    for raw in paths:
        if limit and n >= limit:
            truncated = True
            break
        n += 1
        mapped = map_claim_to_pair(
            raw,
            store_root=sroot_s,
            mount_root=mroot_s,
        )
        if mapped.kind == "outside_store_root":
            counts["outside_store_root"] += 1
            continue
        if mapped.kind == "conflict_name_path":
            counts["conflict_name_path"] += 1
            continue
        probe = probe_pair(
            mapped.store_path,
            mapped.mount_path,
            relative=mapped.relative,
        )
        counts[probe.kind] = counts.get(probe.kind, 0) + 1
        if probe.kind == "dual" and probe.store_size is not None and probe.store_size >= 0:
            dual_bytes += probe.store_size
            saw_dual_size = True
        if (
            probe.kind == "store_only"
            and probe.store_size is not None
            and probe.store_size >= 0
        ):
            store_only_bytes += probe.store_size
            saw_store_only_size = True
    return _stats_from_counts(
        counts,
        counted=n,
        store_root=sroot_s,
        mount_root=mroot_s,
        intent=intent,
        sample_limit=limit or None,
        truncated=truncated,
        dual_bytes=dual_bytes if saw_dual_size else None,
        store_only_bytes=store_only_bytes if saw_store_only_size else None,
        elapsed_s=round(time.time() - t0, 3),
    )


def sample_claim_paths(
    con: sqlite3.Connection,
    *,
    tier: str = "DropboxStorage",
    limit: int = DEFAULT_SAMPLE_LIMIT,
) -> list[str]:
    """Bounded current-claim paths for dual-presence sample (no full census)."""
    lim = max(0, int(limit))
    if lim == 0:
        return []
    try:
        rows = con.execute(
            """
            SELECT file_path FROM claims
            WHERE is_current = 1 AND tier = ?
            ORDER BY id
            LIMIT ?
            """,
            (tier, lim),
        ).fetchall()
    except sqlite3.Error as exc:
        log_swallowed_error(
            "dual_presence.sample_claim_paths",
            exc,
            context={"tier": tier, "limit": lim},
        )
        return []
    out: list[str] = []
    for row in rows:
        p = row[0] if not isinstance(row, sqlite3.Row) else row["file_path"]
        if p:
            out.append(str(p))
    return out


def collect_stats_from_fixed_rels(
    *,
    store_root: Path | str | None = None,
    mount_root: Path | str | None = None,
    rels: Sequence[str] | None = None,
    intent: DualPresenceIntent = "observe",
) -> DualPresenceStats:
    """Probe fixed relatives under store/mount (fp_status-style, no DB)."""
    sroot = Path(store_root) if store_root is not None else default_store_root()
    mroot = Path(mount_root) if mount_root is not None else default_mount_root()
    use_rels = list(rels) if rels is not None else list(_FIXED_SAMPLE_RELS)
    paths = [str(sroot / r) for r in use_rels]
    return collect_dual_presence_stats(
        paths,
        store_root=sroot,
        mount_root=mroot,
        intent=intent,
    )


def load_plan_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Load steward plan TSV: leading ``#`` comments + DictReader body."""
    comments: list[str] = []
    body: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("#"):
                    comments.append(line.rstrip("\n"))
                else:
                    body.append(line.rstrip("\n"))
    except OSError as exc:
        log_swallowed_error(
            "dual_presence.load_plan_tsv",
            exc,
            context={"path": str(path)},
        )
        raise
    if not body:
        return comments, []
    reader = csv.DictReader(body, delimiter="\t")
    rows = [dict(r) for r in reader]
    return comments, rows


def filter_plan_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    path_col: str = "source_path",
    store_root: Path | str | None = None,
    mount_root: Path | str | None = None,
    limit: int = 0,
    intent: DualPresenceIntent = "observe",
    progress: Callable[[int, PresenceKind], None] | None = None,
    comments: Sequence[str] = (),
    input_plan: str | None = None,
) -> FilterResult:
    """Bucket plan TSV rows by PresenceKind. Does not mutate inventory.db."""
    sroot = Path(store_root) if store_root is not None else default_store_root()
    mroot = Path(mount_root) if mount_root is not None else default_mount_root()
    try:
        sroot_s = str(sroot.expanduser().resolve())
    except OSError:
        sroot_s = str(sroot.expanduser())
    try:
        mroot_s = str(mroot.expanduser().resolve())
    except OSError:
        mroot_s = str(mroot.expanduser())

    buckets = _empty_buckets()
    counts: dict[str, int] = {k: 0 for k in ALL_PRESENCE_KINDS}
    fieldnames: list[str] = []
    if rows:
        fieldnames = list(rows[0].keys())
    t0 = time.time()
    n = 0
    truncated = False
    for row in rows:
        if limit and n >= limit:
            truncated = True
            break
        n += 1
        raw = (row.get(path_col) or "").strip()
        mapped = map_claim_to_pair(raw, store_root=sroot_s, mount_root=mroot_s)
        if mapped.kind == "outside_store_root":
            kind: PresenceKind = "outside_store_root"
        elif mapped.kind == "conflict_name_path":
            kind = "conflict_name_path"
        else:
            probe = probe_pair(
                mapped.store_path,
                mapped.mount_path,
                relative=mapped.relative,
            )
            kind = probe.kind
        row_dict = dict(row)
        buckets[kind].append(row_dict)
        counts[kind] = counts.get(kind, 0) + 1
        if progress is not None:
            progress(n, kind)

    stats = _stats_from_counts(
        counts,
        counted=n,
        store_root=sroot_s,
        mount_root=mroot_s,
        intent=intent,
        sample_limit=limit or None,
        truncated=truncated,
        elapsed_s=round(time.time() - t0, 3),
    )
    return FilterResult(
        buckets=buckets,
        stats=stats,
        fieldnames=tuple(fieldnames),
        comments=tuple(comments),
        path_col=path_col,
        input_plan=input_plan,
    )


def filter_plan_file(
    plan_path: Path,
    *,
    store_root: Path | str | None = None,
    mount_root: Path | str | None = None,
    limit: int = 0,
    path_col: str = "source_path",
    intent: DualPresenceIntent = "observe",
) -> FilterResult:
    """Load a plan TSV and filter rows by dual-presence."""
    comments, rows = load_plan_tsv(plan_path)
    return filter_plan_rows(
        rows,
        path_col=path_col,
        store_root=store_root,
        mount_root=mount_root,
        limit=limit,
        intent=intent,
        comments=comments,
        input_plan=str(plan_path),
    )


def _write_bucket_tsv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    comments: Sequence[str],
    note: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        for c in comments:
            fh.write(c if c.endswith("\n") else c + "\n")
        fh.write(f"# filter-plan-dual-presence: {note}\n")
        writer = csv.DictWriter(
            fh,
            fieldnames=list(fieldnames),
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))


def dual_presence_stats_to_dict(stats: DualPresenceStats) -> dict[str, Any]:
    """JSON-stable stats dict (filter-stats.json + MCP/CLI)."""
    d = asdict(stats)
    d["cloud_safe_sample_ratio"] = stats.cloud_safe_sample_ratio()
    d["cloud_safe_kinds"] = sorted(CLOUD_SAFE_KINDS)
    d["local_reclaim_kinds"] = sorted(LOCAL_RECLAIM_KINDS)
    return d


def write_filtered_plans(
    result: FilterResult,
    *,
    out_dir: Path,
    comments: Sequence[str] | None = None,
    fieldnames: Sequence[str] | None = None,
    write_empty: bool = False,
) -> FilterArtifacts:
    """Write plan-<kind>.tsv buckets + filter-stats.json under out_dir.

    Only writes plan artefacts (data-dir / operator --out). Never opens
    inventory.db for UPDATE.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    use_comments = list(comments) if comments is not None else list(result.comments)
    use_fields = list(fieldnames) if fieldnames is not None else list(result.fieldnames)
    if not use_fields:
        use_fields = [result.path_col]

    bucket_paths: dict[str, str] = {}
    for kind, rows in result.buckets.items():
        if not rows and not write_empty:
            continue
        dest = out / f"plan-{kind}.tsv"
        _write_bucket_tsv(
            dest,
            use_fields,
            rows,
            use_comments,
            f"bucket={kind} n={len(rows)}",
        )
        bucket_paths[kind] = str(dest)

    stats_payload = dual_presence_stats_to_dict(result.stats)
    stats_payload.update(
        {
            "input_rows_seen": result.stats.counted,
            "input_plan": result.input_plan,
            "path_col": result.path_col,
            "limit": result.stats.sample_limit or 0,
            "buckets_written": sorted(bucket_paths.keys()),
        }
    )
    stats_path = out / "filter-stats.json"
    stats_path.write_text(json.dumps(stats_payload, indent=2) + "\n", encoding="utf-8")
    return FilterArtifacts(
        out_dir=str(out),
        stats_path=str(stats_path),
        bucket_paths=bucket_paths,
        stats=result.stats,
    )


def attach_filter_to_plan(
    plan_id: str,
    artifacts: FilterArtifacts,
    *,
    data_dir: Path | None = None,
) -> Path | None:
    """Copy filter-stats.json (+ plan-dual.tsv if present) into plan backlog dir.

    Clears ADR-0019 ``dual_presence_unfiltered`` on next refresh when
    ``filter-stats.json`` is present beside the plan.
    """
    try:
        from steward.infra.plans.registry import (
            BY_ID_DIRNAME,
            FILTER_STATS_FILENAME,
            SUMMARY_FILENAME,
            plans_dir,
            refresh_plan_status,
            show_plan,
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("dual_presence.attach_filter_import", exc, context={})
        return None

    from steward.infra.db.settings import data_dir as _default_data_dir

    ddir = Path(data_dir) if data_dir is not None else _default_data_dir()
    dest_dir = plans_dir(ddir) / BY_ID_DIRNAME / plan_id
    if not dest_dir.is_dir():
        return None
    src_stats = Path(artifacts.stats_path)
    dest_stats = dest_dir / FILTER_STATS_FILENAME
    try:
        dest_stats.write_bytes(src_stats.read_bytes())
    except OSError as exc:
        log_swallowed_error(
            "dual_presence.attach_filter_stats",
            exc,
            context={"plan_id": plan_id, "src": str(src_stats)},
        )
        return None

    dual_src = artifacts.bucket_paths.get("dual")
    if dual_src:
        try:
            dest_dual = dest_dir / "plan-dual.tsv"
            dest_dual.write_bytes(Path(dual_src).read_bytes())
        except OSError as exc:
            log_swallowed_error(
                "dual_presence.attach_filter_dual_tsv",
                exc,
                context={"plan_id": plan_id},
            )

    # Best-effort status refresh so blocked_reasons drop dual_presence_unfiltered.
    if show_plan(plan_id, data_dir=data_dir) is not None:
        try:
            refresh_plan_status(plan_id, data_dir=data_dir)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "dual_presence.attach_filter_refresh",
                exc,
                context={"plan_id": plan_id},
            )
    # Touch summary path existence for callers
    _ = dest_dir / SUMMARY_FILENAME
    return dest_stats




def sample_from_inventory(
    db_path: Path | str,
    *,
    store_root: Path | str | None = None,
    mount_root: Path | str | None = None,
    limit: int = DEFAULT_SAMPLE_LIMIT,
    tier: str = "DropboxStorage",
    intent: DualPresenceIntent = "observe",
) -> DualPresenceStats:
    """Open inventory read-only, sample claim paths, return DualPresenceStats."""
    from steward.infra.db.connect import connect

    target = Path(db_path).expanduser()
    con = connect(target, read_only=True, load_vec=False)
    try:
        paths = sample_claim_paths(con, tier=tier, limit=limit)
    finally:
        con.close()
    if not paths:
        return collect_stats_from_fixed_rels(
            store_root=store_root,
            mount_root=mount_root,
            intent=intent,
        )
    return collect_dual_presence_stats(
        paths,
        store_root=store_root,
        mount_root=mount_root,
        intent=intent,
        limit=limit,
    )


def ready_for_cloud_filter(stats: DualPresenceStats, *, mount_present: bool) -> bool:
    """True when mount is present and sample is not purely store-only."""
    if not mount_present:
        return False
    if stats.counted == 0:
        return False
    if stats.dual > 0:
        return True
    # Mount present but no dual among probed — not ready for bulk cloud.
    return False


__all__ = [
    "DEFAULT_DUAL_RATIO_THRESHOLD",
    "DEFAULT_HEALTH_SAMPLE_LIMIT",
    "DEFAULT_SAMPLE_LIMIT",
    "DEFAULT_STORE_ROOT",
    "DualPresenceStats",
    "FilterArtifacts",
    "FilterResult",
    "PresenceProbe",
    "attach_filter_to_plan",
    "collect_dual_presence_stats",
    "collect_stats_from_fixed_rels",
    "default_mount_root",
    "default_store_root",
    "dual_presence_stats_to_dict",
    "filter_plan_file",
    "filter_plan_rows",
    "load_plan_tsv",
    "probe_pair",
    "ready_for_cloud_filter",
    "sample_claim_paths",
    "sample_from_inventory",
    "write_filtered_plans",
]
