# SPDX-License-Identifier: Apache-2.0

"""Pure :class:`StatusReport` → interactive HTML dashboard.

Self-contained page (inline CSS + JS, no CDN). Soft-polls
``/status.json`` without full-page reloads (includes estate
``posture`` for the banner). Analysis panes and the ops console
talk to ``/api/*`` (including ``/api/health``). Meta-refresh lives
only inside ``<noscript>`` so JS-capable browsers never blank the UI.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

from steward.infra.status import StatusReport, _format_bytes


def _esc(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


def _row(label: str, value: str, *, metric: str = "") -> str:
    m = f' data-metric="{html.escape(metric)}"' if metric else ""
    return f"<tr><th>{html.escape(label)}</th><td{m}>{value}</td></tr>"


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * part / total))


def _scan_bars(*, walked: int, hashed: int, skipped: int, errors: int) -> str:
    total = max(walked, hashed + skipped, 1)
    h, s, e = _pct(hashed, total), _pct(skipped, total), _pct(errors, total)
    used = h + s + e
    other = max(0.0, 100.0 - used) if walked > hashed + skipped else 0.0
    scale = 100.0 / max(h + s + e + other, 1e-9)
    h, s, e = h * scale, s * scale, e * scale
    err_li = (
        f"<li><span class='swatch errors'></span>errors "
        f"<strong class='bad' data-metric='scan.errors'>{errors:,}</strong></li>"
        if errors
        else ""
    )
    return f"""\
<svg class="chart-bars" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true">
  <rect class="bar-track" x="0" y="0" width="100" height="10" rx="2"/>
  <rect class="bar-hashed" data-bar="hashed" x="0" y="0" width="{h:.2f}" height="10" rx="2"/>
  <rect class="bar-skipped" data-bar="skipped" x="{h:.2f}" y="0" width="{s:.2f}" height="10"/>
  <rect class="bar-errors" data-bar="errors" x="{h + s:.2f}" y="0" width="{e:.2f}" height="10"/>
</svg>
<ul class="legend">
  <li><span class="swatch hashed"></span>hashed <strong data-metric="scan.files_hashed">{hashed:,}</strong></li>
  <li><span class="swatch skipped"></span>skipped <strong data-metric="scan.files_skipped">{skipped:,}</strong></li>
  <li><span class="swatch walked"></span>walked <strong data-metric="scan.files_walked">{walked:,}</strong></li>
  {err_li}
</ul>"""


def _success_ring(*, successes: int, failures: int, label: str) -> str:
    total = successes + failures
    rate = (100.0 * successes / total) if total else 0.0
    c = 97.389
    dash = c * rate / 100.0
    tone = "ok" if failures == 0 and total > 0 else ("warn" if failures else "muted")
    return f"""\
<div class="gauge" data-gauge="{html.escape(label)}">
  <svg viewBox="0 0 36 36" class="gauge-svg" role="img" aria-label="{rate:.0f}%">
    <circle class="gauge-track" cx="18" cy="18" r="15.5"/>
    <circle class="gauge-arc gauge-{tone}" cx="18" cy="18" r="15.5"
      stroke-dasharray="{dash:.2f} {c - dash:.2f}" transform="rotate(-90 18 18)"/>
    <text class="gauge-text" x="18" y="19.5" text-anchor="middle">{rate:.0f}%</text>
  </svg>
  <div class="gauge-meta">
    <span class="ok" data-metric="{html.escape(label)}.successes">{successes:,}</span> ok
    · <span class="{"bad" if failures else "dim"}" data-metric="{html.escape(label)}.failures">{failures:,}</span> fail
  </div>
</div>"""


def _audit_ring(*, ok: bool, skipped: bool, rows: int) -> str:
    if skipped:
        tone, label, dash = "warn", "skip", "24 97"
    elif ok:
        tone, label, dash = "ok", "ok", "97.39 0"
    else:
        tone, label, dash = "bad", "!", "97.39 0"
    return f"""\
<div class="gauge audit-gauge">
  <svg viewBox="0 0 36 36" class="gauge-svg" role="img" aria-label="audit {label}">
    <circle class="gauge-track" cx="18" cy="18" r="15.5"/>
    <circle class="gauge-arc gauge-{tone}" cx="18" cy="18" r="15.5"
      stroke-dasharray="{dash}" transform="rotate(-90 18 18)"/>
    <text class="gauge-text gauge-text-sm" x="18" y="19.5" text-anchor="middle">{html.escape(label)}</text>
  </svg>
  <div class="gauge-meta"><span data-metric="audit.rows_checked">{rows:,}</span> rows checked</div>
</div>"""


def _kpi(label: str, value: str, metric: str, *, tone: str = "", i: int = 0) -> str:
    t = f" kpi-{tone}" if tone else ""
    return (
        f'<button type="button" class="kpi{t}" style="--i:{min(i, 8)}" '
        f'data-open-panel="overview" data-metric-card="{html.escape(metric)}">'
        f'<div class="kpi-value" data-metric="{html.escape(metric)}">{value}</div>'
        f'<div class="kpi-label">{html.escape(label)}</div></button>'
    )


def _card(
    title: str,
    rows: list[str],
    *,
    extra: str = "",
    index: int = 0,
    panel: str = "",
    detail_hint: str = "Open analysis",
) -> str:
    extra_html = f'<div class="card-visual">{extra}</div>' if extra else ""
    panel_attr = f' data-panel="{html.escape(panel)}"' if panel else ""
    return f"""\
<section class="card" style="--i:{min(index, 8)}" data-card="{html.escape(title)}"{panel_attr}>
  <div class="card-head">
    <h2>{html.escape(title)}</h2>
    <button type="button" class="card-expand" data-expand="{html.escape(panel or title)}" title="{html.escape(detail_hint)}">
      Details
    </button>
  </div>
  {extra_html}
  <table><tbody>{"".join(rows)}</tbody></table>
</section>"""


def _render_kpis(report: StatusReport) -> str:
    inv = report.inventory
    a = report.audit_chain
    if getattr(a, "skipped", False):
        atone, aval = "warn", "skip"
    elif a.ok:
        atone, aval = "ok", "ok"
    else:
        atone, aval = "bad", "BROKEN"
    stash_tone = "warn" if report.stash.in_flight_entries else ""
    items = [
        _kpi("permanodes", f"{inv.permanodes:,}", "inv.permanodes", i=0),
        _kpi("claims", f"{inv.current_claims:,}", "inv.current_claims", i=1),
        _kpi("scan runs", f"{inv.scan_runs:,}", "inv.scan_runs", i=2),
        _kpi("audit log", f"{inv.audit_entries:,}", "inv.audit_entries", i=3),
        _kpi("machines", f"{inv.machines:,}", "inv.machines", i=4),
        _kpi("stash", f"{report.stash.in_flight_entries:,}", "stash.in_flight", tone=stash_tone, i=5),
        _kpi("audit chain", aval, "audit.status", tone=atone, i=6),
    ]
    return f'<div class="kpi-strip" aria-label="Key metrics">{"".join(items)}</div>'


def _render_inventory(report: StatusReport) -> str:
    inv = report.inventory
    parts = [
        ("permanodes", inv.permanodes),
        ("claims", inv.current_claims),
        ("scans", inv.scan_runs),
        ("audit", inv.audit_entries),
    ]
    total = max(sum(p[1] for p in parts), 1)
    x = 0.0
    segs = []
    for name, n in parts:
        w = 100.0 * n / total
        segs.append(f'<rect class="dist-{name}" data-bar="{name}" x="{x:.2f}" y="0" width="{w:.2f}" height="8" rx="1"/>')
        x += w
    dist = f"""\
<svg class="chart-bars dist-bars" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
  <rect class="bar-track" x="0" y="0" width="100" height="8" rx="1"/>{"".join(segs)}
</svg>
<ul class="legend compact">
  <li><span class="swatch hashed"></span>permanodes</li>
  <li><span class="swatch skipped"></span>claims</li>
  <li><span class="swatch walked"></span>scans</li>
  <li><span class="swatch muted"></span>audit</li>
