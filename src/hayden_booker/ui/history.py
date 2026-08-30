from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from hayden_booker.calendar_events import (
    CALENDAR_LOCATION,
    event_description,
    event_details,
    event_title,
)
from hayden_booker.constants import AttemptStatus
from hayden_booker.domain.models import ReservationOccurrence
from hayden_booker.persistence.repository import ReservationRepository

GOOGLE_CALENDAR_ENDPOINT = "https://calendar.google.com/calendar/render"

SUCCESS_STATUSES = frozenset({AttemptStatus.CONFIRMED})
PENDING_STATUSES = frozenset(
    {
        AttemptStatus.PLANNED,
        AttemptStatus.CHECKING_AVAILABILITY,
        AttemptStatus.SUBMITTING,
        AttemptStatus.DRY_RUN_COMPLETE,
    }
)


def list_bookings(
    repository: ReservationRepository,
    *,
    timezone: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    zone = ZoneInfo(timezone)
    return [
        booking_payload(occurrence, zone=zone)
        for occurrence in repository.list_occurrences(limit=limit)
    ]


def booking_detail(
    repository: ReservationRepository,
    occurrence_id: str,
    *,
    timezone: str,
) -> dict[str, Any]:
    occurrence = repository.get(occurrence_id)
    payload = booking_payload(occurrence, zone=ZoneInfo(timezone))
    payload["events"] = [
        {
            "id": event.id,
            "event_type": event.event_type,
            "occurred_at_utc": _iso(event.occurred_at_utc),
            "room": event.room,
            "details": event.details,
        }
        for event in repository.list_events(occurrence_id)
    ]
    return payload


def booking_payload(occurrence: ReservationOccurrence, *, zone: ZoneInfo) -> dict[str, Any]:
    details = event_details(occurrence, zone=zone)
    start_local = details.start
    end_local = details.end
    return {
        "id": occurrence.id,
        "schedule_id": occurrence.schedule_id,
        "target_date": occurrence.target_date.isoformat(),
        "weekday": occurrence.target_date.strftime("%A"),
        "start_time": occurrence.start_time,
        "end_time": occurrence.end_time,
        "duration_minutes": int((end_local - start_local).total_seconds() // 60),
        "room": occurrence.chosen_room,
        "status": occurrence.status.value,
        "outcome": _outcome(occurrence.status),
        "attempt_count": occurrence.attempt_count,
        "created_at_utc": _iso(occurrence.created_at_utc),
        "updated_at_utc": _iso(occurrence.updated_at_utc),
        "confirmed_at_utc": _iso(occurrence.confirmed_at_utc),
        "confirmation_reference": occurrence.confirmation_reference,
        "last_error_code": occurrence.last_error_code,
        "last_error_summary": occurrence.last_error_summary,
        "acknowledged_at_utc": _iso(occurrence.acknowledged_at_utc),
        "acknowledged": occurrence.acknowledged_at_utc is not None,
        "timezone": str(zone),
        "start_local": start_local.isoformat(),
        "end_local": end_local.isoformat(),
        "calendar": {
            "title": details.title,
            "location": CALENDAR_LOCATION,
            "google_url": google_calendar_url(occurrence, zone=zone),
            "ics_path": f"/api/bookings/{occurrence.id}/calendar.ics",
            "synced": occurrence.calendar_synced_at_utc is not None,
            "event_id": occurrence.calendar_event_id,
            "synced_at_utc": _iso(occurrence.calendar_synced_at_utc),
            "sync_error": occurrence.calendar_sync_error,
        },
    }


def google_calendar_url(occurrence: ReservationOccurrence, *, zone: ZoneInfo) -> str:
    details = event_details(occurrence, zone=zone)
    query = urlencode(
        {
            "action": "TEMPLATE",
            "text": details.title,
            "dates": f"{_compact(details.start)}/{_compact(details.end)}",
            "ctz": str(zone),
            "location": CALENDAR_LOCATION,
            "details": details.description,
        }
    )
    return f"{GOOGLE_CALENDAR_ENDPOINT}?{query}"


def ics_document(occurrence: ReservationOccurrence, *, zone: ZoneInfo) -> str:
    details = event_details(occurrence, zone=zone)
    start_utc = details.start.astimezone(UTC)
    end_utc = details.end.astimezone(UTC)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Hayden Room Booker//Local Dashboard//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{occurrence.id}@hayden-room-booker.local",
        f"DTSTAMP:{_compact(datetime.now(UTC))}Z",
        f"DTSTART:{_compact(start_utc)}Z",
        f"DTEND:{_compact(end_utc)}Z",
        f"SUMMARY:{_escape(event_title(occurrence))}",
        f"LOCATION:{_escape(CALENDAR_LOCATION)}",
        f"DESCRIPTION:{_escape(event_description(occurrence))}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def _outcome(status: AttemptStatus) -> str:
    if status in SUCCESS_STATUSES:
        return "confirmed"
    if status in PENDING_STATUSES:
        return "pending"
    return "failed"


def _compact(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
