# SPDX-License-Identifier: Apache-2.0

"""Pure :class:`StatusReport` → HTML renderer.

Renders the same six-section dashboard the CLI's Rich tables show,
but as a single self-contained HTML page (inline CSS, no external
assets). The HTML is plain enough that an operator can save the page
and email it as evidence.

The renderer is a pure function — no I/O, no DB access. The server
layer fetches the :class:`StatusReport` via
:func:`steward.infra.status.collect_status` and hands it here.

Auto-refresh is wired via a ``<meta http-equiv="refresh">`` tag with
a configurable interval (default 30 s). Operators who want the page
to stop refreshing can hit a browser's stop button or pass
``refresh_seconds=0``.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from steward.infra.status import StatusReport, _format_bytes

# ─────────────────────── small helpers ──────────────────────────


def _esc(value: Any) -> str:
    """HTML-escape a value, treating ``None`` as ``—`` (em dash)."""
    if value is None:
        return "—"
    return html.escape(str(value))


def _row(label: str, value: str, *, css_class: str = "") -> str:
    """Render one labelled cell pair as a ``<tr>``."""
    cls = f' class="{css_class}"' if css_class else ""
    return f"<tr{cls}><th>{html.escape(label)}</th><td>{value}</td></tr>"


# ─────────────────────── per-section renderers ──────────────────────────


def _render_inventory(report: StatusReport) -> str:
    rows = [
        _row("permanodes", f"{report.inventory.permanodes:,}"),
        _row("current claims", f"{report.inventory.current_claims:,}"),
        _row("scan runs", f"{report.inventory.scan_runs:,}"),
        _row("audit entries", f"{report.inventory.audit_entries:,}"),
        _row("machines", f"{report.inventory.machines:,}"),
        _row(
            "db file",
            f"{_esc(report.db.path)} <span class='dim'>({_esc(_format_bytes(report.db.size_bytes))})</span>",
        ),
        _row("db modified", _esc(report.db.modified_iso)),
    ]
    return _section("inventory", rows)


def _render_latest_scan(report: StatusReport) -> str:
    s = report.latest_scan
    if s.scan_run_id is None:
        return _section("latest scan", [_row("status", "<em>no scans yet</em>")])
    rows = [
        _row("scan_run_id", _esc(s.scan_run_id)),
        _row("root", _esc(s.root_path)),
        _row("finished_at", _esc(s.finished_at)),
        _row("files_walked", f"{s.files_walked:,}"),
        _row("files_hashed", f"{s.files_hashed:,}"),
        _row("files_skipped", f"{s.files_skipped:,}"),
        _row("bytes_hashed", _esc(_format_bytes(s.bytes_hashed))),
    ]
    if s.errors:
        rows.append(
            _row("errors", f'<span class="bad">{s.errors:,}</span>'),
        )
    return _section("latest scan", rows)


def _render_stash(report: StatusReport) -> str:
    s = report.stash
    if s.in_flight_entries == 0:
        return _section(
            "stash",
            [_row("status", "<em>no in-flight stash entries</em>")],
        )
    rows = [
        _row("in_flight_entries", f"{s.in_flight_entries:,}"),
        _row("distinct_run_ids", f"{s.distinct_run_ids:,}"),
        _row("oldest", _esc(s.oldest_ts_iso)),
        _row("newest", _esc(s.newest_ts_iso)),
    ]
    return _section("stash", rows)


def _render_adapter(report: StatusReport, *, attr: str, title: str) -> str:
    run = getattr(report, attr)
    if run is None:
        return _section(
            title,
            [_row("status", "<em>no runs yet</em>")],
        )
    rows = [
        _row("timestamp", _esc(run.timestamp)),
    ]
    if run.policy_name:
        rows.append(_row("policy", _esc(run.policy_name)))
    if attr == "last_replicate":
        runs = int(run.payload.get("runs", 0) or 0)
        successes = int(run.payload.get("successes", 0) or 0)
        failures = int(run.payload.get("failures", 0) or 0)
        bytes_n = int(run.payload.get("bytes_transferred", 0) or 0)
        rows.append(_row("runs", f"{runs:,}"))
        rows.append(_row("successes", f"{successes:,}"))
        if failures:
            rows.append(
                _row("failures", f'<span class="bad">{failures:,}</span>'),
            )
        rows.append(_row("bytes_transferred", _esc(_format_bytes(bytes_n))))
    elif attr == "last_archive":
        runs = int(run.payload.get("runs", 0) or 0)
        successes = int(run.payload.get("successes", 0) or 0)
        failures = int(run.payload.get("failures", 0) or 0)
        bytes_n = int(run.payload.get("total_bytes_added", 0) or 0)
        rows.append(_row("runs", f"{runs:,}"))
        rows.append(_row("successes", f"{successes:,}"))
        if failures:
            rows.append(
                _row("failures", f'<span class="bad">{failures:,}</span>'),
            )
        rows.append(_row("total_bytes_added", _esc(_format_bytes(bytes_n))))
    return _section(title, rows)


def _render_audit(report: StatusReport) -> str:
    a = report.audit_chain
    rows = [
        _row("rows_checked", f"{a.rows_checked:,}"),
    ]
    if a.ok:
        rows.append(_row("status", '<span class="ok">ok</span>'))
    else:
        rows.append(
            _row("status", '<span class="bad">BROKEN</span>'),
        )
        if a.error:
            rows.append(_row("error", _esc(a.error)))
    return _section("audit chain", rows)


# ─────────────────────── frame ──────────────────────────


def _section(title: str, rows: list[str]) -> str:
    """Render one card-style section: header + rows table."""
    return f'<section class="card"><h2>{html.escape(title)}</h2><table><tbody>{"".join(rows)}</tbody></table></section>'


_CSS = """\
* { box-sizing: border-box; }
body {
  font: 14px/1.45 -apple-system, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
  margin: 0;
  padding: 24px;
  background: #f7f7f8;
  color: #1a1a1a;
}
header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 24px;
}
header h1 { margin: 0; font-size: 20px; font-weight: 600; }
header .meta { color: #6b6b73; font-size: 13px; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
}
.card {
  background: #fff;
  border-radius: 8px;
  border: 1px solid #e6e6ec;
  padding: 16px 20px;
}
.card h2 {
  margin: 0 0 12px 0;
  font-size: 14px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #6b6b73;
}
table { width: 100%; border-collapse: collapse; }
th {
  text-align: left;
  font-weight: 500;
  color: #4a4a55;
  padding: 4px 0;
  width: 50%;
}
td {
  text-align: right;
  font-family: ui-monospace, "SF Mono", Consolas, monospace;
  font-size: 13px;
  padding: 4px 0;
  word-break: break-all;
}
.dim { color: #8b8b93; font-size: 12px; }
.ok { color: #198038; font-weight: 600; }
.bad { color: #c21e1e; font-weight: 600; }
em { color: #8b8b93; font-style: normal; }
a { color: #0a58ca; }
.audit-banner {
  border-color: #c21e1e;
  background: #fff5f5;
}
footer {
  margin-top: 32px;
  color: #8b8b93;
  font-size: 12px;
  text-align: center;
}
@media (prefers-color-scheme: dark) {
  body { background: #16161a; color: #e6e6ec; }
  header .meta { color: #9a9aa5; }
  .card { background: #202027; border-color: #34343c; }
  .card h2 { color: #9a9aa5; }
  th { color: #b6b6c0; }
  .dim { color: #7f7f88; }
  .ok { color: #4ade80; }
  .bad { color: #f87171; }
  em { color: #7f7f88; }
  a { color: #6ba4ff; }
  .audit-banner { border-color: #f87171; background: #2a1618; }
  footer { color: #7f7f88; }
}
"""


def render_status_html(
    report: StatusReport,
    *,
    refresh_seconds: int = 30,
    rendered_at: datetime | None = None,
    include_imports: bool = False,
) -> str:
    """Render a :class:`StatusReport` as a complete HTML document.

    Parameters
    ----------
    report:
        The :class:`StatusReport` produced by
        :func:`steward.infra.status.collect_status`.
    refresh_seconds:
        Interval for the ``<meta http-equiv="refresh">`` tag. Set to
        ``0`` to disable auto-refresh entirely.
    rendered_at:
        Override for the "rendered at" header timestamp.
    include_imports:
        Whether the current view spans attached inventories
        (ADR-0013). Surfaces as a header badge + a toggle link
        between "local only" and "all machines".
    """
    ts = rendered_at or datetime.now()
    refresh_tag = f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">' if refresh_seconds > 0 else ""

    sections = [
        _render_inventory(report),
        _render_latest_scan(report),
        _render_stash(report),
        _render_adapter(report, attr="last_replicate", title="last replicate"),
        _render_adapter(report, attr="last_archive", title="last archive"),
        _render_audit(report),
    ]

    audit_banner = ""
    if not report.audit_chain.ok:
        audit_banner = (
            '<div class="card audit-banner">'
            '<strong class="bad">audit chain broken:</strong> '
            f"{_esc(report.audit_chain.error)}"
            "</div>"
        )

    if include_imports:
        scope_marker = (
            '<span class="meta"> · scope: <strong>all machines</strong> · <a href="/">switch to local only</a></span>'
        )
    else:
        scope_marker = '<span class="meta"> · scope: <strong>local only</strong> · <a href="/?include_imports=1">include attached</a></span>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Steward — status</title>
{refresh_tag}
<style>{_CSS}</style>
</head>
<body>
<header>
  <h1>Steward — status</h1>
  <span class="meta">rendered {html.escape(ts.isoformat(timespec="seconds"))}
  · refresh {int(refresh_seconds)}s</span>
  {scope_marker}
</header>
{audit_banner}
<main class="grid">
{"".join(sections)}
</main>
<footer>
  read-only — every mutation still requires <code>steward apply --execute</code>
</footer>
</body>
</html>
"""


__all__ = ["render_status_html"]
