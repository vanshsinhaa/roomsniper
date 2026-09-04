from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from hayden_booker import booking_log
from hayden_booker.constants import AttemptStatus
from hayden_booker.domain.models import OccurrenceKey
from hayden_booker.persistence.database import connect
from hayden_booker.persistence.repository import ReservationRepository
from hayden_booker.sample_config import SAMPLE_CONFIG

KEY = OccurrenceKey(
    schedule_id="monday-2pm",
    target_date=date(2026, 8, 24),
    start_time="14:00",
    end_time="16:00",
)


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReservationRepository:
    monkeypatch.setenv("HAYDEN_BOOKER_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "config.yaml").write_text(SAMPLE_CONFIG, encoding="utf-8")
    from hayden_booker.config import database_path

    return ReservationRepository(connect(database_path()))


def confirmed_record(repository: ReservationRepository) -> dict[str, object]:
    occurrence = repository.ensure_occurrence(KEY).occurrence
    repository.update_status(
        occurrence.id,
        AttemptStatus.CONFIRMED,
        room="Study Room C19",
        confirmation_reference="LIBCAL-secret-1234",
    )
    return booking_log.record_from_occurrence(
        repository.get(occurrence.id), timezone="America/Phoenix"
    )


def test_record_is_sanitized_by_default(repository: ReservationRepository) -> None:
    record = confirmed_record(repository)
    assert record["room"] == "Study Room C19"
    assert record["outcome"] == "confirmed"
    assert record["duration_minutes"] == 120
    assert record["weekday"] == "Monday"
    assert "confirmation_reference" not in record


def test_record_can_opt_into_references(repository: ReservationRepository) -> None:
    occurrence = repository.ensure_occurrence(KEY).occurrence
    repository.update_status(
        occurrence.id,
        AttemptStatus.CONFIRMED,
        room="Study Room C19",
        confirmation_reference="LIBCAL-secret-1234",
    )
    record = booking_log.record_from_occurrence(
        repository.get(occurrence.id), timezone="America/Phoenix", include_references=True
    )
    assert record["confirmation_reference"] == "LIBCAL-secret-1234"


def test_failed_attempts_are_logged_as_failed(repository: ReservationRepository) -> None:
    occurrence = repository.ensure_occurrence(KEY).occurrence
    repository.update_status(occurrence.id, AttemptStatus.NO_AVAILABILITY)
    record = booking_log.record_from_occurrence(
        repository.get(occurrence.id), timezone="America/Phoenix"
    )
    assert record["outcome"] == "failed"
    assert record["room"] is None


def test_merge_deduplicates_by_id_and_keeps_the_newer_copy() -> None:
    first = {"id": "a", "logged_at_utc": "2026-08-01T00:00:00Z", "room": "342"}
    updated = {"id": "a", "logged_at_utc": "2026-08-01T00:00:00Z", "room": "C19"}
    second = {"id": "b", "logged_at_utc": "2026-08-02T00:00:00Z", "room": "357"}
    merged = booking_log.merge_records([first], [updated, second])
    assert [record["id"] for record in merged] == ["a", "b"]
    assert merged[0]["room"] == "C19"


def test_ledger_round_trip_is_byte_stable(tmp_path: Path) -> None:
    records = [
        {"id": "a", "logged_at_utc": "2026-08-01T00:00:00Z"},
        {"id": "b", "logged_at_utc": "2026-08-02T00:00:00Z"},
    ]
    path = tmp_path / "bookings.json"
    booking_log.write_ledger(path, records)
    first = path.read_bytes()
    booking_log.write_ledger(path, booking_log.load_ledger(path))
    assert path.read_bytes() == first
    assert json.loads(first)["count"] == 2


def test_render_lists_newest_first_with_summary(repository: ReservationRepository) -> None:
    record = confirmed_record(repository)
    older = dict(record, id="older", logged_at_utc="2020-01-01T00:00:00Z", target_date="2020-01-01")
    markdown = booking_log.render_markdown([record, older])
    body = markdown.splitlines()
    assert "- Confirmed bookings: **2**" in body
    assert "- Hours reserved: **4.0**" in body
    assert "| Study Room C19 | 2 |" in body
    rows = [line for line in body if line.startswith("| 2")]
    assert rows[0].startswith("| 2026-08-24")
    assert rows[-1].startswith("| 2020-01-01")


def test_render_escapes_pipes_in_room_names() -> None:
    record = {
        "id": "a",
        "target_date": "2026-08-24",
        "weekday": "Monday",
        "start_time": "14:00",
        "end_time": "15:00",
        "room": "Room | A",
        "status": "CONFIRMED",
        "outcome": "confirmed",
        "attempt_count": 1,
        "logged_at_utc": "2026-08-24T00:00:00Z",
    }
    assert "Room \\| A" in booking_log.render_markdown([record])


def test_commit_message_is_conventional(repository: ReservationRepository) -> None:
    message = booking_log.commit_message(confirmed_record(repository))
    subject = message.splitlines()[0]
    assert subject == "chore(bookings): log 2026-08-24 14:00-16:00 Study Room C19"
    assert "Outcome: confirmed (CONFIRMED)" in message
