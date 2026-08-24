from __future__ import annotations

import json
import threading
import urllib.request
from datetime import UTC, date, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from hayden_booker.config import load_config
from hayden_booker.constants import AttemptStatus
from hayden_booker.domain.models import OccurrenceKey
from hayden_booker.persistence.database import connect
from hayden_booker.persistence.repository import ReservationRepository
from hayden_booker.sample_config import SAMPLE_CONFIG
from hayden_booker.ui import health, history
from hayden_booker.ui.server import create_server

KEY = OccurrenceKey(
    schedule_id="monday-2pm",
    target_date=date(2026, 8, 24),
    start_time="14:00",
    end_time="15:00",
)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HAYDEN_BOOKER_DATA_DIR", str(tmp_path / "data"))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    return config_path


def seeded_repository() -> ReservationRepository:
    from hayden_booker.config import database_path

    repository = ReservationRepository(connect(database_path()))
    occurrence = repository.ensure_occurrence(KEY).occurrence
    repository.update_status(
        occurrence.id,
        AttemptStatus.CONFIRMED,
        room="Study Room 311A",
        confirmation_reference="LC-123",
    )
    return repository


def test_status_reports_missing_auth_and_never_leaks_secrets(environment: Path) -> None:
    status = health.build_status(environment)
    auth = next(check for check in status["checks"] if check["key"] == "auth")
    assert auth["state"] == "error"
    assert "auth setup" in (auth["remedy"] or "")
    assert status["overall"] in {"attention", "error"}
    serialized = json.dumps(status).lower()
    assert "cookie" not in serialized
    assert "password" not in serialized


def test_status_reports_invalid_configuration(tmp_path: Path, environment: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: 9\nschedules: []\n", encoding="utf-8")
    status = health.build_status(broken)
    config_check = next(check for check in status["checks"] if check["key"] == "config")
    assert config_check["state"] == "error"
    assert status["overall"] == "error"


def write_auth_state() -> None:
    from hayden_booker.security import browser_state

    path = browser_state.browser_auth_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")


def write_log(lines: list[dict[str, Any]]) -> None:
    from hayden_booker.config import app_data_dir

    directory = app_data_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(line) for line in lines) + "\n"
    (directory / "hayden-booker.jsonl").write_text(body, encoding="utf-8")


def test_saved_session_without_contradicting_run_is_ok(environment: Path) -> None:
    write_auth_state()
    status = health.build_status(environment)
    auth = next(check for check in status["checks"] if check["key"] == "auth")
    assert auth["state"] == "ok"


def test_run_that_hit_the_sign_in_wall_marks_auth_expired(environment: Path) -> None:
    write_auth_state()
    later = datetime.now(UTC) + timedelta(minutes=5)
    write_log(
        [
            {
                "timestamp_utc": later.isoformat().replace("+00:00", "Z"),
                "level": "INFO",
                "event": "authentication_checked",
                "status": "AUTH_REQUIRED",
            }
        ]
    )
    status = health.build_status(environment)
    auth = next(check for check in status["checks"] if check["key"] == "auth")
    assert auth["state"] == "error"
    assert "auth setup" in (auth["remedy"] or "")
    assert status["overall"] == "error"


def test_authentication_signal_before_the_saved_session_is_ignored(environment: Path) -> None:
    write_auth_state()
    earlier = datetime.now(UTC) - timedelta(days=2)
    write_log(
        [
            {
                "timestamp_utc": earlier.isoformat().replace("+00:00", "Z"),
                "level": "INFO",
                "event": "authentication_required",
            }
        ]
    )
    status = health.build_status(environment)
    auth = next(check for check in status["checks"] if check["key"] == "auth")
    assert auth["state"] == "ok"


def test_booking_payload_builds_calendar_links(environment: Path) -> None:
    repository = seeded_repository()
    bookings = history.list_bookings(repository, timezone="America/Phoenix")
    repository.connection.close()
    assert len(bookings) == 1
    booking = bookings[0]
    assert booking["outcome"] == "confirmed"
    assert booking["room"] == "Study Room 311A"
    google = booking["calendar"]["google_url"]
    assert google.startswith("https://calendar.google.com/calendar/render?")
    assert "dates=20260824T140000%2F20260824T150000" in google
    assert "ctz=America%2FPhoenix" in google


def test_ics_document_uses_utc_instants(environment: Path) -> None:
    repository = seeded_repository()
    occurrence = repository.list_occurrences(limit=1)[0]
    document = history.ics_document(occurrence, zone=ZoneInfo("America/Phoenix"))
    repository.connection.close()
    assert "DTSTART:20260824T210000Z" in document
    assert "DTEND:20260824T220000Z" in document
    assert "SUMMARY:Hayden Library - Study Room 311A" in document


def seed_manual_review() -> str:
    from hayden_booker.config import database_path

    repository = ReservationRepository(connect(database_path()))
    occurrence = repository.ensure_occurrence(
        OccurrenceKey(
            schedule_id="smoke-test",
            target_date=date(2026, 8, 22),
            start_time="17:30",
            end_time="18:00",
        )
    ).occurrence
    repository.update_status(
        occurrence.id,
        AttemptStatus.MANUAL_REVIEW_REQUIRED,
        room="Study Room 311B",
        error_code="UNKNOWN_RESULT",
        error_summary="The final page did not contain a known signal.",
    )
    repository.connection.close()
    return occurrence.id


def test_unreviewed_occurrence_raises_attention(environment: Path) -> None:
    seed_manual_review()
    status = health.build_status(environment)
    database = next(check for check in status["checks"] if check["key"] == "database")
    assert database["state"] == "attention"
    assert "manual review" in database["detail"]


