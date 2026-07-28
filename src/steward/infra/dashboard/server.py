# SPDX-License-Identifier: Apache-2.0

"""HTTP server for the dashboard — stdlib ``http.server``, no deps.

The dashboard ships as a tiny single-page app: two endpoints (HTML
+ JSON) served by Python's stdlib ``http.server``. No FastAPI, no
Jinja2 — the renderer is a pure string-builder; serving is just
``BaseHTTPRequestHandler.send_response`` + write the bytes.

This is deliberate. The dashboard's job is "show the operator what
they'd see in ``steward status``, in a browser, with auto-refresh."
Anything more — auth, sessions, write surface — belongs in a v0.3
FastAPI build with proper auth integration.

Bind defaults to ``127.0.0.1`` so the dashboard is never reachable
off the operator's machine without explicit opt-in. Operators who
want LAN exposure can pass ``--host 0.0.0.0`` and live with the
consequences (read-only, but still).
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from steward.infra.dashboard.render import render_status_html
from steward.infra.status import collect_status, status_to_dict


class _DashboardHandler(BaseHTTPRequestHandler):
    """Two routes: GET ``/`` (HTML) and GET ``/status.json``.

    The handler reads its bound state off the server instance
    (``self.server._db_path`` / ``self.server._refresh_seconds``)
    rather than via globals — this lets tests construct an isolated
    server with their own DB path.
    """

    # Type-checker hint — set by :class:`DashboardServer`.
    server: "DashboardServer"

    def do_GET(self) -> None:  # noqa: N802 — required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._respond_html(parsed.query)
        elif parsed.path == "/status.json":
            self._respond_json(parsed.query)
        elif parsed.path == "/healthz":
            self._respond_text("ok", status=200)
        else:
            self._respond_text("not found\n", status=404)

    def _query_flag(self, query: str, name: str) -> bool:
        """Parse ``?name=1`` from the URL. Accepts 1/true/yes/on."""
        params = parse_qs(query)
        raw = params.get(name, [""])[0].strip().lower()
        return raw in ("1", "true", "yes", "on")

    def _collect(self, query: str) -> Any:
        include_imports = self._query_flag(query, "include_imports")
        # Default quick=server default; ?full=1 forces full chain/stash.
        full = self._query_flag(query, "full")
        quick = self.server._quick and not full
        if self._query_flag(query, "quick"):
            quick = True
        return collect_status(
            db_path=self.server._db_path,
            include_imports=include_imports,
            quick=quick,
        ), include_imports, quick

    def _respond_html(self, query: str) -> None:
        try:
            report, include_imports, _quick = self._collect(query)
            body = render_status_html(
                report,
                refresh_seconds=self.server._refresh_seconds,
                include_imports=include_imports,
            ).encode("utf-8")
        except Exception as exc:  # pragma: no cover - defensive  # noqa: BLE001
            self._respond_text(
                f"500 error collecting status: {exc}\n", status=500
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _respond_json(self, query: str) -> None:
        try:
            report, include_imports, quick = self._collect(query)
            payload: dict[str, Any] = status_to_dict(report)
            payload["include_imports"] = include_imports
            payload["quick"] = quick
        except Exception as exc:  # pragma: no cover  # noqa: BLE001
            self._respond_text(
                f"500 error collecting status: {exc}\n", status=500
            )
            return
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _respond_text(self, body: str, *, status: int) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # Silence the default per-request access log (stdout). The CLI
    # prints a single "listening" line itself; per-request noise on
    # an auto-refreshing dashboard is unhelpful.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        del format, args


class DashboardServer(HTTPServer):
    """``HTTPServer`` subclass that carries the operator's DB path."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        db_path: Path,
        refresh_seconds: int = 30,
        quick: bool = True,
    ) -> None:
        super().__init__((host, port), _DashboardHandler)
        self._db_path = db_path
        self._refresh_seconds = refresh_seconds
        self._quick = quick


def run_dashboard(
    *,
    db_path: Path,
    host: str = "127.0.0.1",
    port: int = 8080,
    refresh_seconds: int = 30,
    quick: bool = True,
) -> None:
    """Block forever serving the dashboard. CLI invokes this directly.

    Stop with SIGINT (Ctrl-C). The function never returns under normal
    operation — ``serve_forever()`` is interruptible only by signal.

    ``quick`` defaults True so multi‑GB inventories stay responsive;
    pass ``quick=False`` or hit ``?full=1`` for full audit-chain walks.
    """
    server = DashboardServer(
        host=host,
        port=port,
        db_path=db_path,
        refresh_seconds=refresh_seconds,
        quick=quick,
    )
    try:
        server.serve_forever()
    finally:  # pragma: no cover - cleanup
        server.server_close()


__all__ = ["DashboardServer", "run_dashboard"]
