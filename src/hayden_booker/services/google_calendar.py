from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from requests import RequestException, Response

from hayden_booker.calendar_events import event_details
from hayden_booker.config import CalendarConfig
from hayden_booker.constants import AttemptStatus
from hayden_booker.domain.models import ReservationOccurrence
from hayden_booker.security.secrets import (
    SecretStoreError,
    get_google_calendar_credentials,
    set_google_calendar_credentials,
)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
CALENDAR_API_ROOT = "https://www.googleapis.com/calendar/v3"


class CalendarAuthorizationError(RuntimeError):
    pass


class CalendarSyncError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SyncedCalendarEvent:
    event_id: str
    already_existed: bool = False


def authorize_google_calendar(client_secrets_path: Path) -> None:
    if not client_secrets_path.is_file():
        raise CalendarAuthorizationError(
            f"Google OAuth client file was not found: {client_secrets_path}"
        )
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secrets_path), scopes=[CALENDAR_SCOPE]
        )
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=True,
            authorization_prompt_message="Opening Google authorization in your browser...",
            success_message="Google Calendar connected. You can close this tab.",
        )
        set_google_calendar_credentials(str(credentials.to_json()))
    except SecretStoreError:
        raise
    except Exception as exc:
        raise CalendarAuthorizationError(
            f"Google authorization could not start: {type(exc).__name__}"
        ) from exc


class GoogleCalendarClient:
    def __init__(self, config: CalendarConfig, *, zone: ZoneInfo) -> None:
        self.config = config
        self.zone = zone

    def add_confirmed_booking(
        self,
        occurrence: ReservationOccurrence,
    ) -> SyncedCalendarEvent:
        if occurrence.status is not AttemptStatus.CONFIRMED:
            raise CalendarSyncError("Only confirmed bookings can be added to Google Calendar.")
        credentials = _load_credentials()
        session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        details = event_details(occurrence, zone=self.zone)
        event_url = self._event_url(details.event_id)
        try:
            response = session.post(
                self._events_url(),
                json={
                    "id": details.event_id,
                    "summary": details.title,
                    "description": details.description,
                    "location": details.location,
                    "start": {
                        "dateTime": details.start.isoformat(),
                        "timeZone": str(self.zone),
                    },
                    "end": {
                        "dateTime": details.end.isoformat(),
                        "timeZone": str(self.zone),
                    },
                    "extendedProperties": {"private": {"haydenOccurrenceId": occurrence.id}},
                },
                timeout=self.config.request_timeout_seconds,
            )
            if response.status_code == 409:
                existing = session.get(
                    event_url,
                    timeout=self.config.request_timeout_seconds,
                )
                if existing.status_code == 200:
                    _save_refreshed_credentials(credentials)
                    return SyncedCalendarEvent(details.event_id, already_existed=True)
                raise CalendarSyncError(_response_error(existing))
            if response.status_code not in {200, 201}:
                raise CalendarSyncError(_response_error(response))
        except (GoogleAuthError, RequestException) as exc:
            raise CalendarSyncError(
                f"Google Calendar request failed: {type(exc).__name__}"
            ) from exc
        _save_refreshed_credentials(credentials)
        return SyncedCalendarEvent(details.event_id)

    def _events_url(self) -> str:
        calendar_id = quote(self.config.calendar_id, safe="")
        return f"{CALENDAR_API_ROOT}/calendars/{calendar_id}/events"

    def _event_url(self, event_id: str) -> str:
        return f"{self._events_url()}/{quote(event_id, safe='')}"


def _load_credentials() -> Credentials:
    try:
        serialized = get_google_calendar_credentials()
    except SecretStoreError as exc:
        raise CalendarSyncError(str(exc)) from exc
    if not serialized:
        raise CalendarSyncError(
            "Google Calendar is not connected; run `hayden-booker calendar connect`."
        )
    try:
        parsed = json.loads(serialized)
        if not isinstance(parsed, dict):
            raise ValueError("credential payload is not an object")
        credentials = Credentials.from_authorized_user_info(  # type: ignore[no-untyped-call]
            parsed, scopes=[CALENDAR_SCOPE]
        )
        if not isinstance(credentials, Credentials):  # pragma: no cover - library contract
            raise ValueError("credential factory returned an unexpected value")
        return credentials
    except (TypeError, ValueError) as exc:
        raise CalendarSyncError(
            "Stored Google Calendar authorization is invalid; reconnect the calendar."
        ) from exc


def _save_refreshed_credentials(credentials: Credentials) -> None:
    try:
        serialized = credentials.to_json()  # type: ignore[no-untyped-call]
        set_google_calendar_credentials(str(serialized))
    except SecretStoreError:
        # A successful event insert is authoritative. A failed best-effort token refresh write
        # must not make the caller retry and create a duplicate event.
        return


def _response_error(response: Response) -> str:
    message = "request was rejected"
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = str(error["message"]).strip()[:300]
    return f"Google Calendar API returned HTTP {response.status_code}: {message}"
