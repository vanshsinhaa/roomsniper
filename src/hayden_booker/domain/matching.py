from __future__ import annotations

from dataclasses import dataclass

from hayden_booker.domain.models import RoomAvailability
from hayden_booker.time_utils import required_half_hours


@dataclass(frozen=True, slots=True)
class RoomMatch:
    room: str
    required_starts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RejectedRoom:
    room: str
    missing_starts: tuple[str, ...]


def choose_room(
    availability: list[RoomAvailability],
    preferences: list[str],
    start_time: str,
    end_time: str,
    *,
    excluded: set[str] | None = None,
) -> tuple[RoomMatch | None, tuple[RejectedRoom, ...]]:
    required = required_half_hours(start_time, end_time)
    by_room = {room.room: room for room in availability}
    rejected: list[RejectedRoom] = []
    excluded = excluded or set()
    for room_name in preferences:
        if room_name in excluded:
            continue
        room = by_room.get(room_name)
        enabled = room.enabled_starts if room else frozenset()
        missing = tuple(slot for slot in required if slot not in enabled)
        if not missing:
            return RoomMatch(room_name, required), tuple(rejected)
        rejected.append(RejectedRoom(room_name, missing))
    return None, tuple(rejected)
