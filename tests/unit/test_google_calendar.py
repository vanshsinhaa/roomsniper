from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from google.auth.exceptions import RefreshError

from hayden_booker.config import AppConfig, CalendarConfig
from hayden_booker.constants import AttemptStatus
from hayden_booker.domain.models import OccurrenceKey, ReservationOccurrence
from hayden_booker.persistence.database import connect
from hayden_booker.persistence.repository import ReservationRepository
from hayden_booker.sample_config import SAMPLE_CONFIG
from hayden_booker.services import google_calendar
from hayden_booker.services.google_calendar import CalendarSyncError, GoogleCalendarClient
from hayden_booker.services.runner import BookingRunner


class FakeCredentials:
    def to_json(self) -> str:
        return '{"token":"refreshed"}'


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(
        self, post_response: FakeResponse, get_response: FakeResponse | None = None
    ) -> None:
        self.post_response = post_response
        self.get_response = get_response
        self.post_url = ""
        self.post_json: dict[str, Any] = {}
        self.get_url = ""

    def post(self, url: str, *, json: dict[str, Any], timeout: int) -> FakeResponse:
        self.post_url = url
        self.post_json = json
        assert timeout == 20
        return self.post_response

    def get(self, url: str, *, timeout: int) -> FakeResponse:
        self.get_url = url
        assert timeout == 20
        assert self.get_response is not None
        return self.get_response


class FailingSession(FakeSession):
    def post(self, url: str, *, json: dict[str, Any], timeout: int) -> FakeResponse:
        raise RefreshError("revoked token should not escape into the booking flow")


class FailingCalendar:
    def add_confirmed_booking(self, occurrence: ReservationOccurrence) -> None:
        raise CalendarSyncError("Authorization expired")


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[str] = []

    def event(self, event: str, **fields: Any) -> None:
        self.events.append(event)


def confirmed_occurrence(tmp_path: Path) -> ReservationOccurrence:
    repository = ReservationRepository(connect(tmp_path / "history.sqlite3"))
    occurrence = repository.ensure_occurrence(
        OccurrenceKey(
            schedule_id="monday-afternoon",
            target_date=date(2026, 8, 24),
            start_time="13:30",
            end_time="15:30",
        )
    ).occurrence
    confirmed = repository.update_status(
        occurrence.id,
        AttemptStatus.CONFIRMED,
        room="Study Room 311A",
        confirmation_reference="LC-123",
    )
    repository.connection.close()
    return confirmed


def install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    session: FakeSession,
) -> None:
    credentials = FakeCredentials()
    monkeypatch.setattr(google_calendar, "_load_credentials", lambda: credentials)
    monkeypatch.setattr(google_calendar, "AuthorizedSession", lambda _: session)
    monkeypatch.setattr(google_calendar, "_save_refreshed_credentials", lambda _: None)


def test_add_confirmed_booking_builds_idempotent_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occurrence = confirmed_occurrence(tmp_path)
    session = FakeSession(FakeResponse(201, {"id": occurrence.id.replace("-", "")}))
    install_fakes(monkeypatch, session)

    synced = GoogleCalendarClient(
        CalendarConfig(calendar_id="primary"),
        zone=ZoneInfo("America/Phoenix"),
    ).add_confirmed_booking(occurrence)

    assert synced.event_id == occurrence.id.replace("-", "")
    assert synced.already_existed is False
    assert session.post_url.endswith("/calendars/primary/events")
    assert session.post_json["summary"] == "Hayden Library - Study Room 311A"
    assert session.post_json["start"] == {
        "dateTime": "2026-08-24T13:30:00-07:00",
        "timeZone": "America/Phoenix",
    }
    assert session.post_json["extendedProperties"]["private"] == {
        "haydenOccurrenceId": occurrence.id
    }


def test_existing_deterministic_event_is_a_successful_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occurrence = confirmed_occurrence(tmp_path)
    session = FakeSession(FakeResponse(409), FakeResponse(200, {"id": "existing"}))
    install_fakes(monkeypatch, session)

    synced = GoogleCalendarClient(
        CalendarConfig(),
        zone=ZoneInfo("America/Phoenix"),
    ).add_confirmed_booking(occurrence)

    assert synced.already_existed is True
    assert session.get_url.endswith(occurrence.id.replace("-", ""))


def test_api_error_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occurrence = confirmed_occurrence(tmp_path)
    session = FakeSession(FakeResponse(403, {"error": {"message": "Permission denied"}}))
    install_fakes(monkeypatch, session)

    with pytest.raises(CalendarSyncError, match="HTTP 403: Permission denied"):
        GoogleCalendarClient(
            CalendarConfig(),
            zone=ZoneInfo("America/Phoenix"),
        ).add_confirmed_booking(occurrence)


def test_revoked_authorization_becomes_a_calendar_sync_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occurrence = confirmed_occurrence(tmp_path)
    install_fakes(monkeypatch, FailingSession(FakeResponse(500)))

    with pytest.raises(CalendarSyncError, match="RefreshError"):
        GoogleCalendarClient(
            CalendarConfig(),
            zone=ZoneInfo("America/Phoenix"),
        ).add_confirmed_booking(occurrence)


@pytest.mark.asyncio
async def test_calendar_failure_never_downgrades_confirmed_booking(tmp_path: Path) -> None:
    repository = ReservationRepository(connect(tmp_path / "history.sqlite3"))
    occurrence = repository.ensure_occurrence(
        OccurrenceKey(
            schedule_id="monday-afternoon",
            target_date=date(2026, 8, 24),
            start_time="13:30",
            end_time="15:30",
        )
    ).occurrence
    repository.update_status(occurrence.id, AttemptStatus.CONFIRMED, room="Study Room 311A")
    config = AppConfig.model_validate(yaml.safe_load(SAMPLE_CONFIG))
    config.calendar.enabled = True
    logger = RecordingLogger()
    runner = BookingRunner(config, repository, logger)  # type: ignore[arg-type]
    runner.calendar = FailingCalendar()  # type: ignore[assignment]

    message = await runner._sync_calendar(occurrence.id)

    saved = repository.get(occurrence.id)
    assert "booking is still confirmed" in message
    assert saved.status is AttemptStatus.CONFIRMED
    assert saved.calendar_sync_error == "Authorization expired"
    assert logger.events == ["calendar_sync_failed"]
