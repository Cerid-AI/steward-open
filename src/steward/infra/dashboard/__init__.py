"""HTML status dashboard.

A single-page operator dashboard served over plain HTTP. The page
renders the same data ``steward status`` prints to the terminal —
inventory counts, latest scan, stash summary, last replicate/archive
runs, audit-chain status, machines.

The implementation uses Python's stdlib ``http.server`` so there are
no new runtime deps. The page is server-rendered HTML with inline
CSS + a 30-second meta-refresh — no JavaScript framework, no
build step. Operators see live state by opening
``http://127.0.0.1:8080/`` in any browser.

Two endpoints:

* ``GET /`` — rendered HTML
* ``GET /status.json`` — same shape as ``steward status --json``

Per ADR-0009 (pull-don't-push), the dashboard is **read-only**. No
write surface; no mutation. The bind defaults to ``127.0.0.1`` so the
page is never accessible off the operator's machine without explicit
opt-in.
"""

from steward.infra.dashboard.render import render_status_html
from steward.infra.dashboard.server import DashboardServer, run_dashboard

__all__ = [
    "DashboardServer",
    "render_status_html",
    "run_dashboard",
]
