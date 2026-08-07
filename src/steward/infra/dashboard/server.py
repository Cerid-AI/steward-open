# SPDX-License-Identifier: Apache-2.0

"""HTTP server for the dashboard — stdlib ``http.server``, no deps.

Routes:

* ``GET /`` — interactive HTML shell (SSR seed + progressive JS)
* ``GET /status.json`` — status snapshot (short TTL cache)
* ``GET /api/analysis`` — scans + audit + policies + schedules
* ``GET /api/stats`` — overview or ``?axis=`` (incl. volume, cross)
* ``GET /api/surface`` — path-tree inventory surface (ADR-0022)
* ``GET /api/actions`` — action catalog
* ``POST /api/actions`` — run an action (loopback only)
* ``GET /api/fp`` — Dropbox FP probe
* ``GET /api/fleet`` — multi-machine fleet health matrix (ADR-0021)
* ``GET /api/health`` — estate posture (cheap quick default; ``?full=1``)
* ``GET /api/health/series`` — compact snapshot points for sparklines
* ``GET /api/plans`` — plan backlog list (ADR-0019)
* ``GET /api/plans/<id>`` — plan backlog detail
* ``GET /api/queues`` — Queues pane: plans + stash + schedule overdue
* ``GET /api/schedule/reliability`` — schedule reliability surface
* ``GET /healthz`` — liveness

Threading: ``ThreadingHTTPServer`` so a slow action does not freeze
status polls. Actions that mutate require loopback + confirmation.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from steward.infra.dashboard import api as dash_api
from steward.infra.dashboard.render import render_status_html


class _StatusCache:
    """Tiny TTL cache so soft-refresh polls do not thrash multi-GB DBs."""

    def __init__(self, ttl_seconds: float = 2.0) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._key: tuple[Any, ...] | None = None
        self._payload: dict[str, Any] | None = None
        self._expires = 0.0

    def get_or_load(
        self,
        key: tuple[Any, ...],
        loader: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if self._payload is not None and self._key == key and now < self._expires:
                return self._payload
        payload = loader()
        with self._lock:
            self._key = key
            self._payload = payload
            self._expires = time.monotonic() + self._ttl
        return payload

    def invalidate(self) -> None:
        with self._lock:
            self._payload = None
            self._key = None
            self._expires = 0.0


class _DashboardHandler(BaseHTTPRequestHandler):
    """Dashboard HTTP routes."""

    server: "DashboardServer"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._respond_html(parsed.query)
        elif path == "/status.json":
            self._respond_status_json(parsed.query)
        elif path == "/api/analysis":
            self._respond_analysis(parsed.query)
        elif path == "/api/stats":
            self._respond_stats(parsed.query)
        elif path == "/api/surface":
            self._respond_surface(parsed.query)
        elif path == "/api/actions":
            self._respond_json_obj({"ok": True, "actions": dash_api.action_catalog()})
        elif path == "/api/fp":
            self._respond_fp()
        elif path == "/api/fleet":
            self._respond_fleet(parsed.query)
        elif path == "/api/health":
            self._respond_health(parsed.query)
        elif path == "/api/health/series":
            self._respond_health_series(parsed.query)
        elif path == "/api/plans":
            self._respond_plans(parsed.query)
        elif path.startswith("/api/plans/"):
            self._respond_plan_detail(path)
        elif path == "/api/queues":
            self._respond_queues(parsed.query)
        elif path == "/api/schedule/reliability":
            self._respond_schedule_reliability(parsed.query)
        elif path == "/healthz":
            self._respond_text("ok", status=200)
        else:
            self._respond_text("not found\n", status=404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/actions":
            self._respond_run_action()
        else:
            self._respond_text("not found\n", status=404)

    def do_OPTIONS(self) -> None:  # noqa: N802
        # Local SPA convenience (same-origin normally).
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── helpers ──────────────────────────────────────────────

    def _query_flag(self, query: str, name: str) -> bool:
        params = parse_qs(query)
        raw = params.get(name, [""])[0].strip().lower()
        return raw in ("1", "true", "yes", "on")

    def _query_int(self, query: str, name: str, default: int) -> int:
        params = parse_qs(query)
        raw = params.get(name, [""])[0].strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _query_str(self, query: str, name: str, default: str = "") -> str:
        params = parse_qs(query)
        return params.get(name, [default])[0]

    def _is_loopback(self) -> bool:
        host = self.client_address[0] if self.client_address else ""
        return host in ("127.0.0.1", "::1", "localhost")

    def _collect_flags(self, query: str) -> tuple[bool, bool]:
        include_imports = self._query_flag(query, "include_imports")
        full = self._query_flag(query, "full")
        quick = self.server._quick and not full
        if self._query_flag(query, "quick"):
            quick = True
        return include_imports, quick

    def _respond_html(self, query: str) -> None:
        try:
            include_imports, quick = self._collect_flags(query)
            from steward.infra.status import collect_status

            report = collect_status(
                db_path=self.server._db_path,
                include_imports=include_imports,
                quick=quick,
            )
            # Warm status + health caches so the first soft-poll is free.
            from steward.infra.dashboard.api import posture_from_status_report
            from steward.infra.status import status_to_dict

            key = (include_imports, quick)
            cached = status_to_dict(report)
            cached["include_imports"] = include_imports
            cached["quick"] = quick
            cached["ok"] = True
            cached["generated_at"] = time.time()
            cached["posture"] = posture_from_status_report(report, quick=quick)

            def _loader() -> dict[str, Any]:
                return cached

            self.server._status_cache.get_or_load(key, _loader)
            hkey = ("health", include_imports, quick, False, not quick)
            self.server._health_cache.get_or_load(
                hkey,
                lambda: {
                    "ok": True,
                    **cached["posture"],
                    "include_imports": include_imports,
                    "probes": False,
                    "elapsed_ms": 0,
                    "generated_at": cached["generated_at"],
                },
            )
            body = render_status_html(
                report,
                refresh_seconds=self.server._refresh_seconds,
                include_imports=include_imports,
                quick=quick,
                posture=cached["posture"],
            ).encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            self._respond_text(f"500 error collecting status: {exc}\n", status=500)
            return
        self._send_bytes(body, content_type="text/html; charset=utf-8")

    def _respond_status_json(self, query: str) -> None:
        try:
            include_imports, quick = self._collect_flags(query)
            key = (include_imports, quick)
            payload = self.server._status_cache.get_or_load(
                key,
                lambda: dash_api.build_status_payload(
                    self.server._db_path,
                    include_imports=include_imports,
                    quick=quick,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._respond_text(f"500 error collecting status: {exc}\n", status=500)
            return
        self._send_bytes(dash_api.dumps(payload), content_type="application/json; charset=utf-8")

    def _respond_analysis(self, query: str) -> None:
        try:
            include_imports = self._query_flag(query, "include_imports")
            payload = dash_api.build_analysis_bundle(
                self.server._db_path,
                include_imports=include_imports,
                scan_limit=self._query_int(query, "scan_limit", 12),
                audit_limit=self._query_int(query, "audit_limit", 25),
            )
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json_obj(payload)

    def _respond_stats(self, query: str) -> None:
        try:
            include_imports = self._query_flag(query, "include_imports")
            axis = self._query_str(query, "axis", "").strip()
            if axis:
                payload = dash_api.build_stats_axis(
                    self.server._db_path,
                    axis=axis,
                    limit=self._query_int(query, "limit", 20),
                    include_imports=include_imports,
                    dim_a=self._query_str(query, "dim_a", "") or None,
                    dim_b=self._query_str(query, "dim_b", "") or None,
                    path_prefix=self._query_str(query, "path_prefix", "") or None,
                )
            else:
                payload = dash_api.build_stats_overview(
                    self.server._db_path,
                    include_imports=include_imports,
                )
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json_obj(payload)

    def _respond_surface(self, query: str) -> None:
        try:
            payload = dash_api.build_surface_payload(
                self.server._db_path,
                path_prefix=self._query_str(query, "prefix", "")
                or self._query_str(query, "path_prefix", ""),
                color_by=self._query_str(query, "color_by", "none") or "none",
                tier=self._query_str(query, "tier", "") or None,
                volume=self._query_str(query, "volume", "") or None,
                child_limit=self._query_int(query, "limit", 100),
                measure=self._query_str(query, "measure", "total_bytes") or "total_bytes",
                include_imports=self._query_flag(query, "include_imports"),
            )
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json_obj(payload)

    def _respond_fp(self) -> None:
        try:
            from steward.infra.fp_status import collect_fp_status, fp_status_to_dict

            payload = {"ok": True, **fp_status_to_dict(collect_fp_status())}
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json_obj(payload)

    def _respond_health(self, query: str) -> None:
        try:
            include_imports, quick = self._collect_flags(query)
            probes = self._query_flag(query, "probes")
            include_fp = self._query_flag(query, "fp") or (not quick)
            key = ("health", include_imports, quick, probes, include_fp)
            payload = self.server._health_cache.get_or_load(
                key,
                lambda: dash_api.build_health_payload(
                    self.server._db_path,
                    include_imports=include_imports,
                    quick=quick,
                    probes=probes,
                    include_fp=include_fp,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json_obj(payload)


    def _respond_fleet(self, query: str) -> None:
        try:
            include_imports, quick = self._collect_flags(query)
            # Default include_imports for fleet (ADR-0021); allow ?local_only=1
            params = parse_qs(query)
            if "local_only" in params and params["local_only"] and params["local_only"][0] in (
                "1",
                "true",
                "yes",
            ):
                include_imports = False
            elif "include_imports" not in params:
                include_imports = True
            key = ("fleet", include_imports, quick)
            payload = self.server._health_cache.get_or_load(
                key,
                lambda: dash_api.build_fleet_payload(
                    self.server._db_path,
                    include_imports=include_imports,
                    quick=quick,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json_obj(payload)

    def _respond_health_series(self, query: str) -> None:
        try:
            limit = self._query_int(query, "limit", 48)
            payload = dash_api.build_health_series(self.server._db_path, limit=limit)
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json_obj(payload)

    def _respond_plans(self, query: str) -> None:
        try:
            status = self._query_str(query, "status", "") or None
            policy = self._query_str(query, "policy", "") or None
            limit = self._query_int(query, "limit", 50)
            payload = dash_api.build_plans_payload(status=status, policy=policy, limit=limit)
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json_obj(payload)

    def _respond_plan_detail(self, path: str) -> None:
        plan_id = path[len("/api/plans/") :].strip("/")
        if not plan_id or "/" in plan_id:
            self._respond_json_obj({"ok": False, "error": "invalid plan id"}, status=400)
            return
        try:
            payload = dash_api.build_plan_detail(plan_id)
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        status = 200 if payload.get("ok") else 404
        self._respond_json_obj(payload, status=status)

    def _respond_queues(self, query: str) -> None:
        try:
            include_imports, quick = self._collect_flags(query)
            payload = dash_api.build_queues_payload(
                self.server._db_path,
                include_imports=include_imports,
                quick=quick,
                plan_limit=self._query_int(query, "limit", 25),
            )
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json_obj(payload)

    def _respond_schedule_reliability(self, query: str) -> None:
        try:
            probe = self._query_flag(query, "probe")
            payload = dash_api.build_schedule_reliability_payload(probe=probe)
        except Exception as exc:  # noqa: BLE001
            self._respond_json_obj({"ok": False, "error": str(exc)}, status=500)
            return
        self._respond_json_obj(payload)

    def _respond_run_action(self) -> None:
        if not self._is_loopback():
            self._respond_json_obj(
                {"ok": False, "error": "actions only allowed from loopback clients"},
                status=403,
            )
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._respond_json_obj({"ok": False, "error": "invalid JSON body"}, status=400)
            return
        action_id = str(body.get("action") or body.get("id") or "").strip()
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        # Allow top-level param fields too for convenience
        for k, v in body.items():
            if k not in ("action", "id", "params") and k not in params:
                params[k] = v
        if not action_id:
            self._respond_json_obj({"ok": False, "error": "action required"}, status=400)
            return
        result = dash_api.run_action(self.server._db_path, action_id=action_id, params=params)
        # Invalidate status cache after any successful write/plan
        if result.get("ok") and action_id in {
            "refresh_rollups",
            "refresh_health",
            "replicate_execute",
            "archive_execute",
            "stash_finalize",
            "stash_restore",
            "status_full",
        }:
            self.server._status_cache.invalidate()
            self.server._health_cache.invalidate()
        status = 200 if result.get("ok") else 400
        if result.get("need_confirm"):
            status = 409
        self._respond_json_obj(result, status=status)

    def _respond_json_obj(self, obj: dict[str, Any], *, status: int = 200) -> None:
        self._send_bytes(
            dash_api.dumps(obj),
            content_type="application/json; charset=utf-8",
            status=status,
        )

    def _respond_text(self, body: str, *, status: int) -> None:
        self._send_bytes(body.encode("utf-8"), content_type="text/plain; charset=utf-8", status=status)

    def _send_bytes(
        self,
        body: bytes,
        *,
        content_type: str,
        status: int = 200,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        del format, args


class DashboardServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying operator DB path + refresh config."""

    daemon_threads = True
    allow_reuse_address = True

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
        self._status_cache = _StatusCache(ttl_seconds=2.0)
        self._health_cache = _StatusCache(ttl_seconds=5.0)


def run_dashboard(
    *,
    db_path: Path,
    host: str = "127.0.0.1",
    port: int = 8080,
    refresh_seconds: int = 30,
    quick: bool = True,
) -> None:
    """Block forever serving the dashboard. CLI invokes this directly."""
    server = DashboardServer(
        host=host,
        port=port,
        db_path=db_path,
        refresh_seconds=refresh_seconds,
        quick=quick,
    )
    try:
        server.serve_forever()
    finally:  # pragma: no cover
        server.server_close()


__all__ = ["DashboardServer", "run_dashboard"]
