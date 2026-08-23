from __future__ import annotations

from hayden_booker.domain.matching import choose_room
from hayden_booker.domain.models import RoomAvailability, Slot
from hayden_booker.time_utils import parse_slot_label, required_half_hours


def room(name: str, starts: list[str]) -> RoomAvailability:
    return RoomAvailability(
        room=name,
        slots=tuple(Slot(start=start, end="unused", enabled=True) for start in starts),
    )


def test_normalizes_libcal_twelve_hour_slot() -> None:
    assert parse_slot_label("01:30 PM - 02:00 PM") == ("13:30", "14:00")
    assert parse_slot_label("12:00 AM \N{EN DASH} 12:30 AM") == ("00:00", "00:30")


def test_builds_contiguous_half_hour_interval() -> None:
    assert required_half_hours("13:30", "15:30") == (
        "13:30",
        "14:00",
        "14:30",
        "15:00",
    )


def test_room_preference_order_wins() -> None:
    availability = [
        room("Study Room 311B", ["13:30", "14:00"]),
        room("Study Room 311A", ["13:30", "14:00"]),
    ]
    match, rejected = choose_room(
        availability,
        ["Study Room 311A", "Study Room 311B"],
        "13:30",
        "14:30",
    )
    assert match is not None
    assert match.room == "Study Room 311A"
    assert rejected == ()


def test_partial_slots_are_never_combined_across_rooms() -> None:
    availability = [
        room("Study Room 311A", ["13:30"]),
        room("Study Room 311B", ["14:00"]),
    ]
    match, rejected = choose_room(
        availability,
        ["Study Room 311A", "Study Room 311B"],
        "13:30",
        "14:30",
    )
    assert match is None
    assert rejected[0].missing_starts == ("14:00",)
    assert rejected[1].missing_starts == ("13:30",)
