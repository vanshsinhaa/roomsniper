"""Sanitized public booking ledger: JSON records plus the rendered Markdown table.

The ledger is the only booking data that ever leaves the local machine. It carries dates,
times, room names, and outcomes; it never carries the school ID, the browser session, or
LibCal confirmation references unless the operator opts in explicitly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # keep module import stdlib-only so CI can render without installing deps
    from hayden_booker.constants import AttemptStatus
    from hayden_booker.domain.models import ReservationOccurrence
    from hayden_booker.persistence.repository import ReservationRepository

LEDGER_PATH = Path("docs/bookings.json")
MARKDOWN_PATH = Path("BOOKINGS.md")
LEDGER_VERSION = 1

_TABLE_HEADER = (
    "| Date | Day | Time | Room | Outcome | Status | Attempts | Logged (UTC) |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |"
)


def outcome_for(status: AttemptStatus) -> str:
    from hayden_booker.ui.history import PENDING_STATUSES, SUCCESS_STATUSES

    if status in SUCCESS_STATUSES:
        return "confirmed"
    if status in PENDING_STATUSES:
        return "pending"
    return "failed"


def record_from_occurrence(
    occurrence: ReservationOccurrence,
    *,
    timezone: str,
    include_references: bool = False,
) -> dict[str, Any]:
    """Project one occurrence onto the sanitized ledger shape."""
    from zoneinfo import ZoneInfo

    from hayden_booker.calendar_events import local_datetime

    zone = ZoneInfo(timezone)
    start_local = local_datetime(occurrence.target_date, occurrence.start_time, zone)
    end_local = local_datetime(occurrence.target_date, occurrence.end_time, zone)
    logged_at = (
        occurrence.confirmed_at_utc or occurrence.updated_at_utc or occurrence.created_at_utc
    )
    record: dict[str, Any] = {
        "id": occurrence.id,
        "schedule_id": occurrence.schedule_id,
        "target_date": occurrence.target_date.isoformat(),
        "weekday": occurrence.target_date.strftime("%A"),
        "start_time": occurrence.start_time,
        "end_time": occurrence.end_time,
        "duration_minutes": int((end_local - start_local).total_seconds() // 60),
        "room": occurrence.chosen_room,
        "status": occurrence.status.value,
        "outcome": outcome_for(occurrence.status),
        "attempt_count": occurrence.attempt_count,
        "timezone": timezone,
        "created_at_utc": _iso(occurrence.created_at_utc),
        "confirmed_at_utc": _iso(occurrence.confirmed_at_utc),
        "logged_at_utc": _iso(logged_at),
        "error_code": occurrence.last_error_code,
    }
    if include_references:
        record["confirmation_reference"] = occurrence.confirmation_reference
    return record


def records_from_repository(
    repository: ReservationRepository,
    *,
    timezone: str,
    limit: int = 500,
    include_references: bool = False,
) -> list[dict[str, Any]]:
    return [
        record_from_occurrence(occurrence, timezone=timezone, include_references=include_references)
        for occurrence in repository.list_occurrences(limit=limit)
    ]


def merge_records(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Combine ledgers, letting incoming records win on identical ids, then sort."""
    by_id = {record["id"]: record for record in existing}
    for record in incoming:
        by_id[record["id"]] = record
    return sort_records(list(by_id.values()))


def sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: (
            str(record.get("logged_at_utc") or ""),
            str(record.get("target_date") or ""),
            str(record.get("start_time") or ""),
            str(record.get("id") or ""),
        ),
    )


def load_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):  # tolerate a bare array from an older ledger version
        return list(payload)
    return list(payload.get("bookings", []))


def write_ledger(path: Path, records: list[dict[str, Any]]) -> None:
    # Deterministic on purpose: no wall-clock field, so an unchanged ledger produces an
    # unchanged file and CI never manufactures an empty commit.
    payload = {
        "version": LEDGER_VERSION,
        "count": len(records),
        "bookings": sort_records(records),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def render_markdown(records: list[dict[str, Any]]) -> str:
    ordered = list(reversed(sort_records(records)))
    confirmed = [record for record in ordered if record.get("outcome") == "confirmed"]
    rooms = _room_tally(confirmed)
    favourite = f"{rooms[0][0]} ({rooms[0][1]} bookings)" if rooms else "-"
    lines = [
        "# Booking log",
        "",
        "Every reservation attempt Hayden Room Booker has made, newest first. This file is",
        "generated from [`docs/bookings.json`](docs/bookings.json) - edit the ledger, not the",
        "table.",
        "",
        "## Summary",
        "",
        f"- Attempts logged: **{len(ordered)}**",
        f"- Confirmed bookings: **{len(confirmed)}**",
        f"- Hours reserved: **{_hours(confirmed)}**",
        f"- Favourite room: **{favourite}**",
        "",
        "## Rooms",
        "",
    ]
    if rooms:
        lines.append("| Room | Confirmed bookings |")
        lines.append("| --- | --- |")
        lines.extend(f"| {room} | {count} |" for room, count in rooms)
    else:
        lines.append("No confirmed bookings yet.")
    lines.extend(["", "## Bookings", ""])
    if ordered:
        lines.append(_TABLE_HEADER)
        lines.extend(_row(record) for record in ordered)
    else:
        lines.append("No reservation attempts recorded.")
    return "\n".join(lines) + "\n"


def commit_message(record: dict[str, Any]) -> str:
    """Conventional-commit subject and body for one logged booking."""
    room = record.get("room") or "no room"
    subject = (
        f"chore(bookings): log {record.get('target_date')} "
        f"{record.get('start_time')}-{record.get('end_time')} {room}"
    )
    body = "\n".join(
        [
            f"Schedule: {record.get('schedule_id')}",
            f"Outcome: {record.get('outcome')} ({record.get('status')})",
            f"Room: {room}",
            f"Attempts: {record.get('attempt_count')}",
            f"Logged at: {record.get('logged_at_utc')}",
        ]
    )
    return f"{subject}\n\n{body}\n"


def _row(record: dict[str, Any]) -> str:
    cells = [
        str(record.get("target_date", "-")),
        str(record.get("weekday", "-")),
        f"{record.get('start_time', '?')}-{record.get('end_time', '?')}",
        str(record.get("room") or "-"),
        str(record.get("outcome", "-")),
        str(record.get("status", "-")),
        str(record.get("attempt_count", 0)),
        str(record.get("logged_at_utc") or "-"),
    ]
    return "| " + " | ".join(_escape(cell) for cell in cells) + " |"


def _room_tally(records: list[dict[str, Any]]) -> list[tuple[str, int]]:
    tally: dict[str, int] = {}
    for record in records:
        room = record.get("room")
        if not room:
            continue
        tally[str(room)] = tally.get(str(room), 0) + 1
    return sorted(tally.items(), key=lambda item: (-item[1], item[0]))


def _hours(records: list[dict[str, Any]]) -> str:
    minutes = sum(int(record.get("duration_minutes") or 0) for record in records)
    return f"{minutes / 60:.1f}"


def _escape(value: str) -> str:
    return value.replace("|", "\\|")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
