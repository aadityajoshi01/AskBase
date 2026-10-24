"""AskBase gateway HTTP server (stdlib only, no extra dependencies).

Serves the contracted routes from ui-evaluation/docs/api-contract.json
so the Agent 4 UI can talk to the real pipeline:

    GET  /api/health
    GET  /api/databases
    GET  /api/schema?database=<id>
    POST /api/datasets/profile   {"database": "<id>"}
    POST /api/datasets/upload    {"fileName": "…", "csv": "<text>"} -> registers a dataset
    POST /api/analyze            {"question": "...", "databaseConfig": {...}}
    POST /api/report             {"databaseConfig": {...}} -> verified sales report HTML
    GET  /api/chart?ticket=...&type=bar&x=...&y=...   -> image/png (rows from the
         server-side cache of that analysis' permission-filtered result, never
         from the client)

Run from the repository root:  python -m gateway.server
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from gateway import ROOT
from gateway import charts as chart_service
from gateway.ingest import IngestError, build_dataset
from gateway.orchestrator import (
    UnknownDatabase,
    agents,
    get_insights,
    identity_directory,
    load_registry,
    policy_for_dataset,
    principal_for,
    resolve_database,
    run_analysis,
    run_report,
    visible_schema,
)
from gateway.permissions import (PolicyError, restrict_profile, row_predicates)

HOST = os.getenv("GATEWAY_HOST", "127.0.0.1")
PORT = int(os.getenv("GATEWAY_PORT", "8010"))
MAX_BODY_BYTES = 64 * 1024
MAX_UPLOAD_BODY_BYTES = 6 * 1024 * 1024
# A rejected body is still drained to this ceiling: replying and closing while the
# client is mid-send gets the connection reset (WinError 10053) instead of a JSON 413.
MAX_DRAIN_BYTES = 8 * 1024 * 1024
# The gateway has no login: a caller names itself in `X-AskBase-User` and the server
# decides what that user may see. Cross-origin reads are limited to localhost so a
# random web page cannot browse the databases of a developer running this locally.
WRITING_ROLES = ("admin", "analyst")
ALLOWED_ORIGIN = os.getenv("GATEWAY_ALLOWED_ORIGIN", "")


def _cors_origin(header: str | None) -> str:
    if ALLOWED_ORIGIN:
        return ALLOWED_ORIGIN
    if header and header.startswith(("http://localhost:", "http://127.0.0.1:")):
        return header
    return "http://127.0.0.1:4173"


class BodyTooLarge(ValueError):
    pass


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "AskBaseGateway/1.0"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin",
                         _cors_origin(self.headers.get("Origin")))
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-AskBase-User")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _send_image(self, png: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin",
                         _cors_origin(self.headers.get("Origin")))
        self.end_headers()
        self.wfile.write(png)

    def _identity(self, body: dict | None = None) -> str:
        """The caller's self-declared user. The role is never taken from here."""
        return str(self.headers.get("X-AskBase-User") or (body or {}).get("user") or "")

    def _deny(self, message: str, *, identity: str | None = None, **extra):
        """403 with the caller's resolved role. `extra` fills the keys the route's
        consumer already expects, so a refusal never looks like a malformed success."""
        return self._send({"status": "error", "message": message,
                           "role": principal_for(identity)["role"], **extra}, 403)

    def do_OPTIONS(self):  # noqa: N802
        self._send({}, 204)

    def _read_json(self, max_body: int = MAX_BODY_BYTES) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_DRAIN_BYTES:
            self.close_connection = True      # refuse to buffer a huge upload
            raise BodyTooLarge(f"request body exceeds {MAX_DRAIN_BYTES} bytes")
        raw = self.rfile.read(length) if length else b"{}"
        if length > max_body:
            raise BodyTooLarge(f"request body exceeds {max_body} bytes")
        parsed = json.loads(raw or b"{}")
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return parsed

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._send({"status": "ok", "service": "askbase-gateway"})
        if parsed.path == "/api/databases":
            registry = load_registry()
            return self._send({
                "databases": [
                    {"id": entry["id"], "label": entry["label"], "type": "sqlite",
                     "available": os.path.isfile(entry["path"])}
                    for entry in registry.values()
                ]})
        if parsed.path == "/api/users":
            return self._send(identity_directory(self._identity()))
        if parsed.path == "/api/schema":
            database_id = (parse_qs(parsed.query).get("database") or [""])[0]
            identity = self._identity()
            try:
                _, entry = resolve_database({"database": database_id})
                _stored, visible, _hidden = visible_schema(
                    entry, policy_for_dataset(identity, database_id))
                return self._send(visible)
            except UnknownDatabase as exc:
                return self._send({"status": "error", "message": str(exc)}, 404)
            except PolicyError as exc:
                return self._deny(f"policy error: {exc}", identity=identity, tables=[])
        if parsed.path == "/api/insights":
            database_id = (parse_qs(parsed.query).get("database") or [""])[0]
            identity = self._identity()
            empty = {"questions": [], "summary": "", "context": ""}
            try:
                return self._send(get_insights({"database": database_id}, identity))
            except UnknownDatabase as exc:
                return self._send({"status": "error", "message": str(exc), **empty}, 404)
            except PolicyError as exc:
                return self._deny(f"policy error: {exc}", identity=identity, **empty)
        if parsed.path == "/api/chart":
            qs = parse_qs(parsed.query)
            payload = {key: (values[0] if values else "")
                       for key, values in (("ticket", qs.get("ticket")),
                                           ("type", qs.get("type")),
                                           ("x", qs.get("x")),
                                           ("y", qs.get("y")))
                       if values}
            try:
                status, media, body = chart_service.handle_chart_request(payload)
                if status == 200:
                    return self._send_image(body)
                return self._send(json.loads(body), status)
            except chart_service.ChartError as exc:
                return self._send({"status": "error", "message": str(exc)}, exc.status)
            except Exception as exc:
                return self._send({"status": "error",
                                   "message": f"chart rendering failed: {exc}"}, 500)
        self._send({"status": "error", "message": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        max_body = (MAX_UPLOAD_BODY_BYTES if self.path == "/api/datasets/upload"
                    else MAX_BODY_BYTES)
        try:
            body = self._read_json(max_body)
        except BodyTooLarge as exc:
            return self._send({"status": "error", "message": str(exc)}, 413)
        except (ValueError, json.JSONDecodeError) as exc:
            return self._send({"status": "error", "message": f"invalid JSON body: {exc}"}, 400)

        if self.path == "/api/analyze":
            question = str(body.get("question") or "")
            identity = self._identity(body)
            try:
                return self._send(run_analysis(question, body.get("databaseConfig"), identity))
            except Exception as exc:  # never leak a stack trace as a 500 to the UI
                return self._send({
                    "status": "error",
                    "answer": "AskBase could not produce a verified answer for this question.",
                    # the analysis-response schema types sql as string — null breaks it
                    "sql": "", "columns": [], "rows": [], "rowCount": 0, "row_count": 0,
                    "truncated": False, "warnings": [str(exc)],
                    "verification": {"passed": False, "message": f"gateway failure: {exc}", "checks": []},
                    "chart": None, "assumptions": [], "clarification_question": None,
                    "error": {"type": "gateway", "message": str(exc)},
                    "meta": {"durationMs": 0, "readOnly": True, "code": "analysis_failed",
                             "role": principal_for(identity)["role"]}}, 200)

        if self.path == "/api/report":
            identity = self._identity(body)
            try:
                return self._send(run_report(body.get("databaseConfig"), identity))
            except UnknownDatabase as exc:
                return self._send({"status": "error", "message": str(exc), "html": ""}, 404)
            except PolicyError as exc:
                return self._deny(f"policy error: {exc}", identity=identity, html="")
            except Exception as exc:
                return self._send({"status": "error", "message": f"report failed: {exc}",
                                   "html": ""}, 500)

        if self.path == "/api/datasets/profile":
            identity = self._identity(body)
            try:
                database_id, entry = resolve_database(
                    {"database": str(body.get("database") or "")})
                policy = policy_for_dataset(identity, database_id)
                stored, _visible, hidden = visible_schema(entry, policy)
                profile = agents.profile_database({"type": "sqlite", "path": entry["path"]})
                return self._send(restrict_profile(
                    profile, policy, set(hidden), row_predicates(policy, stored)))
            except UnknownDatabase as exc:
                return self._send({"status": "error", "message": str(exc)}, 404)
            except PolicyError as exc:
                return self._deny(f"policy error: {exc}", identity=identity, tables=[])
            except Exception as exc:
                return self._send({"status": "error", "message": str(exc)}, 500)

        if self.path == "/api/datasets/upload":
            identity = self._identity(body)
            role = principal_for(identity)["role"]
            if role not in WRITING_ROLES:
                return self._deny(f"role {role} may not register datasets",
                                  identity=identity)
            try:
                entry = build_dataset(str(body.get("fileName") or body.get("name") or "uploaded"),
                                      str(body.get("csv") or ""))
            except IngestError as exc:
                return self._send({"status": "error", "message": str(exc)}, 400)
            except OSError as exc:
                return self._send({"status": "error", "message": f"could not store the dataset: {exc}"}, 500)
            server_path = str(ROOT / entry["path"])
            public = {key: entry[key] for key in
                      ("id", "label", "table", "row_count", "columns", "source") if key in entry}
            try:
                profile = agents.profile_database({"type": "sqlite", "path": server_path})
            except Exception:  # a profile failure must not lose an accepted upload
                profile = None
            return self._send({"status": "ok", "database": public,
                               "schema": agents.get_schema({"type": "sqlite", "path": server_path}),
                               "profile": profile}, 201)

        self._send({"status": "error", "message": "not found"}, 404)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), GatewayHandler)
    print(f"AskBase gateway listening on http://{HOST}:{PORT}")
    print("Databases:", ", ".join(sorted(load_registry())))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