def test_acknowledgement_clears_attention_without_unblocking_submission(environment: Path) -> None:
    from hayden_booker.config import database_path

    occurrence_id = seed_manual_review()
    repository = ReservationRepository(connect(database_path()))
    acknowledged = repository.acknowledge(occurrence_id)
    decision = repository.begin_submission(
        occurrence_id, "Study Room 311B", safety_timeout_minutes=15
    )
    repository.connection.close()

    assert acknowledged.acknowledged_at_utc is not None
    assert acknowledged.status is AttemptStatus.MANUAL_REVIEW_REQUIRED
    assert decision.allowed is False, "acknowledgement must not re-open automatic submission"

    status = health.build_status(environment)
    database = next(check for check in status["checks"] if check["key"] == "database")
    assert database["state"] == "ok"


def test_acknowledgement_is_idempotent(environment: Path) -> None:
    from hayden_booker.config import database_path

    occurrence_id = seed_manual_review()
    repository = ReservationRepository(connect(database_path()))
    first = repository.acknowledge(occurrence_id)
    second = repository.acknowledge(occurrence_id)
    repository.connection.close()
    assert first.acknowledged_at_utc == second.acknowledged_at_utc


def serve(config_path: Path) -> Any:
    server = create_server(config_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_dashboard_endpoints_serve_locally(environment: Path) -> None:
    repository = seeded_repository()
    occurrence_id = repository.list_occurrences(limit=1)[0].id
    repository.connection.close()
    server = serve(environment)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/") as response:
            assert response.status == 200
            assert b"Hayden Room Booker" in response.read()
        with urllib.request.urlopen(f"{base}/api/bookings") as response:
            payload = json.loads(response.read())
        assert payload["bookings"][0]["id"] == occurrence_id
        with urllib.request.urlopen(f"{base}/api/bookings/{occurrence_id}") as response:
            detail = json.loads(response.read())
        assert detail["events"], "detail view exposes the attempt timeline"
        with urllib.request.urlopen(
            f"{base}/api/bookings/{occurrence_id}/calendar.ics"
        ) as response:
            assert response.headers["Content-Type"].startswith("text/calendar")
            assert b"BEGIN:VCALENDAR" in response.read()
    finally:
        server.shutdown()
        server.server_close()


def test_acknowledge_endpoint_requires_the_dashboard_header(environment: Path) -> None:
    occurrence_id = seed_manual_review()
    server = serve(environment)
    port = server.server_address[1]
    path = f"/api/bookings/{occurrence_id}/acknowledge"
    try:
        forged = HTTPConnection("127.0.0.1", port, timeout=5)
        forged.request("POST", path, headers={"Host": "127.0.0.1"})
        assert forged.getresponse().status == 403, "a cross-origin form POST must be rejected"
        forged.close()

        allowed = HTTPConnection("127.0.0.1", port, timeout=5)
        allowed.request("POST", path, headers={"Host": "127.0.0.1", "X-Hayden-Dashboard": "1"})
        response = allowed.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["acknowledged"] is True
        allowed.close()
    finally:
        server.shutdown()
        server.server_close()


def test_config_endpoint_validates_and_persists_editable_settings(environment: Path) -> None:
    server = serve(environment)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{base}/api/config") as response:
            editable = json.loads(response.read())
        assert editable["known_rooms"]
        assert editable["timezone"] == "America/Phoenix"

        schedule = editable["schedules"][0]
        schedule["enabled"] = False
        schedule["room_preferences"] = ["Study Room C19", "Study Room 311A"]
        body = json.dumps({"live_booking_enabled": True, "schedules": editable["schedules"]})

        forged = HTTPConnection("127.0.0.1", port, timeout=5)
        forged.request(
            "POST",
            "/api/config",
            body=body,
            headers={"Content-Type": "application/json", "Host": "127.0.0.1"},
        )
        assert forged.getresponse().status == 403
        forged.close()

        allowed = HTTPConnection("127.0.0.1", port, timeout=5)
        allowed.request(
            "POST",
            "/api/config",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Host": "127.0.0.1",
                "X-Hayden-Dashboard": "1",
            },
        )
        response = allowed.getresponse()
        assert response.status == 200
        saved = json.loads(response.read())
        assert saved["live_booking_enabled"] is True
        allowed.close()

        config, _ = load_config(environment)
        assert config.live_booking_enabled is True
        assert config.schedules[0].enabled is False
        assert config.schedules[0].room_preferences[0] == "Study Room C19"
        assert config.libcal.base_url == "https://asu.libcal.com"
    finally:
        server.shutdown()
        server.server_close()


def test_invalid_config_edit_does_not_change_the_file(environment: Path) -> None:
    server = serve(environment)
    port = server.server_address[1]
    original = environment.read_text(encoding="utf-8")
    invalid_schedule = {
        "id": "bad-time",
        "enabled": True,
        "weekday": "monday",
        "start_time": "15:00",
        "end_time": "14:00",
        "room_preferences": ["Study Room C19"],
        "exact_time_required": True,
    }
    try:
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            "POST",
            "/api/config",
            body=json.dumps({"live_booking_enabled": True, "schedules": [invalid_schedule]}),
            headers={
                "Content-Type": "application/json",
                "Host": "127.0.0.1",
                "X-Hayden-Dashboard": "1",
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 422
        assert "later than start_time" in payload["error"]
        assert environment.read_text(encoding="utf-8") == original
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_rejects_unknown_paths_and_foreign_hosts(environment: Path) -> None:
    server = serve(environment)
    port = server.server_address[1]
    try:
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/api/bookings", headers={"Host": "evil.example.com"})
        assert connection.getresponse().status == 403
        connection.close()
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/../secrets", headers={"Host": "127.0.0.1"})
        assert connection.getresponse().status == 404
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
