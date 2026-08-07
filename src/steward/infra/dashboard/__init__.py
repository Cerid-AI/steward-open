"""HTML status dashboard (ops console — intentional non-parity).

A single-page **operator ops console** over plain HTTP. It is **not** a
full mirror of every ``steward`` CLI verb. Product stance:

* **Always GUI:** status, estate health, fleet matrix, inventory stats /
  surface exploration, inspect, FP + dual-presence sample, plan backlog.
* **Usually GUI:** policy plan, apply dry-run (with execute *handoff*),
  dual-presence plan filter, replicate/archive dry-run, selected
  EXECUTE-gated adapter actions already in the ops rail.
* **CLI/MCP primary:** scan/watch/classify/embed, ``db *`` lifecycle,
  ``apply --execute`` as structural SoT (ADR-0002/0016), photos,
  schedule install, env/policy file SoT.

Do **not** remove existing ops-rail controls when extending the console.

Implementation: stdlib ``http.server``, SSR HTML + inline CSS/JS:

* Soft-poll ``/status.json`` (estate ``posture``)
* Analysis panes: scans, audit, stats, surface, fleet, inspector, …
* Loopback ``POST /api/actions`` (destructive requires typing ``EXECUTE``)

Bind defaults to ``127.0.0.1``.
"""

from steward.infra.dashboard.render import render_status_html
from steward.infra.dashboard.server import DashboardServer, run_dashboard

__all__ = [
    "DashboardServer",
    "render_status_html",
    "run_dashboard",
]
