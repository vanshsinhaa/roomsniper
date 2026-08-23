from __future__ import annotations

import json
import re
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from hayden_booker.config import database_path, load_config
from hayden_booker.persistence.database import connect
from hayden_booker.persistence.repository import ReservationRepository
from hayden_booker.ui import health, history

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_TIMEZONE = "America/Phoenix"

_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
_STATIC_FILES = {
    "/static/app.js": "application/javascript; charset=utf-8",
    "/static/styles.css": "text/css; charset=utf-8",
}
_OCCURRENCE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# A cross-origin page can submit a form POST, but it cannot set a custom header
# without a preflight this server never approves.
_WRITE_HEADER = "X-Hayden-Dashboard"


class DashboardHandler(BaseHTTPRequestHandler):
    """Read-only handler; the dashboard never triggers a booking action."""

    server_version = "HaydenBookerUI"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def __init__(self, *args: Any, config_path: Path, **kwargs: Any) -> None:
        self.config_path = config_path
        super().__init__(*args, **kwargs)

    # BaseHTTPRequestHandler API ------------------------------------------------
    def do_GET(self) -> None:
        if not self._host_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Only local requests are served."})
            return
        path = urlsplit(self.path).path
        try:
            self._route(path)
        except FileNotFoundError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
        except KeyError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown booking."})
        except Exception as exc:  # pragma: no cover - surfaced in the dashboard banner
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}"})

    def do_POST(self) -> None:
        """The only write path: marking an occurrence as humanly reviewed."""
        if not self._host_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Only local requests are served."})
            return
        if self.headers.get(_WRITE_HEADER) is None:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Missing dashboard header."})
            return
        path = urlsplit(self.path).path
        acknowledge = re.fullmatch(r"/api/bookings/([^/]+)/acknowledge", path)
        if not acknowledge:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        try:
            occurrence_id = _validated_id(acknowledge.group(1))
            with _repository() as repository:
                repository.acknowledge(occurrence_id)
                payload = history.booking_detail(
                    repository, occurrence_id, timezone=self._timezone()
                )
        except KeyError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown booking."})
            return
        except Exception as exc:  # pragma: no cover - surfaced in the dashboard banner
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}"})
            return
        self._send_json(HTTPStatus.OK, payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    # Routing -------------------------------------------------------------------
    def _route(self, path: str) -> None:
        if path in {"", "/", "/index.html"}:
            self._send_bytes(HTTPStatus.OK, _asset("index.html"), "text/html; charset=utf-8")
            return
        if path in _STATIC_FILES:
            name = path.rsplit("/", 1)[-1]
            self._send_bytes(HTTPStatus.OK, _asset(name), _STATIC_FILES[path])
            return
        if path == "/api/status":
            self._send_json(HTTPStatus.OK, health.build_status(self.config_path))
            return
        if path == "/api/bookings":
            limit = self._int_param("limit", default=100, minimum=1, maximum=500)
            with _repository() as repository:
                bookings = history.list_bookings(repository, timezone=self._timezone(), limit=limit)
            self._send_json(HTTPStatus.OK, {"bookings": bookings})
            return
        if path == "/api/logs":
            limit = self._int_param("limit", default=100, minimum=1, maximum=500)
            self._send_json(HTTPStatus.OK, {"events": health.read_log_events(limit=limit)})
            return
        booking = re.fullmatch(r"/api/bookings/([^/]+)", path)
        if booking:
            occurrence_id = _validated_id(booking.group(1))
            with _repository() as repository:
                payload = history.booking_detail(
                    repository, occurrence_id, timezone=self._timezone()
                )
            self._send_json(HTTPStatus.OK, payload)
            return
        calendar = re.fullmatch(r"/api/bookings/([^/]+)/calendar\.ics", path)
        if calendar:
            occurrence_id = _validated_id(calendar.group(1))
            with _repository() as repository:
                occurrence = repository.get(occurrence_id)
            document = history.ics_document(occurrence, zone=ZoneInfo(self._timezone()))
            self._send_bytes(
                HTTPStatus.OK,
                document.encode("utf-8"),
                "text/calendar; charset=utf-8",
                extra_headers={
                    "Content-Disposition": f'attachment; filename="hayden-{occurrence_id}.ics"'
                },
            )
            return
        raise FileNotFoundError(path)

    # Helpers -------------------------------------------------------------------
    def _timezone(self) -> str:
        try:
            config, _ = load_config(self.config_path)
        except ValueError:
            return DEFAULT_TIMEZONE
        return config.timezone

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip()
        return host in _ALLOWED_HOSTS or host == ""

    def _int_param(self, name: str, *, default: int, minimum: int, maximum: int) -> int:
        values = parse_qs(urlsplit(self.path).query).get(name)
        if not values:
            return default
        try:
            parsed = int(values[0])
        except ValueError:
            return default
        return max(minimum, min(maximum, parsed))

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


class _RepositoryContext:
    """Per-request SQLite connection; sqlite3 connections are not thread-safe."""

    def __enter__(self) -> ReservationRepository:
        self.connection = connect(database_path())
        return ReservationRepository(self.connection)

    def __exit__(self, *exc_info: object) -> None:
        self.connection.close()


def _repository() -> _RepositoryContext:
    return _RepositoryContext()


def _validated_id(value: str) -> str:
    if not _OCCURRENCE_ID.fullmatch(value):
        raise KeyError(value)
    return value


def _asset(name: str) -> bytes:
    return (resources.files("hayden_booker.ui.static") / name).read_bytes()


def create_server(
    config_path: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> ThreadingHTTPServer:
    handler = partial(DashboardHandler, config_path=config_path)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(config_path: Path, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    with create_server(config_path, host=host, port=port) as server:
        server.serve_forever()
