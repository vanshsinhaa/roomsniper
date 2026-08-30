from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from hayden_booker.domain.models import ReservationOccurrence

CALENDAR_LOCATION = "Hayden Library, Arizona State University, Tempe, AZ"


@dataclass(frozen=True, slots=True)
class CalendarEventDetails:
    event_id: str
    title: str
    description: str
    location: str
    start: datetime
    end: datetime


def event_details(
    occurrence: ReservationOccurrence,
    *,
    zone: ZoneInfo,
) -> CalendarEventDetails:
    return CalendarEventDetails(
        event_id=occurrence.id.replace("-", ""),
        title=event_title(occurrence),
        description=event_description(occurrence),
        location=CALENDAR_LOCATION,
        start=local_datetime(occurrence.target_date, occurrence.start_time, zone),
        end=local_datetime(occurrence.target_date, occurrence.end_time, zone),
    )


def event_title(occurrence: ReservationOccurrence) -> str:
    room = occurrence.chosen_room or "Study room"
    return f"Hayden Library - {room}"


def event_description(occurrence: ReservationOccurrence) -> str:
    parts = [
        f"Schedule: {occurrence.schedule_id}",
        f"Status: {occurrence.status.value}",
    ]
    if occurrence.confirmation_reference:
        parts.append(f"Confirmation: {occurrence.confirmation_reference}")
    parts.append("Booked by Hayden Room Booker.")
    return " | ".join(parts)


def local_datetime(target_date: date, clock: str, zone: ZoneInfo) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=zone)