</ul>"""
    rows = [
        _row("permanodes", f"{inv.permanodes:,}", metric="inv.permanodes"),
        _row("current claims", f"{inv.current_claims:,}", metric="inv.current_claims"),
        _row("scan runs", f"{inv.scan_runs:,}", metric="inv.scan_runs"),
        _row("audit entries", f"{inv.audit_entries:,}", metric="inv.audit_entries"),
        _row("machines", f"{inv.machines:,}", metric="inv.machines"),
        _row(
            "db file",
            f"{_esc(report.db.path)} <span class='dim' data-metric='db.size'>({_esc(_format_bytes(report.db.size_bytes))})</span>",
        ),
        _row("db modified", _esc(report.db.modified_iso), metric="db.modified"),
    ]
    return _card("inventory", rows, extra=dist, index=0, panel="stats")


def _render_latest_scan(report: StatusReport) -> str:
    s = report.latest_scan
    if s.scan_run_id is None:
        return _card("latest scan", [_row("status", "<em>no scans yet</em>")], index=1, panel="scans")
    chart = _scan_bars(
        walked=s.files_walked,
        hashed=s.files_hashed,
        skipped=s.files_skipped,
        errors=s.errors,
    )
    rows = [
        _row("scan_run_id", _esc(s.scan_run_id), metric="scan.id"),
        _row("root", _esc(s.root_path), metric="scan.root"),
        _row("finished_at", _esc(s.finished_at), metric="scan.finished"),
        _row("files_walked", f"{s.files_walked:,}", metric="scan.files_walked"),
        _row("files_hashed", f"{s.files_hashed:,}", metric="scan.files_hashed"),
        _row("files_skipped", f"{s.files_skipped:,}", metric="scan.files_skipped"),
        _row("bytes_hashed", _esc(_format_bytes(s.bytes_hashed)), metric="scan.bytes_hashed"),
    ]
    if s.errors:
        rows.append(_row("errors", f'<span class="bad">{s.errors:,}</span>', metric="scan.errors"))
    return _card("latest scan", rows, extra=chart, index=1, panel="scans")


def _render_stash(report: StatusReport) -> str:
    s = report.stash
    if s.in_flight_entries == 0:
        return _card("stash", [_row("status", "<em>no in-flight stash entries</em>")], index=2, panel="ops")
    rows = [
        _row("in_flight_entries", f"{s.in_flight_entries:,}", metric="stash.in_flight"),
        _row("distinct_run_ids", f"{s.distinct_run_ids:,}", metric="stash.runs"),
        _row("oldest", _esc(s.oldest_ts_iso), metric="stash.oldest"),
        _row("newest", _esc(s.newest_ts_iso), metric="stash.newest"),
    ]
    return _card("stash", rows, index=2, panel="ops")


def _render_adapter(report: StatusReport, *, attr: str, title: str, index: int) -> str:
    run = getattr(report, attr)
    if run is None:
        return _card(title, [_row("status", "<em>no runs yet</em>")], index=index, panel="ops")
    prefix = "replicate" if attr == "last_replicate" else "archive"
    rows = [_row("timestamp", _esc(run.timestamp), metric=f"{prefix}.ts")]
    if run.policy_name:
        rows.append(_row("policy", _esc(run.policy_name), metric=f"{prefix}.policy"))
    runs = int(run.payload.get("runs", 0) or 0)
    successes = int(run.payload.get("successes", 0) or 0)
    failures = int(run.payload.get("failures", 0) or 0)
    if attr == "last_replicate":
        bytes_n = int(run.payload.get("bytes_transferred", 0) or 0)
        byte_label = "bytes_transferred"
    else:
        bytes_n = int(run.payload.get("total_bytes_added", 0) or 0)
        byte_label = "total_bytes_added"
    rows.append(_row("runs", f"{runs:,}", metric=f"{prefix}.runs"))
    rows.append(_row("successes", f"{successes:,}", metric=f"{prefix}.successes"))
    if failures:
        rows.append(_row("failures", f'<span class="bad">{failures:,}</span>', metric=f"{prefix}.failures"))
    rows.append(_row(byte_label, _esc(_format_bytes(bytes_n)), metric=f"{prefix}.bytes"))
    return _card(
        title,
        rows,
        extra=_success_ring(successes=successes, failures=failures, label=prefix),
        index=index,
        panel="ops",
    )


def _render_audit(report: StatusReport) -> str:
    a = report.audit_chain
    rows = [_row("rows_checked", f"{a.rows_checked:,}", metric="audit.rows_checked")]
    if getattr(a, "skipped", False):
        rows.append(_row("status", '<span class="warn">skipped (quick)</span>', metric="audit.status"))
    elif a.ok:
        rows.append(_row("status", '<span class="ok">ok</span>', metric="audit.status"))
    else:
        rows.append(_row("status", '<span class="bad">BROKEN</span>', metric="audit.status"))
        if a.error:
            rows.append(_row("error", _esc(a.error), metric="audit.error"))
    return _card(
        "audit chain",
        rows,
        extra=_audit_ring(ok=a.ok, skipped=bool(getattr(a, "skipped", False)), rows=a.rows_checked),
        index=5,
        panel="audit",
    )



def _render_posture_banner(posture: dict[str, Any] | None) -> str:
    """Green / amber / red estate-health banner above the KPI strip."""
    if not posture:
        return ""
    overall = str(posture.get("overall") or "unknown")
    if overall not in ("ok", "warn", "fail", "unknown"):
        overall = "unknown"
    score = posture.get("score")
    try:
        score_s = f"{int(score)}" if score is not None else "—"
    except (TypeError, ValueError):
        score_s = "—"
    labels = {
        "ok": "Estate healthy",
        "warn": "Estate needs attention",
        "fail": "Estate unhealthy",
        "unknown": "Estate posture unknown",
    }
    title = labels.get(overall, "Estate posture")
    messages = posture.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    # Prefer first fail/warn messages; cap for layout.
    msg_bits = [str(m) for m in messages if m][:3]
    detail = " · ".join(msg_bits) if msg_bits else str(
        (posture.get("signals") or {}).get("inventory", {}).get("message")
        or "No open health issues."
    )
    source = str(posture.get("source") or "status")
    quick = posture.get("quick")
    mode = "quick" if quick else "full"
    return (
        f'<div class="card posture-banner posture-{html.escape(overall)}" '
        f'id="posture-banner" role="status" '
        f'data-posture-overall="{html.escape(overall)}" '
        f'data-posture-score="{html.escape(score_s)}">'
        f'<div class="posture-head">'
        f'<span class="posture-dot" aria-hidden="true"></span>'
        f'<strong class="posture-title" data-metric="posture.overall_label">'
        f'{html.escape(title)}</strong>'
        f'<span class="posture-level" data-metric="posture.overall">{html.escape(overall)}</span>'
        f'<span class="posture-score">score <span data-metric="posture.score">{html.escape(score_s)}</span></span>'
        f'<span class="posture-meta dim">{html.escape(mode)} · {html.escape(source)}</span>'
        f'</div>'
        f'<p class="posture-detail" data-metric="posture.detail">{_esc(detail)}</p>'
        f'</div>'
    )


_CSS = r"""
:root {
  --bg:#f4f5f7; --bg-elevated:#fff; --bg-soft:#eef0f4; --ink:#12131a;
  --muted:#6b6f7b; --muted-strong:#4a4e5a; --line:#e2e5ec;
  --accent:#0d9488; --accent-2:#6366f1; --accent-glow:rgba(13,148,136,.28);
  --ok:#16a34a; --bad:#dc2626; --warn:#d97706;
  --shadow:0 1px 2px rgba(16,24,40,.04),0 8px 24px rgba(16,24,40,.06);
  --shadow-hover:0 2px 4px rgba(16,24,40,.06),0 16px 40px rgba(16,24,40,.1);
  --ease:cubic-bezier(.16,1,.3,1);
  --d-fast:120ms; --d-snug:180ms; --d-med:260ms; --d-grand:480ms;
  --radius:14px;
  --mono:ui-monospace,"SF Mono",Consolas,monospace;
  --sans:-apple-system,"SF Pro Text","Segoe UI",system-ui,sans-serif;
  --drawer-w:min(480px,92vw);
  --rail-w:280px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0e0f14; --bg-elevated:#171922; --bg-soft:#1e2130; --ink:#e8e9ef;
    --muted:#9aa0b0; --muted-strong:#b6bbca; --line:#2a2e3d;
    --accent:#2dd4bf; --accent-2:#818cf8; --accent-glow:rgba(45,212,191,.22);
    --ok:#4ade80; --bad:#f87171; --warn:#fbbf24;
    --shadow:0 1px 2px rgba(0,0,0,.35),0 12px 32px rgba(0,0,0,.35);
    --shadow-hover:0 2px 6px rgba(0,0,0,.4),0 20px 48px rgba(0,0,0,.45);
  }
}
* { box-sizing:border-box; }
html { color-scheme: light dark; }
body {
  font:14px/1.5 var(--sans); margin:0; min-height:100vh; color:var(--ink);
  background:
    radial-gradient(1200px 600px at 10% -10%, color-mix(in srgb,var(--accent) 14%,transparent), transparent 60%),
    radial-gradient(900px 500px at 100% 0%, color-mix(in srgb,var(--accent-2) 12%,transparent), transparent 55%),
    var(--bg);
  -webkit-font-smoothing:antialiased;
}
body.drawer-open { overflow:hidden; }
.app { display:grid; grid-template-columns:1fr var(--rail-w); gap:0; min-height:100vh; }
@media (max-width:1100px) { .app { grid-template-columns:1fr; } .ops-rail { display:none; } .ops-rail.open { display:block; position:fixed; inset:0 0 0 auto; z-index:40; width:var(--rail-w); box-shadow:var(--shadow-hover); } }
.main-col { padding:24px 22px 48px; min-width:0; }
header {
  display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between;
  gap:12px 16px; margin-bottom:16px;
  animation:rise var(--d-grand) var(--ease) both;
}
.brand { display:flex; align-items:center; gap:12px; }
.mark {
  width:36px; height:36px; border-radius:10px; color:#fff; font-weight:700;
  display:grid; place-items:center; letter-spacing:-.02em;
  background:linear-gradient(145deg,var(--accent),var(--accent-2));
  box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 30%,transparent),0 8px 20px var(--accent-glow);
}
header h1 { margin:0; font-size:1.2rem; font-weight:650; letter-spacing:-.02em; }
header .sub { margin:2px 0 0; color:var(--muted); font-size:12.5px; }
.meta-block { display:flex; flex-wrap:wrap; align-items:center; gap:8px 12px; color:var(--muted); font-size:12.5px; }
.live,.chip-btn,.scope a,.card-expand,.tab,.act-btn,.icon-btn {
  border:1px solid var(--line); background:var(--bg-elevated); color:var(--ink);
  border-radius:999px; font:inherit; cursor:pointer;
  transition:background var(--d-fast) var(--ease), border-color var(--d-fast) var(--ease), transform var(--d-fast) var(--ease);
}
.live { display:inline-flex; align-items:center; gap:7px; padding:4px 10px 4px 8px; font-variant-numeric:tabular-nums; }
.live-dot { width:7px; height:7px; border-radius:50%; background:var(--accent); box-shadow:0 0 0 0 var(--accent-glow); animation:pulse-dot 1.8s var(--ease) infinite; }
.live.paused .live-dot,.live.stale .live-dot { background:var(--warn); animation:none; }
.live.err .live-dot { background:var(--bad); animation:none; }
.chip-btn,.card-expand { padding:5px 12px; font-size:12px; font-weight:500; color:var(--muted); }
.chip-btn:hover,.card-expand:hover,.tab:hover,.act-btn:hover { color:var(--ink); border-color:color-mix(in srgb,var(--accent) 40%,var(--line)); background:color-mix(in srgb,var(--accent) 8%,var(--bg-elevated)); }
.chip-btn:active,.card-expand:active,.act-btn:active { transform:scale(.97); }
.chip-btn.active { color:var(--ink); background:color-mix(in srgb,var(--accent) 16%,var(--bg-elevated)); box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--accent) 35%,transparent); }
.scope { display:inline-flex; border:1px solid var(--line); border-radius:999px; overflow:hidden; background:var(--bg-elevated); }
.scope a { text-decoration:none; color:var(--muted); padding:5px 12px; font-size:12px; font-weight:500; border:0; border-radius:0; }
.scope a.active { color:var(--ink); background:color-mix(in srgb,var(--accent) 16%,var(--bg-elevated)); }
.kpi-strip { display:grid; grid-template-columns:repeat(auto-fit,minmax(108px,1fr)); gap:10px; margin-bottom:16px; }
.kpi {
  text-align:left; background:var(--bg-elevated); border:1px solid var(--line); border-radius:12px;
  padding:12px 14px; box-shadow:var(--shadow); cursor:pointer;
  animation:rise var(--d-grand) var(--ease) both; animation-delay:calc(var(--i,0)*40ms);
  transition:transform var(--d-fast) var(--ease), box-shadow var(--d-snug) var(--ease), border-color var(--d-fast);
}
@media (hover:hover) and (pointer:fine) {
  .kpi:hover,.card:hover { transform:translateY(-2px); box-shadow:var(--shadow-hover); border-color:color-mix(in srgb,var(--accent) 30%,var(--line)); }
}
.kpi-value { font-family:var(--mono); font-size:1.3rem; font-weight:650; letter-spacing:-.03em; font-variant-numeric:tabular-nums; line-height:1.15; }
.kpi-label { margin-top:4px; font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); font-weight:600; }
.kpi-ok .kpi-value { color:var(--ok); } .kpi-bad .kpi-value { color:var(--bad); } .kpi-warn .kpi-value { color:var(--warn); }
.panel-tabs { display:flex; flex-wrap:wrap; gap:6px; margin:0 0 14px; }
.tab { padding:6px 12px; font-size:12.5px; font-weight:550; color:var(--muted); border-radius:10px; }
.tab.active { color:var(--ink); background:color-mix(in srgb,var(--accent) 14%,var(--bg-elevated)); border-color:color-mix(in srgb,var(--accent) 35%,var(--line)); }
.panel {
  display:none; background:var(--bg-elevated); border:1px solid var(--line); border-radius:var(--radius);
  padding:14px 16px; margin-bottom:16px; box-shadow:var(--shadow); min-height:120px;
}
.panel.active { display:block; animation:fade var(--d-med) var(--ease); }
.panel h3 { margin:0 0 10px; font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
.panel .toolbar { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; align-items:center; }
.panel input[type=text], .panel input[type=search], .drawer input, .modal input, .ops-rail input {
  flex:1; min-width:140px; padding:8px 10px; border-radius:10px; border:1px solid var(--line);
  background:var(--bg-soft); color:var(--ink); font:inherit;
}
.panel input:focus, .drawer input:focus, .modal input:focus { outline:2px solid color-mix(in srgb,var(--accent) 45%,transparent); border-color:var(--accent); }
.data-table { width:100%; border-collapse:collapse; font-size:12.5px; }
.data-table th,.data-table td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
.data-table th { color:var(--muted); font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
.data-table td.num,.data-table th.num { text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; }
.data-table tr { cursor:pointer; transition:background var(--d-fast); }
.data-table tr:hover { background:color-mix(in srgb,var(--accent) 6%,transparent); }
.handoff-card {
  margin:0 0 14px; padding:12px 14px; border-radius:10px;
  border:1px solid color-mix(in srgb,var(--warn) 45%,var(--line));
  background:color-mix(in srgb,var(--warn) 10%,var(--card));
}
.handoff-card h4 { margin:0 0 8px; font-size:13px; }
.handoff-cmd { font-size:12px; white-space:pre-wrap; word-break:break-all; margin:6px 0; }
.perf-hint { font-size:12.5px; color:var(--muted); margin:0 0 10px; padding:8px 10px; border-radius:8px; background:var(--bg-soft); border:1px solid var(--line); }
.surface-svg { width:100%; height:auto; max-height:480px; display:block; border-radius:10px; background:var(--bg-soft); border:1px solid var(--line); }
.surface-label { fill:var(--ink); font-size:11px; font-family:var(--sans,system-ui,sans-serif); pointer-events:none; }
.surface-cell:hover rect { filter:brightness(1.12); }
.mono { font-family:var(--mono); font-size:12px; word-break:break-all; }
.muted { color:var(--muted); }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; }
.card {
  background:var(--bg-elevated); border-radius:var(--radius); border:1px solid var(--line);
  padding:14px 16px 12px; box-shadow:var(--shadow);
  animation:rise var(--d-grand) var(--ease) both; animation-delay:calc(80ms + var(--i,0)*50ms);
  transition:transform var(--d-fast) var(--ease), box-shadow var(--d-snug) var(--ease), border-color var(--d-fast);
}
.card-head { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:10px; }
.card h2 { margin:0; font-size:11.5px; font-weight:650; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
.card-visual { margin:0 0 12px; }
.chart-bars { width:100%; height:12px; display:block; border-radius:6px; overflow:hidden; }
.dist-bars { height:8px; margin-bottom:8px; }
.bar-track { fill:var(--bg-soft); }
.bar-hashed,.dist-permanodes { fill:var(--accent); }
.bar-skipped,.dist-claims { fill:var(--accent-2); }
.bar-errors { fill:var(--bad); }
.dist-scans { fill:var(--ok); }
.dist-audit { fill:var(--muted); opacity:.55; }
.legend { list-style:none; margin:10px 0 0; padding:0; display:flex; flex-wrap:wrap; gap:8px 14px; font-size:12px; color:var(--muted); }
.legend.compact { margin-top:6px; }
.legend strong { color:var(--ink); font-family:var(--mono); font-weight:600; font-variant-numeric:tabular-nums; }
.swatch { display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:6px; vertical-align:middle; }
.swatch.hashed { background:var(--accent); } .swatch.skipped { background:var(--accent-2); }
.swatch.walked { background:var(--ok); } .swatch.errors { background:var(--bad); }
.swatch.muted { background:var(--muted); opacity:.6; }
.gauge { display:flex; align-items:center; gap:14px; }
.gauge-svg { width:72px; height:72px; flex-shrink:0; }
.gauge-track { fill:none; stroke:var(--bg-soft); stroke-width:3.2; }
.gauge-arc { fill:none; stroke-width:3.2; stroke-linecap:round; transition:stroke-dasharray var(--d-med) var(--ease); }
.gauge-ok { stroke:var(--ok); } .gauge-bad { stroke:var(--bad); } .gauge-warn { stroke:var(--warn); } .gauge-muted { stroke:var(--muted); }
.gauge-text { font-family:var(--mono); font-size:7.5px; font-weight:700; fill:var(--ink); }
.gauge-text-sm { font-size:6.5px; text-transform:uppercase; }
.gauge-meta { font-size:12.5px; color:var(--muted); font-variant-numeric:tabular-nums; }
table.kv { width:100%; border-collapse:collapse; }
table.kv th { text-align:left; font-weight:500; color:var(--muted-strong); padding:5px 0; width:42%; font-size:12.5px; }
table.kv td { text-align:right; font-family:var(--mono); font-size:12.5px; padding:5px 0; word-break:break-all; font-variant-numeric:tabular-nums; }
.card table { width:100%; border-collapse:collapse; }
.card th { text-align:left; font-weight:500; color:var(--muted-strong); padding:5px 0; width:42%; font-size:12.5px; }
.card td { text-align:right; font-family:var(--mono); font-size:12.5px; padding:5px 0; word-break:break-all; font-variant-numeric:tabular-nums; }
.dim { color:var(--muted); font-size:12px; }
.ok { color:var(--ok); font-weight:600; } .bad { color:var(--bad); font-weight:600; } .warn { color:var(--warn); font-weight:600; }
em { color:var(--muted); font-style:normal; }
a { color:var(--accent); }
.audit-banner {
  border:1px solid color-mix(in srgb,var(--bad) 45%,var(--line));
  background:color-mix(in srgb,var(--bad) 10%,var(--bg-elevated));
  border-radius:var(--radius); padding:12px 16px; margin-bottom:14px;
}
.posture-banner {
  border:1px solid var(--line);
  background:var(--bg-elevated);
  border-radius:var(--radius); padding:12px 16px; margin-bottom:14px;
  box-shadow:var(--shadow);
  animation:rise var(--d-grand) var(--ease) both;
}
.posture-banner.posture-ok {
  border-color:color-mix(in srgb,var(--ok) 45%,var(--line));
  background:color-mix(in srgb,var(--ok) 10%,var(--bg-elevated));
}
.posture-banner.posture-warn {
  border-color:color-mix(in srgb,var(--warn) 50%,var(--line));
  background:color-mix(in srgb,var(--warn) 12%,var(--bg-elevated));
}
.posture-banner.posture-fail {
  border-color:color-mix(in srgb,var(--bad) 50%,var(--line));
  background:color-mix(in srgb,var(--bad) 12%,var(--bg-elevated));
}
.posture-banner.posture-unknown {
  border-color:color-mix(in srgb,var(--muted) 40%,var(--line));
  background:color-mix(in srgb,var(--muted) 8%,var(--bg-elevated));
}
.posture-head {
  display:flex; flex-wrap:wrap; align-items:center; gap:8px 12px;
}
.posture-dot {
  width:10px; height:10px; border-radius:50%; background:var(--muted);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--muted) 25%,transparent);
}
.posture-ok .posture-dot { background:var(--ok); box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent); }
.posture-warn .posture-dot { background:var(--warn); box-shadow:0 0 0 3px color-mix(in srgb,var(--warn) 25%,transparent); }
.posture-fail .posture-dot { background:var(--bad); box-shadow:0 0 0 3px color-mix(in srgb,var(--bad) 25%,transparent); }
.posture-title { font-size:14px; font-weight:650; letter-spacing:-.01em; }
.posture-ok .posture-title { color:var(--ok); }
.posture-warn .posture-title { color:var(--warn); }
.posture-fail .posture-title { color:var(--bad); }
.posture-level {
  font-family:var(--mono); font-size:11px; font-weight:700; text-transform:uppercase;
  letter-spacing:.06em; padding:2px 8px; border-radius:999px;
  border:1px solid var(--line); background:var(--bg-soft); color:var(--muted-strong);
}
.posture-ok .posture-level { color:var(--ok); border-color:color-mix(in srgb,var(--ok) 35%,var(--line)); }
.posture-warn .posture-level { color:var(--warn); border-color:color-mix(in srgb,var(--warn) 35%,var(--line)); }
.posture-fail .posture-level { color:var(--bad); border-color:color-mix(in srgb,var(--bad) 35%,var(--line)); }
.posture-score { font-family:var(--mono); font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; }
.posture-meta { font-size:12px; margin-left:auto; }
.posture-detail { margin:8px 0 0; font-size:13px; color:var(--muted-strong); line-height:1.4; }
.ops-rail {
  border-left:1px solid var(--line); background:color-mix(in srgb,var(--bg-elevated) 92%,var(--bg));
  padding:18px 14px 32px; position:sticky; top:0; height:100vh; overflow:auto;
}
.ops-rail h2 { margin:0 0 4px; font-size:13px; letter-spacing:.04em; text-transform:uppercase; color:var(--muted); }
.ops-rail .rail-sub { margin:0 0 14px; font-size:12px; color:var(--muted); }
.act-group { margin-bottom:14px; }
.act-group h3 { margin:0 0 8px; font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); }
.act-btn {
  display:block; width:100%; text-align:left; padding:8px 10px; margin:0 0 6px;
  border-radius:10px; font-size:12.5px; font-weight:550;
}
.act-btn.danger { border-color:color-mix(in srgb,var(--bad) 40%,var(--line)); color:var(--bad); }
.act-btn.slow::after { content:" slow"; font-size:10px; color:var(--warn); font-weight:600; }
.drawer-backdrop {
  position:fixed; inset:0; background:rgba(10,12,18,.45); opacity:0; pointer-events:none;
  transition:opacity var(--d-med) var(--ease); z-index:50;
}
.drawer-backdrop.open { opacity:1; pointer-events:auto; }
.drawer {
  position:fixed; top:0; right:0; width:var(--drawer-w); height:100%; background:var(--bg-elevated);
  border-left:1px solid var(--line); box-shadow:var(--shadow-hover); z-index:51;
  transform:translateX(105%); transition:transform var(--d-med) var(--ease);
  display:flex; flex-direction:column;
}
.drawer.open { transform:none; }
.drawer-head { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:14px 16px; border-bottom:1px solid var(--line); }
.drawer-head h2 { margin:0; font-size:15px; }
.drawer-body { padding:14px 16px 28px; overflow:auto; flex:1; }
.drawer pre, .modal pre, #action-result {
  background:var(--bg-soft); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; font-family:var(--mono); font-size:11.5px; white-space:pre-wrap; word-break:break-word;
  max-height:50vh; overflow:auto;
}
.modal-backdrop {
  position:fixed; inset:0; background:rgba(10,12,18,.5); z-index:60; display:none;
  align-items:center; justify-content:center; padding:20px;
}
.modal-backdrop.open { display:flex; }
.modal {
  width:min(440px,100%); background:var(--bg-elevated); border:1px solid var(--line);
  border-radius:16px; box-shadow:var(--shadow-hover); padding:18px; animation:rise var(--d-med) var(--ease);
}
.modal h2 { margin:0 0 8px; font-size:16px; }
.modal p { margin:0 0 12px; color:var(--muted); font-size:13px; }
.modal .row { display:flex; flex-direction:column; gap:6px; margin-bottom:10px; }
.modal label { font-size:12px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
.modal-actions { display:flex; gap:8px; justify-content:flex-end; margin-top:14px; }
.toast {
  position:fixed; bottom:18px; left:50%; transform:translateX(-50%) translateY(20px);
  background:var(--bg-elevated); border:1px solid var(--line); border-radius:12px;
  padding:10px 14px; box-shadow:var(--shadow-hover); z-index:70; opacity:0; pointer-events:none;
  transition:opacity var(--d-snug) var(--ease), transform var(--d-snug) var(--ease);
  max-width:min(520px,92vw); font-size:13px;
}
.toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
.toast.ok { border-color:color-mix(in srgb,var(--ok) 40%,var(--line)); }
.toast.err { border-color:color-mix(in srgb,var(--bad) 40%,var(--line)); }
footer { margin-top:24px; color:var(--muted); font-size:12px; text-align:center; }
footer code { font-family:var(--mono); font-size:11px; padding:1px 6px; border-radius:4px; background:var(--bg-soft); border:1px solid var(--line); }
.metric-value-pulse { animation:metric-pop 700ms var(--ease); }
.skeleton { height:12px; border-radius:6px; background:linear-gradient(90deg,var(--bg-soft),color-mix(in srgb,var(--accent) 10%,var(--bg-soft)),var(--bg-soft)); background-size:200% 100%; animation:shimmer 1.2s linear infinite; margin:8px 0; }
.visually-hidden { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
@keyframes rise { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
@keyframes fade { from { opacity:0; } to { opacity:1; } }
@keyframes pulse-dot { 0% { box-shadow:0 0 0 0 var(--accent-glow); } 70% { box-shadow:0 0 0 8px transparent; } 100% { box-shadow:0 0 0 0 transparent; } }
@keyframes metric-pop { 0% { color:var(--accent); } 100% { color:inherit; } }
@keyframes shimmer { 0% { background-position:200% 0; } 100% { background-position:-200% 0; } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration:.01ms !important; animation-iteration-count:1 !important; transition-duration:.01ms !important; }
  .live-dot { animation:none; }
}
"""


_JS = r"""
(function () {
  var cfg = document.getElementById("steward-dash-config");
  if (!cfg) return;
  var refresh = parseInt(cfg.getAttribute("data-refresh") || "0", 10) || 0;
  var includeImports = cfg.getAttribute("data-include-imports") === "1";
  var quick = cfg.getAttribute("data-quick") !== "0";
  var paused = false;
  var pollTimer = null;
  var analysisLoaded = false;
  var actionsCache = null;

  var live = document.getElementById("live-status");
  var liveLabel = document.getElementById("live-label");
  var renderedAt = document.getElementById("rendered-at");
  var toastEl = document.getElementById("toast");
  var drawer = document.getElementById("drawer");
  var drawerBack = document.getElementById("drawer-backdrop");
  var drawerTitle = document.getElementById("drawer-title");
  var drawerBody = document.getElementById("drawer-body");
  var modalBack = document.getElementById("modal-backdrop");
  var actionResult = document.getElementById("action-result");

  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function fmtInt(n) {
    try { return Number(n).toLocaleString("en-US"); } catch (e) { return String(n); }
  }
  function fmtBytes(n) {
    var units = ["B","KiB","MiB","GiB","TiB"], f = Number(n) || 0;
    for (var i = 0; i < units.length; i++) {
      if (f < 1024 || i === units.length - 1)
        return units[i] === "B" ? (Math.round(f) + " B") : (f.toFixed(1) + " " + units[i]);
      f /= 1024;
    }
    return n + " B";
  }
  function toast(msg, kind) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.className = "toast show " + (kind || "");
    clearTimeout(toastEl._t);
    toastEl._t = setTimeout(function () { toastEl.classList.remove("show"); }, 3200);
  }
  function setText(el, text, soft) {
    if (!el) return;
    var next = String(text);
    if (el.textContent === next) return;
    el.textContent = next;
    if (!soft) {
      el.classList.remove("metric-value-pulse");
      void el.offsetWidth;
      el.classList.add("metric-value-pulse");
    }
  }
  function setAll(metric, text, soft) {
    qsa('[data-metric="' + metric + '"]').forEach(function (el) { setText(el, text, soft); });
  }
  function statusQuery() {
    var q = [];
    if (includeImports) q.push("include_imports=1");
    if (!quick) q.push("full=1");
    return q.length ? ("?" + q.join("&")) : "";
  }

  function applyStatus(data) {
    var inv = data.inventory || {};
    var scan = data.latest_scan || {};
    var stash = data.stash || {};
    var audit = data.audit_chain || {};
    var db = data.db || {};
    setAll("inv.permanodes", fmtInt(inv.permanodes || 0), true);
    setAll("inv.current_claims", fmtInt(inv.current_claims || 0), true);
    setAll("inv.scan_runs", fmtInt(inv.scan_runs || 0), true);
    setAll("inv.audit_entries", fmtInt(inv.audit_entries || 0), true);
    setAll("inv.machines", fmtInt(inv.machines || 0), true);
    setAll("stash.in_flight", fmtInt(stash.in_flight_entries || 0), true);

    if (scan.scan_run_id != null) {
      setAll("scan.id", String(scan.scan_run_id), true);
      setAll("scan.root", scan.root_path || "—", true);
      setAll("scan.finished", scan.finished_at || "—", true);
      setAll("scan.files_walked", fmtInt(scan.files_walked || 0), true);
      setAll("scan.files_hashed", fmtInt(scan.files_hashed || 0), true);
      setAll("scan.files_skipped", fmtInt(scan.files_skipped || 0), true);
      setAll("scan.bytes_hashed", fmtBytes(scan.bytes_hashed || 0), true);
      if (document.querySelector('[data-metric="scan.errors"]'))
        setAll("scan.errors", fmtInt(scan.errors || 0), true);
      updateScanBars(scan);
    }
    if (stash.in_flight_entries) {
      setAll("stash.runs", fmtInt(stash.distinct_run_ids || 0), true);
      setAll("stash.oldest", stash.oldest_ts_iso || "—", true);
      setAll("stash.newest", stash.newest_ts_iso || "—", true);
    }
    updateAdapter("replicate", data.last_replicate, "bytes_transferred");
    updateAdapter("archive", data.last_archive, "total_bytes_added");

    setAll("audit.rows_checked", fmtInt(audit.rows_checked || 0), true);
    var auditStatus = audit.skipped ? "skip" : (audit.ok ? "ok" : "BROKEN");
    qsa('[data-metric="audit.status"]').forEach(function (el) {
      if (el.classList.contains("kpi-value")) setText(el, auditStatus, true);
    });
    if (db.size_bytes != null) {
      var sizeEl = document.querySelector('[data-metric="db.size"]');
      if (sizeEl) setText(sizeEl, "(" + fmtBytes(db.size_bytes) + ")", true);
    }
    if (db.modified_iso) setAll("db.modified", db.modified_iso, true);

    if (renderedAt) {
      var now = new Date();
      renderedAt.textContent = now.toISOString().slice(0, 19);
    }
    if (data.posture) applyPosture(data.posture);

    if (live) {
      live.classList.remove("stale", "err");
      if (!paused) live.classList.remove("paused");
    }
    if (liveLabel && !paused) liveLabel.textContent = "live · " + refresh + "s";
  }

  function applyPosture(posture) {
    var banner = document.getElementById("posture-banner");
    if (!banner || !posture) return;
    var overall = String(posture.overall || "unknown");
    if (["ok","warn","fail","unknown"].indexOf(overall) < 0) overall = "unknown";
    banner.className = "card posture-banner posture-" + overall;
    banner.setAttribute("data-posture-overall", overall);
    var labels = {
      ok: "Estate healthy",
      warn: "Estate needs attention",
      fail: "Estate unhealthy",
      unknown: "Estate posture unknown"
    };
    var titleEl = banner.querySelector('[data-metric="posture.overall_label"]');
    if (titleEl) setText(titleEl, labels[overall] || "Estate posture", true);
    var levelEl = banner.querySelector('[data-metric="posture.overall"]');
    if (levelEl) setText(levelEl, overall, true);
    var score = posture.score;
    var scoreEl = banner.querySelector('[data-metric="posture.score"]');
    if (scoreEl && score != null) setText(scoreEl, String(score), true);
    banner.setAttribute("data-posture-score", score != null ? String(score) : "—");
    var msgs = posture.messages || [];
    var detail = "";
    if (msgs.length) detail = msgs.slice(0, 3).join(" · ");
    else if (posture.signals && posture.signals.inventory && posture.signals.inventory.message)
      detail = posture.signals.inventory.message;
    else detail = "No open health issues.";
    var detailEl = banner.querySelector('[data-metric="posture.detail"]');
    if (detailEl) setText(detailEl, detail, true);
  }

  function updateAdapter(prefix, run, bytesKey) {
    if (!run || !run.payload) return;
    var s = Number(run.payload.successes || 0), f = Number(run.payload.failures || 0);
    setAll(prefix + ".ts", run.timestamp || "—", true);
    if (run.policy_name) setAll(prefix + ".policy", run.policy_name, true);
    setAll(prefix + ".runs", fmtInt(run.payload.runs || 0), true);
    setAll(prefix + ".successes", fmtInt(s), true);
    if (document.querySelector('[data-metric="' + prefix + '.failures"]'))
      setAll(prefix + ".failures", fmtInt(f), true);
    setAll(prefix + ".bytes", fmtBytes(run.payload[bytesKey] || 0), true);
    updateGauge(prefix, s, f);
  }

  function updateScanBars(scan) {
    var walked = scan.files_walked || 0, hashed = scan.files_hashed || 0;
    var skipped = scan.files_skipped || 0, errors = scan.errors || 0;
    var total = Math.max(walked, hashed + skipped, 1);
    function pct(p) { return Math.max(0, Math.min(100, 100 * p / total)); }
    var h = pct(hashed), s = pct(skipped), e = pct(errors);
    var used = h + s + e;
    var other = walked > hashed + skipped ? Math.max(0, 100 - used) : 0;
    var scale = 100 / Math.max(h + s + e + other, 1e-9);
    h *= scale; s *= scale; e *= scale;
    var barH = document.querySelector('[data-bar="hashed"]');
    var barS = document.querySelector('[data-bar="skipped"]');
    var barE = document.querySelector('[data-bar="errors"]');
    if (barH) { barH.setAttribute("width", h.toFixed(2)); barH.setAttribute("x", "0"); }
    if (barS) { barS.setAttribute("x", h.toFixed(2)); barS.setAttribute("width", s.toFixed(2)); }
    if (barE) { barE.setAttribute("x", (h + s).toFixed(2)); barE.setAttribute("width", e.toFixed(2)); }
  }

  function updateGauge(prefix, successes, failures) {
    var root = document.querySelector('[data-gauge="' + prefix + '"]');
    if (!root) return;
    var total = successes + failures;
    var rate = total ? (100 * successes / total) : 0;
    var c = 97.389, dash = c * rate / 100;
    var arc = root.querySelector(".gauge-arc");
    var text = root.querySelector(".gauge-text");
    if (arc) {
      arc.setAttribute("stroke-dasharray", dash.toFixed(2) + " " + (c - dash).toFixed(2));
      arc.className = "gauge-arc gauge-" + (!total ? "muted" : (failures === 0 ? "ok" : "warn"));
    }
    if (text) text.textContent = Math.round(rate) + "%";
  }

  function pollOnce() {
    if (paused || document.hidden) return;
    fetch("/status.json" + statusQuery(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) { applyStatus(data); })
      .catch(function () {
        if (live) live.classList.add("err");
        if (liveLabel) liveLabel.textContent = "poll failed";
      });
  }

  function schedulePoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    if (refresh > 0 && !paused) pollTimer = setInterval(pollOnce, refresh * 1000);
  }

  // ── panels ────────────────────────────────────────────
  function showPanel(name) {
    qsa(".panel").forEach(function (p) { p.classList.toggle("active", p.id === "panel-" + name); });
    qsa(".tab").forEach(function (t) { t.classList.toggle("active", t.getAttribute("data-panel") === name); });
    if (name === "scans" || name === "audit" || name === "schedules" || name === "policies") loadAnalysis();
    if (name === "queues") loadQueues();
    if (name === "stats") loadStatsOverview();
    if (name === "surface") loadSurface();
    if (name === "fp") loadFp();
    if (name === "fleet") loadFleet();
    if (name === "ops") loadActionsRail();
  }

  // ── inventory surface (ADR-0022) ───────────────────────
  var surfacePrefix = "";
  var surfaceColorBy = "none";
  var surfaceFilterPrefix = "";

  function surfaceQuery() {
    var q = "?prefix=" + encodeURIComponent(surfacePrefix)
      + "&color_by=" + encodeURIComponent(surfaceColorBy)
      + "&limit=80";
    if (includeImports) q += "&include_imports=1";
    var tierEl = qs("#surface-tier");
    if (tierEl && tierEl.value) q += "&tier=" + encodeURIComponent(tierEl.value);
    var volEl = qs("#surface-volume");
    if (volEl && volEl.value) q += "&volume=" + encodeURIComponent(volEl.value);
    return q;
  }

  function hashColor(s) {
    var h = 0;
    for (var i = 0; i < String(s).length; i++) h = ((h << 5) - h) + String(s).charCodeAt(i) | 0;
    var hue = Math.abs(h) % 360;
    return "hsl(" + hue + " 55% 42%)";
  }

  // Minimal squarify (Bruls et al.) for treemap layout
  function squarify(items, x, y, w, h) {
    if (!items.length || w <= 0 || h <= 0) return [];
    var total = 0;
    items.forEach(function (it) { total += it.value; });
    if (total <= 0) return [];
    var rects = [];
    var row = [];
    var rowSum = 0;
    var horizontal = w >= h;
    var side = horizontal ? h : w;
    function worst(row, side) {
      if (!row.length) return Infinity;
      var s = 0, max = 0, min = Infinity;
      row.forEach(function (r) { s += r.value; if (r.value > max) max = r.value; if (r.value < min) min = r.value; });
      var s2 = s * s;
      var side2 = side * side;
      return Math.max((side2 * max) / s2, s2 / (side2 * min));
    }
    function layoutRow(row, x, y, w, h, horizontal) {
      var s = 0;
      row.forEach(function (r) { s += r.value; });
      if (s <= 0) return;
      if (horizontal) {
        var rw = w * (s / total);
        var yy = y;
        row.forEach(function (r) {
          var rh = h * (r.value / s);
          rects.push({ item: r, x: x, y: yy, w: rw, h: rh });
          yy += rh;
        });
        // shrink remaining
        // caller updates x,w via closure — handled below
      } else {
        var rh = h * (s / total);
        var xx = x;
        row.forEach(function (r) {
          var rw = w * (r.value / s);
          rects.push({ item: r, x: xx, y: y, w: rw, h: rh });
          xx += rw;
        });
      }
    }
    // iterative simpler layout: slice by area
    var cx = x, cy = y, cw = w, ch = h, rem = total;
    items.slice().sort(function (a, b) { return b.value - a.value; }).forEach(function (it, idx, arr) {
      if (rem <= 0 || it.value <= 0) return;
      var frac = it.value / rem;
      if (cw >= ch) {
        var rw = cw * frac;
        rects.push({ item: it, x: cx, y: cy, w: rw, h: ch });
        cx += rw; cw -= rw;
      } else {
        var rh = ch * frac;
        rects.push({ item: it, x: cx, y: cy, w: cw, h: rh });
        cy += rh; ch -= rh;
      }
      rem -= it.value;
    });
    return rects;
  }

  function renderSurface(data) {
    var host = qs("#surface-map");
    var crumb = qs("#surface-crumb");
    var legend = qs("#surface-legend");
    if (!host) return;
    if (!data.ok) {
      host.innerHTML = "<p class='bad'>" + esc(data.error || "surface failed") + "</p>";
      return;
    }
    surfacePrefix = data.path_prefix || "";
    if (crumb) {
      crumb.innerHTML = "";
      var parts = surfacePrefix ? surfacePrefix.split("/").filter(Boolean) : [];
      var btn = document.createElement("button");
      btn.type = "button"; btn.className = "chip-btn"; btn.textContent = "root";
      btn.addEventListener("click", function () { surfacePrefix = ""; loadSurface(); });
      crumb.appendChild(btn);
      var acc = "";
      parts.forEach(function (p) {
        acc += "/" + p;
        (function (path) {
          var b = document.createElement("button");
          b.type = "button"; b.className = "chip-btn"; b.textContent = p;
          b.addEventListener("click", function () { surfacePrefix = path; loadSurface(); });
          crumb.appendChild(document.createTextNode(" / "));
          crumb.appendChild(b);
        })(acc);
      });
    }
    var children = data.children || [];
    if (!children.length) {
      host.innerHTML = "<p class='muted'>No children under this prefix (inventory claims only — not live du).</p>";
      if (legend) legend.innerHTML = "";
      return;
    }
    var items = children.map(function (c) {
      return {
        value: Math.max(Number(c.total_bytes) || Number(c.claim_count) || 1, 1),
        node: c
      };
    });
    var W = 960, H = 420;
    var rects = squarify(items, 0, 0, W, H);
    var svg = '<svg class="surface-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Inventory surface treemap">';
    var overlayKeys = {};
    rects.forEach(function (r) {
      var n = r.item.node;
      var ov = n.overlay_value || "";
      if (ov) overlayKeys[ov] = true;
      var fill = surfaceColorBy === "none" ? "var(--accent)" : hashColor(ov || n.name);
      var opacity = surfaceColorBy === "none" ? "0.55" : "0.85";
      var title = esc(n.path) + " · " + fmtBytes(n.total_bytes) + " · " + fmtInt(n.claim_count) + " claims"
        + (ov ? " · " + ov : "");
      svg += '<g class="surface-cell" data-path="' + esc(n.path) + '" data-dir="' + (n.is_dir ? "1" : "0") + '">';
      svg += '<rect x="' + r.x + '" y="' + r.y + '" width="' + Math.max(r.w - 1, 0) + '" height="' + Math.max(r.h - 1, 0)
        + '" fill="' + fill + '" fill-opacity="' + opacity + '" stroke="var(--card)" stroke-width="1" rx="2">';
      svg += "<title>" + title + "</title></rect>";
      if (r.w > 48 && r.h > 18) {
        svg += '<text x="' + (r.x + 4) + '" y="' + (r.y + 14) + '" class="surface-label">' + esc(n.name.slice(0, 28)) + "</text>";
      }
      svg += "</g>";
    });
    svg += "</svg>";
    host.innerHTML = svg;
    qsa(".surface-cell", host).forEach(function (g) {
      g.style.cursor = "pointer";
      g.addEventListener("click", function () {
        var path = g.getAttribute("data-path") || "";
        var isDir = g.getAttribute("data-dir") === "1";
        if (isDir) {
          surfacePrefix = path;
          loadSurface();
        } else {
          surfaceFilterPrefix = path;
          applySurfaceFilter(path);
        }
      });
      g.addEventListener("dblclick", function () {
        var path = g.getAttribute("data-path") || "";
        surfaceFilterPrefix = path;
        applySurfaceFilter(path);
      });
    });
    if (legend) {
      if (surfaceColorBy === "none") {
        legend.innerHTML = "<span class='muted'>size ∝ bytes · monochrome</span>";
      } else {
        var keys = Object.keys(overlayKeys).sort();
        legend.innerHTML = keys.slice(0, 12).map(function (k) {
          return '<span class="swatch" style="background:' + hashColor(k) + '"></span> ' + esc(k);
        }).join(" · ");
      }
    }
    var note = qs("#surface-notes");
    if (note) {
      var bits = [];
      if (data.truncated) bits.push("truncated");
      (data.notes || []).forEach(function (n) { bits.push(n); });
      bits.push((data.elapsed_ms || "?") + "ms");
      note.textContent = bits.join(" · ");
    }
  }

  function applySurfaceFilter(pathPrefix) {
    surfaceFilterPrefix = pathPrefix || "";
    var qEl = qs("#inspect-q");
    if (qEl) qEl.value = surfaceFilterPrefix;
    var prefEl = qs("#stats-path-prefix");
    if (prefEl) prefEl.value = surfaceFilterPrefix;
    loadStatsAxis("cross");
  }

  function loadSurface() {
    var host = qs("#surface-map");
    if (host) host.innerHTML = '<div class="skeleton"></div><p class="muted">Loading inventory surface…</p>';
    var colorSel = qs("#surface-color");
    if (colorSel) surfaceColorBy = colorSel.value || "none";
    fetch("/api/surface" + surfaceQuery(), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(renderSurface)
      .catch(function (e) {
        if (host) host.innerHTML = "<p class='bad'>Surface failed: " + esc(String(e)) + "</p>";
      });
  }

  function loadAnalysis(force) {
    if (analysisLoaded && !force) return;
    var box = qs("#analysis-tables");
    if (box) box.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
    var q = includeImports ? "?include_imports=1" : "";
    fetch("/api/analysis" + q, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        analysisLoaded = true;
        renderScans(data.scans || []);
        renderAudit(data.audit || []);
        renderPolicies(data.policies || []);
        renderSchedules(data.schedules || []);
        if (box) box.innerHTML = "";
      })
      .catch(function (e) {
        if (box) box.innerHTML = '<p class="bad">Failed to load analysis: ' + e + "</p>";
      });
  }

  function renderScans(rows) {
    var el = qs("#scans-table");
    if (!el) return;
    if (!rows.length) { el.innerHTML = "<p class='muted'>No scan runs.</p>"; return; }
    var html = "<table class='data-table'><thead><tr><th>id</th><th>root</th><th>finished</th><th class='num'>walked</th><th class='num'>hashed</th><th class='num'>err</th></tr></thead><tbody>";
    rows.forEach(function (r) {
      html += "<tr data-inspect='" + escAttr(String(r.root_path || "")) + "'>" +
        "<td class='mono'>" + r.id + "</td>" +
        "<td class='mono'>" + esc(r.root_path) + "</td>" +
        "<td>" + esc(r.finished_at || (r.in_progress ? "in progress" : "—")) + "</td>" +
        "<td class='num'>" + fmtInt(r.files_walked || 0) + "</td>" +
        "<td class='num'>" + fmtInt(r.files_hashed || 0) + "</td>" +
        "<td class='num'>" + fmtInt(r.errors || 0) + "</td></tr>";
    });
    el.innerHTML = html + "</tbody></table>";
  }

  function renderAudit(rows) {
    var el = qs("#audit-table");
    if (!el) return;
    if (!rows.length) { el.innerHTML = "<p class='muted'>No audit rows.</p>"; return; }
    var html = "<table class='data-table'><thead><tr><th>id</th><th>time</th><th>actor</th><th>action</th><th>payload</th></tr></thead><tbody>";
    rows.forEach(function (r) {
      var pay = r.payload || "";
      if (pay.length > 120) pay = pay.slice(0, 120) + "…";
      html += "<tr data-detail='" + escAttr(JSON.stringify(r)) + "'>" +
        "<td class='mono'>" + r.id + "</td>" +
        "<td>" + esc(r.timestamp) + "</td>" +
        "<td>" + esc(r.actor) + "</td>" +
        "<td><strong>" + esc(r.action) + "</strong></td>" +
        "<td class='mono'>" + esc(pay) + "</td></tr>";
    });
    el.innerHTML = html + "</tbody></table>";
    qsa("#audit-table tr[data-detail]").forEach(function (tr) {
      tr.addEventListener("click", function () {
        try { openDrawer("Audit row", "<pre>" + esc(JSON.stringify(JSON.parse(tr.getAttribute("data-detail")), null, 2)) + "</pre>"); }
        catch (e) { openDrawer("Audit row", "<pre>" + esc(tr.getAttribute("data-detail")) + "</pre>"); }
      });
    });
  }

  function renderPolicies(rows) {
    var el = qs("#policies-table");
    if (!el) return;
    if (!rows.length) { el.innerHTML = "<p class='muted'>No policies.</p>"; return; }
    var html = "<table class='data-table'><thead><tr><th>name</th><th>kind</th><th></th></tr></thead><tbody>";
    rows.forEach(function (r) {
      html += "<tr><td class='mono'>" + esc(r.name) + "</td><td>" + esc(r.kind) + "</td>" +
        "<td><button type='button' class='chip-btn' data-show-policy='" + escAttr(r.name) + "'>View</button> " +
        "<button type='button' class='chip-btn' data-plan-policy='" + escAttr(r.name) + "'>Plan</button></td></tr>";
    });
    el.innerHTML = html + "</tbody></table>";
    qsa("[data-show-policy]").forEach(function (b) {
      b.addEventListener("click", function () { runAction("show_policy", { name: b.getAttribute("data-show-policy") }, true); });
    });
    qsa("[data-plan-policy]").forEach(function (b) {
      b.addEventListener("click", function () { openActionModal("policy_plan", { policy: b.getAttribute("data-plan-policy") }); });
    });
  }

  function renderSchedules(rows) {
    var el = qs("#schedules-table");
    if (!el) return;
    if (!rows.length) { el.innerHTML = "<p class='muted'>No schedules.</p>"; return; }
    if (rows[0] && rows[0].error) { el.innerHTML = "<p class='bad'>" + esc(rows[0].error) + "</p>"; return; }
    var hasRel = rows[0] && ("overdue" in rows[0] || "level" in rows[0]);
    var html = hasRel
      ? "<table class='data-table'><thead><tr><th>name</th><th>label</th><th>installed</th><th>loaded</th><th>last_exit</th><th>overdue</th><th>level</th></tr></thead><tbody>"
      : "<table class='data-table'><thead><tr><th>name</th><th>label</th><th>installed</th></tr></thead><tbody>";
    rows.forEach(function (r) {
      if (hasRel) {
        html += "<tr><td class='mono'>" + esc(r.name) + "</td><td>" + esc(r.label) + "</td>" +
          "<td>" + (r.installed ? "<span class='ok'>yes</span>" : "<span class='muted'>no</span>") + "</td>" +
          "<td>" + (r.loaded == null ? "—" : (r.loaded ? "yes" : "no")) + "</td>" +
          "<td class='mono'>" + (r.last_exit_status == null ? "—" : r.last_exit_status) + "</td>" +
          "<td>" + (r.overdue == null ? "—" : (r.overdue ? "<span class='warn'>yes</span>" : "no")) + "</td>" +
          "<td>" + esc(r.level || "—") + "</td></tr>";
      } else {
        html += "<tr><td class='mono'>" + esc(r.name) + "</td><td>" + esc(r.label) + "</td>" +
          "<td>" + (r.installed ? "<span class='ok'>yes</span>" : "<span class='muted'>no</span>") + "</td></tr>";
      }
    });
    el.innerHTML = html + "</tbody></table>";
  }

  var queuesLoaded = false;
  function loadQueues(force) {
    if (queuesLoaded && !force) return;
    var el = qs("#queues-body");
    var spark = qs("#queues-sparklines");
    if (el) el.innerHTML = '<div class="skeleton"></div>';
    fetch("/api/queues", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        queuesLoaded = true;
        if (!data.ok) {
          if (el) el.innerHTML = "<p class='bad'>" + esc(data.error || "queues failed") + "</p>";
          return;
        }
        var html = "";
        html += "<h4>Open plans (" + (data.open_plans || []).length + ")</h4>";
        var plans = data.plans || [];
        if (!plans.length) {
          html += "<p class='muted'>No registered plans. Run <code>steward policy plan</code> or Ops → Generate plan.</p>";
        } else {
          html += "<table class='data-table'><thead><tr><th>plan_id</th><th>status</th><th>policy</th><th class='num'>rows</th><th class='num'>bytes</th><th>blocked</th><th></th></tr></thead><tbody>";
          plans.forEach(function (p) {
            var blocked = (p.blocked_reasons || []).join(", ") || "—";
            var pid = String(p.plan_id || "");
            html += "<tr data-plan-id=\"" + esc(pid) + "\"><td class='mono'>" + esc(pid.slice(0, 14)) + (pid.length > 14 ? "…" : "") + "</td>" +
              "<td>" + esc(p.status) + "</td><td class='mono'>" + esc(p.policy) + "</td>" +
              "<td class='num'>" + fmtInt(p.rows_total || 0) + "</td>" +
              "<td class='num'>" + fmtBytes(p.estimated_bytes || 0) + "</td>" +
              "<td>" + esc(blocked) + "</td>" +
              "<td><button type='button' class='chip-btn plan-detail-btn' data-plan-id=\"" + esc(pid) + "\">Detail</button> " +
              "<button type='button' class='chip-btn plan-filter-btn' data-manifest=\"" + esc(String(p.manifest_path || p.path || "")) + "\">Filter dual</button></td></tr>";
          });
          html += "</tbody></table>";
          html += "<p class='muted'>Detail opens the plan record. Filter dual runs dual-presence bucketing on the plan TSV (writes artefacts only).</p>";
        }
        var st = data.stash || {};
        html += "<h4>In-flight stash</h4><p>entries: <strong>" + fmtInt(st.in_flight_entries || 0) +
          "</strong> · runs: <strong>" + fmtInt(st.distinct_run_ids || 0) + "</strong></p>";
        var od = data.overdue_schedules || [];
        html += "<h4>Overdue schedules (" + od.length + ")</h4>";
        if (!od.length) {
          html += "<p class='muted'>None flagged (cheap path may skip launchctl probe).</p>";
        } else {
          html += "<ul>";
          od.forEach(function (s) {
            html += "<li class='mono'>" + esc(s.name) + " — " + esc(s.message || "overdue") + "</li>";
          });
          html += "</ul>";
        }
        if (el) el.innerHTML = html;
        qsa(".plan-detail-btn", el).forEach(function (b) {
          b.addEventListener("click", function (ev) {
            ev.stopPropagation();
            var id = b.getAttribute("data-plan-id") || "";
            if (!id) return;
            runAction("plan_show", { plan_id: id }, true);
          });
        });
        qsa(".plan-filter-btn", el).forEach(function (b) {
          b.addEventListener("click", function (ev) {
            ev.stopPropagation();
            var m = b.getAttribute("data-manifest") || "";
            if (!m) { toast("Plan has no manifest_path — open Detail", "err"); return; }
            runAction("filter_plan_dual_presence", { manifest_path: m, intent: "cloud_retire" }, true);
          });
        });
      })
      .catch(function (e) {
        if (el) el.innerHTML = "<p class='bad'>Failed to load queues: " + e + "</p>";
      });
    fetch("/api/health/series?limit=48", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!spark) return;
        var series = (data && data.series) || [];
        if (!series.length) {
          spark.innerHTML = "<span class='muted'>No health snapshot history yet — run <code>steward health check --write-snapshot</code>.</span>";
          return;
        }
        var levels = series.map(function (p) { return p.overall || "?"; });
        spark.innerHTML = "<span class='muted'>Health series n=" + series.length +
          " · latest overall=<strong>" + esc(levels[levels.length - 1] || "—") +
          "</strong> · points: " + esc(levels.slice(-12).join(" → ")) + "</span>";
      })
      .catch(function () {
        if (spark) spark.innerHTML = "<span class='muted'>health series unavailable</span>";
      });
  }

  function loadFleet() {
    var el = qs("#fleet-body");
    if (!el) return;
    el.innerHTML = '<div class="skeleton"></div><p class="muted">Loading fleet matrix…</p>';
    var q = "?include_imports=1";
    fetch("/api/fleet" + q, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          el.innerHTML = "<p class='bad'>" + esc(data.error || "fleet failed") + "</p>";
          return;
        }
        var rows = data.rows || [];
        var html = "<p>overall: <strong class='" + (data.overall === "ok" ? "ok" : (data.overall === "fail" ? "bad" : "warn")) + "'>" +
          esc(data.overall || "?") + "</strong> · machines " + rows.length +
          " · elapsed " + (data.elapsed_ms || "?") + "ms · <code>steward machines health</code></p>";
        if (data.envelope_sla) {
          var sla = data.envelope_sla;
          html += "<p class='muted'>envelope SLA: level=" + esc(String(sla.level || sla.overall || "—")) +
            " · local_export_age_h=" + esc(String(sla.local_export_age_hours != null ? sla.local_export_age_hours : "—")) + "</p>";
        }
        if (!rows.length) {
          html += "<p class='muted'>No fleet rows. Attach imports with <code>steward db import</code>.</p>";
        } else {
          html += "<table class='data-table'><thead><tr>" +
            "<th>machine</th><th>source</th><th>scan</th><th class='num'>claims</th><th>chain</th><th>envelope</th><th>level</th></tr></thead><tbody>";
          rows.forEach(function (r) {
            var mid = String(r.machine_id || r.hostname || "—");
            html += "<tr><td class='mono'>" + esc(mid.slice(0, 12)) + (mid.length > 12 ? "…" : "") + "</td>" +
              "<td>" + esc(r.source || "—") + "</td>" +
              "<td class='mono'>" + esc(r.last_scan_finished_at || r.scan_finished_at || "—") + "</td>" +
              "<td class='num'>" + fmtInt(r.claim_count || r.current_claims || 0) + "</td>" +
              "<td>" + esc(r.chain_level || r.chain_status || "—") + "</td>" +
              "<td>" + esc(r.envelope_level || "—") + "</td>" +
              "<td>" + esc(r.scan_level || r.level || "—") + "</td></tr>";
          });
          html += "</tbody></table>";
        }
        if ((data.notes || []).length) {
          html += "<p class='muted'>" + esc((data.notes || []).join(" · ")) + "</p>";
        }
        el.innerHTML = html;
      })
      .catch(function (e) {
        el.innerHTML = "<p class='bad'>Fleet failed: " + esc(String(e)) + "</p>";
      });
  }

  function loadStatsOverview() {
    var el = qs("#stats-body");
    if (!el) return;
    el.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><p class="muted">Aggregating inventory — may take a while on multi-GB DBs…</p>';
    var q = includeImports ? "?include_imports=1" : "";
    fetch("/api/stats" + q, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) { el.innerHTML = "<p class='bad'>" + esc(data.error || "stats failed") + "</p>"; return; }
        var ov = data.overview || {};
        var html = "<p>Total bytes: <strong class='mono'>" + fmtBytes(ov.total_bytes || 0) +
          "</strong> · duplicates: <strong>" + fmtInt(ov.duplicate_count || 0) +
          "</strong> · elapsed " + (data.elapsed_ms || "?") + "ms</p>";
        html += renderStatTable("Top tiers", ov.top_tiers || [], ["tier","claim_count","permanode_count","total_bytes"]);
        html += renderStatTable("Top domains", ov.top_domains || [], ["domain","claim_count","permanode_count","total_bytes"]);
        if (ov.largest_permanode) {
          var lp = ov.largest_permanode;
          html += "<h3>Largest permanode</h3><p class='mono'>" + esc(lp.canonical_hash) +
            " · " + fmtBytes(lp.size_bytes) + " · " + lp.current_claim_count + " claims</p>";
        }
        el.innerHTML = html;
      })
      .catch(function (e) { el.innerHTML = "<p class='bad'>" + esc(String(e)) + "</p>"; });
  }

  function renderStatTable(title, rows, cols) {
    if (!rows.length) return "<h3>" + title + "</h3><p class='muted'>none</p>";
    var html = "<h3>" + title + "</h3><table class='data-table'><thead><tr>";
    cols.forEach(function (c) { html += "<th" + (c.indexOf("count") >= 0 || c.indexOf("bytes") >= 0 ? " class='num'" : "") + ">" + c + "</th>"; });
    html += "</tr></thead><tbody>";
    rows.forEach(function (r) {
      html += "<tr>";
      cols.forEach(function (c) {
        var v = r[c];
        if (c.indexOf("bytes") >= 0) v = fmtBytes(v || 0);
        else if (typeof v === "number") v = fmtInt(v);
        else if (v == null) v = "(none)";
        html += "<td" + (c.indexOf("count") >= 0 || c.indexOf("bytes") >= 0 ? " class='num'" : "") + ">" + esc(String(v)) + "</td>";
      });
      html += "</tr>";
    });
    return html + "</tbody></table>";
  }

  function renderDualPresenceBody(res) {
    if (!res || res.ok === false) {
      return "<p class='bad'>" + esc((res && res.error) || "sample failed") + "</p>";
    }
    var kinds = ["dual","store_only","mount_only","missing_store","conflict_name_path",
      "outside_store_root","mount_error","unknown"];
    var counts = res.counts || res.by_kind || res.kind_counts || null;
    if (!counts) {
      counts = {};
      kinds.forEach(function (k) { if (res[k] != null) counts[k] = res[k]; });
    }
    var html2 = "<p class='muted'>sampled=" + esc(String(res.sampled || res.sample_size || res.n || "?")) +
      " · intent=" + esc(String(res.intent || "observe")) +
      (res.cloud_safe_sample_ratio != null ? " · cloud_safe_ratio=" + esc(String(res.cloud_safe_sample_ratio)) : "") +
      " · CLI: <code>steward fp dual-presence</code></p>";
    var keys = Object.keys(counts).filter(function (k) { return counts[k] != null; });
    if (keys.length) {
      html2 += "<table class='data-table'><thead><tr><th>kind</th><th class='num'>count</th></tr></thead><tbody>";
      keys.sort().forEach(function (k) {
        html2 += "<tr><td class='mono'>" + esc(k) + "</td><td class='num'>" + fmtInt(counts[k]) + "</td></tr>";
      });
      html2 += "</tbody></table>";
    } else {
      html2 += "<pre>" + esc(JSON.stringify(res, null, 2)) + "</pre>";
    }
    return html2;
  }

  function loadFp() {
    var el = qs("#fp-body");
    if (!el) return;
    el.innerHTML = '<div class="skeleton"></div><p class="muted">Probing FP…</p>';
    fetch("/api/fp", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (fp) {
        var html = "<div class='toolbar'>" +
          "<button type='button' class='chip-btn' id='fp-refresh'>Reload FP</button> " +
          "<button type='button' class='chip-btn' id='fp-dual-sample'>Dual-presence sample</button>" +
          "</div>";
        html += "<h4>File Provider status</h4><pre>" + esc(JSON.stringify(fp, null, 2)) + "</pre>";
        html += "<h4>Dual-presence sample</h4><div id='fp-dual-body'><p class='muted'>Loading sample…</p></div>";
        el.innerHTML = html;
        var br = qs("#fp-refresh");
        if (br) br.addEventListener("click", function () { loadFp(); });
        var bd = qs("#fp-dual-sample");
        if (bd) bd.addEventListener("click", function () {
          var box = qs("#fp-dual-body");
          if (box) box.innerHTML = "<p class='muted'>Sampling…</p>";
          runAction("dual_presence_sample", { sample: 48 }, false).then(function (res) {
            var b = qs("#fp-dual-body");
            if (b) b.innerHTML = renderDualPresenceBody(res);
          });
        });
        runAction("dual_presence_sample", { sample: 32 }, false).then(function (res) {
          var b = qs("#fp-dual-body");
          if (b) b.innerHTML = renderDualPresenceBody(res);
        });
      })
      .catch(function (e) { el.innerHTML = "<p class='bad'>" + esc(String(e)) + "</p>"; });
  }

  function runSearch() {
    var q = (qs("#inspect-q") || {}).value || "";
    var mode = (qs("#inspect-mode") || {}).value || "path";
    if (!q.trim()) { toast("Enter a path or hash", "err"); return; }
    var action = mode === "hash" ? "search_hash" : (mode === "inspect" ? "inspect" : "search_path");
    var params = mode === "inspect" ? { target: q.trim() } : { q: q.trim(), limit: 40 };
    runAction(action, params, true);
  }

  // ── drawer / modal ────────────────────────────────────
  function openDrawer(title, bodyHtml) {
    if (!drawer) return;
    drawerTitle.textContent = title;
    drawerBody.innerHTML = bodyHtml;
    drawer.classList.add("open");
    drawerBack.classList.add("open");
    document.body.classList.add("drawer-open");
  }
  function closeDrawer() {
    drawer.classList.remove("open");
    drawerBack.classList.remove("open");
    document.body.classList.remove("drawer-open");
  }

  function openActionModal(actionId, seed) {
    seed = seed || {};
    ensureActions().then(function (actions) {
      var meta = actions.filter(function (a) { return a.id === actionId; })[0];
      if (!meta) { toast("Unknown action " + actionId, "err"); return; }
      qs("#modal-title").textContent = meta.label;
      qs("#modal-desc").textContent = meta.description || "";
      var fields = qs("#modal-fields");
      fields.innerHTML = "";
      (meta.params || []).forEach(function (p) {
        var name = typeof p === "string" ? p : p.name;
        var def = typeof p === "string" ? "" : (p.default != null ? p.default : "");
        if (seed[name] != null) def = seed[name];
        var row = document.createElement("div");
        row.className = "row";
        row.innerHTML = "<label for='mf-" + name + "'>" + name + "</label>" +
          "<input id='mf-" + name + "' name='" + name + "' type='text' value='" + escAttr(String(def)) + "'/>";
        fields.appendChild(row);
      });
      var confirmRow = qs("#modal-confirm-row");
      if (meta.destructive) {
        confirmRow.style.display = "block";
        qs("#mf-confirm").value = "";
      } else {
        confirmRow.style.display = "none";
      }
      modalBack.dataset.action = actionId;
      modalBack.dataset.destructive = meta.destructive ? "1" : "0";
      modalBack.classList.add("open");
      if (actionResult) actionResult.textContent = "";
    });
  }

  function closeModal() { modalBack.classList.remove("open"); }

  function submitModal() {
    var actionId = modalBack.dataset.action;
    var params = {};
    qsa("#modal-fields input").forEach(function (inp) { params[inp.name] = inp.value; });
    if (modalBack.dataset.destructive === "1") {
      params.confirm = (qs("#mf-confirm") || {}).value || "";
    }
    runAction(actionId, params, true).then(function () { /* keep modal open to show result */ });
  }

  function ensureActions() {
    if (actionsCache) return Promise.resolve(actionsCache);
    return fetch("/api/actions", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        actionsCache = data.actions || [];
        return actionsCache;
      });
  }

  function loadActionsRail() {
    ensureActions().then(function (actions) {
      var rail = qs("#ops-actions");
      if (!rail) return;
      var groups = {};
      actions.forEach(function (a) {
        if (a.id === "cli_hint") return;
        (groups[a.group] = groups[a.group] || []).push(a);
      });
      var html = "";
      Object.keys(groups).forEach(function (g) {
        html += "<div class='act-group'><h3>" + esc(g) + "</h3>";
        groups[g].forEach(function (a) {
          var cls = "act-btn" + (a.destructive ? " danger" : "") + (a.slow ? " slow" : "");
          html += "<button type='button' class='" + cls + "' data-action='" + escAttr(a.id) + "'>" + esc(a.label) + "</button>";
        });
        html += "</div>";
      });
      rail.innerHTML = html;
      qsa("[data-action]", rail).forEach(function (b) {
        b.addEventListener("click", function () { openActionModal(b.getAttribute("data-action")); });
      });
    });
  }

  function formatActionBodyHtml(actionId, body) {
    var pretty = JSON.stringify(body, null, 2);
    var html = "<pre>" + esc(pretty) + "</pre>";
    var h = body && body.execute_handoff;
    if (h && actionId === "apply_dry_run") {
      html = "<div class='handoff-card'>" +
        "<h4>Execute handoff (not in GUI)</h4>" +
        "<p class='muted'>" + esc(h.reason || "") + "</p>" +
        "<p><strong>CLI</strong></p><pre class='handoff-cmd'>" + esc(h.cli || "") + "</pre>" +
        (h.cli_dry_run ? "<p class='muted'>Re-check: <code>" + esc(h.cli_dry_run) + "</code></p>" : "") +
        (h.plan_token
          ? "<p><strong>MCP plan_token</strong> (one-shot, copy carefully)</p>" +
            "<pre class='handoff-cmd'>" + esc(h.plan_token) + "</pre>" +
            "<p class='muted'>expires " + esc(h.plan_token_expires_at || "?") +
            " · tool <code>apply_execute</code> with STEWARD_MCP_MODE=write</p>"
          : "<p class='muted'>" + esc(h.note || "No plan_token on this dry-run.") + "</p>") +
        "</div>" + html;
    }
    return html;
  }

  function runAction(actionId, params, showInDrawer) {
    if (actionResult) actionResult.textContent = "Running " + actionId + "…";
    return fetch("/api/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: actionId, params: params || {} }),
    })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
      .then(function (res) {
        var pretty = JSON.stringify(res.body, null, 2);
        if (actionResult) actionResult.textContent = pretty;
        if (showInDrawer) openDrawer(actionId, formatActionBodyHtml(actionId, res.body || {}));
        if (res.body && res.body.ok) {
          toast(actionId + " ok", "ok");
          if (actionId === "refresh_rollups" || actionId === "status_full" || actionId === "refresh_health") pollOnce();
        } else {
          toast((res.body && res.body.error) || (actionId + " failed"), "err");
        }
        return res.body;
      })
      .catch(function (e) {
        toast(String(e), "err");
        if (actionResult) actionResult.textContent = String(e);
      });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escAttr(s) { return esc(s).replace(/'/g, "&#39;"); }

  // ── wire UI ───────────────────────────────────────────
  qsa(".tab").forEach(function (t) {
    t.addEventListener("click", function () { showPanel(t.getAttribute("data-panel")); });
  });
  qsa(".card-expand, .kpi").forEach(function (b) {
    b.addEventListener("click", function () {
      var panel = b.getAttribute("data-expand") || b.getAttribute("data-open-panel") || b.closest(".card") && b.closest(".card").getAttribute("data-panel");
      if (panel) showPanel(panel);
    });
  });
  var pauseBtn = qs("#btn-pause");
  if (pauseBtn) {
    pauseBtn.addEventListener("click", function () {
      paused = !paused;
      pauseBtn.classList.toggle("active", paused);
      pauseBtn.textContent = paused ? "Resume live" : "Pause live";
      if (live) live.classList.toggle("paused", paused);
      if (liveLabel) liveLabel.textContent = paused ? "paused" : ("live · " + refresh + "s");
      schedulePoll();
    });
  }
  var refreshBtn = qs("#btn-refresh-now");
  if (refreshBtn) refreshBtn.addEventListener("click", function () { pollOnce(); loadAnalysis(true); toast("Refreshed"); });
  var railToggle = qs("#btn-ops");
  if (railToggle) railToggle.addEventListener("click", function () {
    var rail = qs(".ops-rail");
    if (rail) rail.classList.toggle("open");
    loadActionsRail();
  });
  if (drawerBack) drawerBack.addEventListener("click", closeDrawer);
  var drawerClose = qs("#drawer-close");
  if (drawerClose) drawerClose.addEventListener("click", closeDrawer);
  var modalCancel = qs("#modal-cancel");
  if (modalCancel) modalCancel.addEventListener("click", closeModal);
  var modalRun = qs("#modal-run");
  if (modalRun) modalRun.addEventListener("click", submitModal);
  if (modalBack) modalBack.addEventListener("click", function (e) { if (e.target === modalBack) closeModal(); });
  var searchBtn = qs("#inspect-go");
  if (searchBtn) searchBtn.addEventListener("click", runSearch);
  var searchInput = qs("#inspect-q");
  if (searchInput) searchInput.addEventListener("keydown", function (e) { if (e.key === "Enter") runSearch(); });

  // Stats axis buttons (incl. cross matrix)
  function loadStatsAxis(axis) {
    var el = qs("#stats-body");
    if (!el) return;
    var prefixEl = qs("#stats-path-prefix");
    var pathPrefix = ((prefixEl && prefixEl.value) || surfaceFilterPrefix || "").trim();
    var slowAxes = { tier: 1, domain: 1, volume: 1, cross: 1, extensions: 1, classifications: 1, duplicates: 1 };
    if (!pathPrefix && slowAxes[axis]) {
      el.innerHTML = '<div class="skeleton"></div><p class="muted">Loading <strong>' + esc(axis) +
        "</strong> unscoped — multi‑GB inventories may take minutes. Prefer path_prefix…</p>";
    } else {
      el.innerHTML = '<div class="skeleton"></div><p class="muted">Loading ' + esc(axis) + "…</p>";
    }
    var q = "?axis=" + encodeURIComponent(axis) + (includeImports ? "&include_imports=1" : "");
    var limitEl = qs("#stats-limit");
    var limit = limitEl && limitEl.value ? limitEl.value : "40";
    q += "&limit=" + encodeURIComponent(limit);
    if (pathPrefix) q += "&path_prefix=" + encodeURIComponent(pathPrefix);
    if (axis === "cross") {
      var da = (qs("#stats-dim-a") || {}).value || "domain";
      var db = (qs("#stats-dim-b") || {}).value || "";
      q += "&dim_a=" + encodeURIComponent(da);
      if (db) q += "&dim_b=" + encodeURIComponent(db);
    }
    fetch("/api/stats" + q, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!el) return;
        if (!data.ok) { el.innerHTML = "<p class='bad'>" + esc(data.error || "failed") + "</p>"; return; }
        var rows = data.rows || data.cells || [];
        if (!rows.length) { el.innerHTML = "<p class='muted'>No rows</p>"; return; }
        var cols = Object.keys(rows[0]);
        var html = "<p class='muted'>" + rows.length + " rows · " + (data.elapsed_ms || "?") + "ms" +
          (data.truncated ? " · <span class='warn'>truncated</span>" : "") +
          (pathPrefix ? " · path_prefix=<code>" + esc(pathPrefix) + "</code>" : "") +
          "</p><table class='data-table'><thead><tr>";
        cols.forEach(function (c) { html += "<th>" + esc(c) + "</th>"; });
        html += "</tr></thead><tbody>";
        rows.forEach(function (r) {
          html += "<tr>";
          cols.forEach(function (c) {
            var v = r[c];
            if (String(c).indexOf("bytes") >= 0 && typeof v === "number") v = fmtBytes(v);
            else if (typeof v === "number") v = fmtInt(v);
            html += "<td class='mono'>" + esc(String(v == null ? "—" : v)) + "</td>";
          });
          html += "</tr>";
        });
        el.innerHTML = html + "</tbody></table>";
      });
  }
  qsa("[data-stats-axis]").forEach(function (b) {
    b.addEventListener("click", function () {
      loadStatsAxis(b.getAttribute("data-stats-axis"));
    });
  });
  var statsCrossBtn = qs("#stats-cross-run");
  if (statsCrossBtn) statsCrossBtn.addEventListener("click", function () { loadStatsAxis("cross"); });
  var surfaceColor = qs("#surface-color");
  if (surfaceColor) surfaceColor.addEventListener("change", function () { loadSurface(); });
  var surfaceReload = qs("#surface-reload");
  if (surfaceReload) surfaceReload.addEventListener("click", function () { loadSurface(); });
  var surfaceUp = qs("#surface-up");
  if (surfaceUp) surfaceUp.addEventListener("click", function () {
    if (!surfacePrefix) return;
    var i = surfacePrefix.lastIndexOf("/");
    surfacePrefix = i > 0 ? surfacePrefix.slice(0, i) : "";
    loadSurface();
  });
  var surfaceFilterBtn = qs("#surface-filter-stats");
  if (surfaceFilterBtn) surfaceFilterBtn.addEventListener("click", function () {
    var prefEl = qs("#stats-path-prefix");
    if (prefEl) prefEl.value = surfacePrefix || "";
    applySurfaceFilter(surfacePrefix);
    showPanel("stats");
  });
  var fleetReload = qs("#fleet-reload");
  if (fleetReload) fleetReload.addEventListener("click", function () { loadFleet(); });
  var ra = qs("#btn-reload-analysis");
  if (ra) ra.addEventListener("click", function () { analysisLoaded = false; loadAnalysis(true); showPanel("scans"); });

  // Initial analysis for default panel
  showPanel("overview");
  loadActionsRail();
  schedulePoll();
  if (liveLabel && refresh > 0) liveLabel.textContent = "live · " + refresh + "s";
})();
"""


def render_status_html(
    report: StatusReport,
    *,
    refresh_seconds: int = 30,
    rendered_at: datetime | None = None,
    include_imports: bool = False,
    quick: bool = True,
    posture: dict[str, Any] | None = None,
) -> str:
    """Render a :class:`StatusReport` as a complete interactive HTML document."""
    ts = rendered_at or datetime.now()
    # Meta refresh ONLY for no-JS browsers — prevents full-page blanking.
    noscript_refresh = (
        f'<noscript><meta http-equiv="refresh" content="{int(refresh_seconds)}"></noscript>'
        if refresh_seconds > 0
        else ""
    )

    sections = [
        _render_inventory(report),
        _render_latest_scan(report),
        _render_stash(report),
        _render_adapter(report, attr="last_replicate", title="last replicate", index=3),
        _render_adapter(report, attr="last_archive", title="last archive", index=4),
        _render_audit(report),
    ]

    if posture is None:
        try:
            from steward.infra.dashboard.api import posture_from_status_report

            posture = posture_from_status_report(report, quick=quick)
        except Exception as exc:  # noqa: BLE001 — banner is best-effort
            from steward.infra.observability import log_swallowed_error

            log_swallowed_error("dashboard.render.posture", exc, context={"quick": quick})
            posture = None

    posture_banner = _render_posture_banner(posture)

    # Keep legacy audit-only banner when posture missing but chain broken.
    audit_banner = ""
    if posture is None and not report.audit_chain.ok:
        audit_banner = (
            '<div class="card audit-banner">'
            '<strong class="bad">audit chain broken:</strong> '
            f"{_esc(report.audit_chain.error)}"
            "</div>"
        )

    if include_imports:
        scope = (
            '<nav class="scope" aria-label="Machine scope">'
            '<a href="/" title="switch to local only">local only</a>'
            '<a class="active" href="/?include_imports=1">all machines</a>'
            "</nav>"
            '<span class="visually-hidden">scope: all machines · switch to local only</span>'
        )
    else:
        scope = (
            '<nav class="scope" aria-label="Machine scope">'
            '<a class="active" href="/">local only</a>'
            '<a href="/?include_imports=1">include attached</a>'
            "</nav>"
            '<span class="visually-hidden">scope: local only</span>'
        )

    refresh_label = f"{int(refresh_seconds)}s" if refresh_seconds > 0 else "off"
    if refresh_seconds > 0:
        live_html = (
            f'<span class="live" id="live-status" title="Soft-refresh interval (no full reload)">'
            f'<span class="live-dot" aria-hidden="true"></span>'
            f'<span id="live-label">live · {html.escape(refresh_label)}</span></span>'
        )
    else:
        live_html = (
            '<span class="live stale" id="live-status">'
            '<span class="live-dot"></span><span id="live-label">refresh off</span></span>'
        )

    mode_chip = "quick" if quick else "full"
    mode_href = "/?full=1" if quick else "/"
    if include_imports:
        mode_href += ("&" if "?" in mode_href else "?") + "include_imports=1"

    seed = {
        "inventory": {
            "permanodes": report.inventory.permanodes,
            "current_claims": report.inventory.current_claims,
            "scan_runs": report.inventory.scan_runs,
            "audit_entries": report.inventory.audit_entries,
            "machines": report.inventory.machines,
        },
        "quick": quick,
        "include_imports": include_imports,
        "posture": {
            "overall": (posture or {}).get("overall"),
            "score": (posture or {}).get("score"),
        },
    }

    cfg = (
        f'id="steward-dash-config" hidden '
        f'data-refresh="{int(refresh_seconds)}" '
        f'data-include-imports="{1 if include_imports else 0}" '
        f'data-quick="{1 if quick else 0}" '
        f'data-seed="{html.escape(json.dumps(seed), quote=True)}"'
    )

    ts_str = html.escape(ts.isoformat(timespec="seconds"))
    script = f"<script>{_JS}</script>" if refresh_seconds >= 0 else ""

    # Bandit B608 false positive: HTML/JS template is not SQL.
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Steward — status</title>
{noscript_refresh}
<style>{_CSS}</style>
</head>
<body>
<div class="app">
  <div class="main-col">
    <header>
      <div class="brand">
        <div class="mark" aria-hidden="true">S</div>
        <div>
          <h1>Steward — status</h1>
          <p class="sub">operator console · analysis · actions</p>
        </div>
      </div>
      <div class="meta-block">
        {live_html}
        <button type="button" class="chip-btn" id="btn-pause">Pause live</button>
        <button type="button" class="chip-btn" id="btn-refresh-now">Refresh now</button>
        <button type="button" class="chip-btn" id="btn-ops">Ops</button>
        <a class="chip-btn" href="{html.escape(mode_href)}">{html.escape(mode_chip)} mode</a>
        <span>rendered <time id="rendered-at">{ts_str}</time> · soft-refresh {html.escape(refresh_label)}</span>
        {scope}
      </div>
    </header>
    {posture_banner}
    {audit_banner}
    {_render_kpis(report)}

    <nav class="panel-tabs" aria-label="Analysis surfaces">
      <button type="button" class="tab active" data-panel="overview">Overview</button>
      <button type="button" class="tab" data-panel="scans">Scans</button>
      <button type="button" class="tab" data-panel="audit">Audit</button>
      <button type="button" class="tab" data-panel="stats">Stats</button>
      <button type="button" class="tab" data-panel="surface">Surface</button>
      <button type="button" class="tab" data-panel="inspect">Inspector</button>
      <button type="button" class="tab" data-panel="policies">Policies</button>
      <button type="button" class="tab" data-panel="queues">Queues</button>
      <button type="button" class="tab" data-panel="schedules">Schedules</button>
      <button type="button" class="tab" data-panel="fleet">Fleet</button>
      <button type="button" class="tab" data-panel="fp">File Provider</button>
      <button type="button" class="tab" data-panel="ops">Ops console</button>
    </nav>

    <section class="panel active" id="panel-overview">
      <h3>System overview</h3>
      <p class="muted">Ops console (not full CLI parity): posture, exploration (stats/surface/fleet), plan
      hygiene, and gated adapter actions. <strong>apply --execute</strong> stays CLI/MCP after dry-run
      handoff. Existing ops-rail EXECUTE actions (replicate/archive/stash) remain. Soft-poll metrics +
      posture — no full reload. JSON: <code>/api/health</code>.</p>
      <div id="analysis-tables"></div>
    </section>
    <section class="panel" id="panel-scans">
      <h3>Recent scan runs</h3>
      <div class="toolbar">
        <button type="button" class="chip-btn" id="btn-reload-analysis">Reload</button>
        <span class="muted">from audit / scan_runs · click row root to inspect</span>
      </div>
      <div id="scans-table"><div class="skeleton"></div></div>
    </section>
    <section class="panel" id="panel-audit">
      <h3>Audit log tail</h3>
      <div id="audit-table"><div class="skeleton"></div></div>
    </section>
    <section class="panel" id="panel-stats">
      <h3>Inventory stats</h3>
      <p class="muted">Aggregations over claims (ADR-0022 matrix). Surface selection fills path_prefix.</p>
      <p class="perf-hint" id="stats-perf-hint">Multi‑GB tip: set <strong>path_prefix</strong> (e.g. <code>/Volumes/Backup</code>)
      before tier/domain/cross. Unscoped pivots on multi‑million claim DBs can take 1–2+ minutes.
      Prefer <code>steward status --quick</code> rollups for headline counts.</p>
      <div class="toolbar">
        <button type="button" class="chip-btn" data-stats-axis="tier">By tier</button>
        <button type="button" class="chip-btn" data-stats-axis="domain">By domain</button>
        <button type="button" class="chip-btn" data-stats-axis="volume">By volume</button>
        <button type="button" class="chip-btn" data-stats-axis="extensions">Extensions</button>
        <button type="button" class="chip-btn" data-stats-axis="classifications">Classifications</button>
        <button type="button" class="chip-btn" data-stats-axis="duplicates">Duplicates</button>
      </div>
      <div class="toolbar">
        <label class="muted">path_prefix <input type="text" id="stats-path-prefix" placeholder="/Volumes/Backup (recommended)" style="min-width:16rem" /></label>
        <label class="muted">limit <input type="number" id="stats-limit" value="40" min="1" max="500" style="width:5rem" /></label>
        <label class="muted">cross dim_a
          <select id="stats-dim-a" class="chip-btn" style="border-radius:10px;padding:6px 8px">
            <option value="domain">domain</option>
            <option value="tier">tier</option>
            <option value="volume">volume</option>
            <option value="extension">extension</option>
            <option value="classification">classification</option>
            <option value="machine_id">machine_id</option>
          </select>
        </label>
        <label class="muted">dim_b
          <select id="stats-dim-b" class="chip-btn" style="border-radius:10px;padding:6px 8px">
            <option value="">(none)</option>
            <option value="extension">extension</option>
            <option value="tier">tier</option>
            <option value="domain">domain</option>
            <option value="volume">volume</option>
            <option value="classification">classification</option>
          </select>
        </label>
        <button type="button" class="chip-btn active" id="stats-cross-run">Run cross</button>
      </div>
      <div id="stats-body"><p class="muted">Open this tab to load overview, or pick an axis / Run cross.</p></div>
    </section>
    <section class="panel" id="panel-surface">
      <h3>Inventory surface</h3>
      <p class="muted">Claim-based treemap (not live disk walk). Area ∝ bytes. Prefer prefix/tier/volume on multi‑GB DBs.
      JSON: <code>/api/surface</code> · CLI: <code>steward surface tree</code>.</p>
      <div class="toolbar">
        <label class="muted">Overlay
          <select id="surface-color" class="chip-btn" style="border-radius:10px;padding:8px 10px">
            <option value="none">none</option>
            <option value="domain">domain</option>
            <option value="extension">extension</option>
            <option value="tier">tier</option>
            <option value="source">source</option>
          </select>
        </label>
        <input type="text" id="surface-tier" placeholder="tier filter e.g. L2" style="max-width:10rem" />
        <input type="text" id="surface-volume" placeholder="volume filter" style="max-width:10rem" />
        <button type="button" class="chip-btn" id="surface-up">Up</button>
        <button type="button" class="chip-btn" id="surface-reload">Reload</button>
        <button type="button" class="chip-btn" id="surface-filter-stats">Filter stats</button>
      </div>
      <div id="surface-crumb" class="toolbar"></div>
      <div id="surface-legend" class="muted" style="margin:0 0 8px"></div>
      <div id="surface-map"><p class="muted">Open this tab to load the surface.</p></div>
      <p id="surface-notes" class="muted"></p>
    </section>
    <section class="panel" id="panel-inspect">
      <h3>Inspector</h3>
      <div class="toolbar">
        <select id="inspect-mode" class="chip-btn" style="border-radius:10px;padding:8px 10px">
          <option value="path">Path contains</option>
          <option value="hash">Hash prefix</option>
          <option value="inspect">Inspect target</option>
        </select>
        <input type="search" id="inspect-q" placeholder="path fragment, hash prefix, or permanode id…" />
        <button type="button" class="chip-btn active" id="inspect-go">Search</button>
      </div>
      <p class="muted">Results open in the detail drawer. Equivalent: <code>steward inspect</code> / MCP find tools.</p>
    </section>
    <section class="panel" id="panel-policies">
      <h3>Policies</h3>
      <div id="policies-table"><div class="skeleton"></div></div>
    </section>
    <section class="panel" id="panel-queues">
      <h3>Operator queues</h3>
      <p class="muted">Open plans, in-flight stash, and overdue schedules (ADR-0019).
      History charts use health snapshots from <code>/api/health/series</code>.</p>
      <div id="queues-sparklines" class="toolbar"></div>
      <div id="queues-body"><div class="skeleton"></div></div>
    </section>
    <section class="panel" id="panel-schedules">
      <h3>Launchd schedules</h3>
      <div id="schedules-table"><div class="skeleton"></div></div>
    </section>
    <section class="panel" id="panel-fleet">
      <h3>Fleet health matrix</h3>
      <p class="muted">Multi-machine freshness + envelope SLA (ADR-0021). API: <code>/api/fleet</code> ·
      CLI: <code>steward machines health</code>.</p>
      <div class="toolbar">
        <button type="button" class="chip-btn" id="fleet-reload">Reload fleet</button>
      </div>
      <div id="fleet-body"><p class="muted">Open this tab to load the fleet matrix.</p></div>
    </section>
    <section class="panel" id="panel-fp">
      <h3>Dropbox File Provider + dual-presence</h3>
      <p class="muted">Layout probe (<code>steward fp status</code>) and bounded dual-presence sample
      (<code>steward fp dual-presence</code> / ADR-0020). Filter plans from Queues or Ops.</p>
      <div id="fp-body"><p class="muted">Open tab to probe store/mount health.</p></div>
    </section>
    <section class="panel" id="panel-ops">
      <h3>Ops console</h3>
      <p class="muted">Actions also live in the right rail. Destructive adapter ops (replicate/archive/stash)
      still require typing <code>EXECUTE</code>. Loopback only. <strong>Apply execute</strong> is not a GUI
      action — use dry-run then the CLI/MCP handoff in the result drawer. Prefer dry-run / plan first.</p>
      <div id="ops-inline"></div>
      <pre id="action-result">Select an action…</pre>
    </section>

    <main class="grid">
{"".join(sections)}
    </main>
    <footer>
      read-only by default — mutations require confirmation ·
      <code>steward apply --execute</code> remains the CLI source of truth
    </footer>
  </div>

  <aside class="ops-rail" aria-label="Operator actions">
    <h2>Ops</h2>
    <p class="rail-sub">Loopback actions · dry-run first</p>
    <div id="ops-actions"><div class="skeleton"></div></div>
  </aside>
</div>

<div class="drawer-backdrop" id="drawer-backdrop"></div>
<aside class="drawer" id="drawer" aria-hidden="true">
  <div class="drawer-head">
    <h2 id="drawer-title">Detail</h2>
    <button type="button" class="chip-btn" id="drawer-close">Close</button>
  </div>
  <div class="drawer-body" id="drawer-body"></div>
</aside>

<div class="modal-backdrop" id="modal-backdrop">
  <div class="modal" role="dialog" aria-modal="true">
    <h2 id="modal-title">Action</h2>
    <p id="modal-desc"></p>
    <div id="modal-fields"></div>
    <div class="row" id="modal-confirm-row" style="display:none">
      <label for="mf-confirm">Type EXECUTE to confirm</label>
      <input id="mf-confirm" name="confirm" type="text" autocomplete="off" placeholder="EXECUTE" />
    </div>
    <div class="modal-actions">
      <button type="button" class="chip-btn" id="modal-cancel">Cancel</button>
      <button type="button" class="chip-btn active" id="modal-run">Run</button>
    </div>
  </div>
</div>

<div class="toast" id="toast" role="status"></div>
<div {cfg}></div>
{script}
</body>
</html>
"""  # nosec B608 — HTML/JS document template, not SQL


__all__ = ["render_status_html"]
