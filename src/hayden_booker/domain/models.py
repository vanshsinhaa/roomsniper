from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from hayden_booker.constants import AttemptStatus


class Slot(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: str
    end: str
    enabled: bool = True
    selected: bool = False


class RoomAvailability(BaseModel):
    model_config = ConfigDict(frozen=True)

    room: str
    slots: tuple[Slot, ...]

    @property
    def enabled_starts(self) -> frozenset[str]:
        return frozenset(slot.start for slot in self.slots if slot.enabled)


class OccurrenceKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    schedule_id: str
    target_date: date
    start_time: str
    end_time: str


class AttemptEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    occurrence_id: str
    event_type: str
    occurred_at_utc: datetime
    room: str | None
    details: dict[str, Any]


class ReservationOccurrence(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    schedule_id: str
    target_date: date
    start_time: str
    end_time: str
    chosen_room: str | None
    status: AttemptStatus
    attempt_count: int
    created_at_utc: datetime
    updated_at_utc: datetime
    confirmed_at_utc: datetime | None
    confirmation_reference: str | None
    last_error_code: str | None
    last_error_summary: str | None
    acknowledged_at_utc: datetime | None = None
