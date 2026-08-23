from __future__ import annotations

from datetime import date
from pathlib import Path

from hayden_booker.constants import AttemptStatus
from hayden_booker.domain.models import OccurrenceKey
from hayden_booker.persistence.database import connect
from hayden_booker.persistence.repository import ReservationRepository


def key() -> OccurrenceKey:
    return OccurrenceKey(
        schedule_id="monday",
        target_date=date(2026, 8, 24),
        start_time="13:30",
        end_time="15:30",
    )


def repository(path: Path) -> ReservationRepository:
    return ReservationRepository(connect(path))


def test_unique_occurrence_is_reused(tmp_path: Path) -> None:
    repo = repository(tmp_path / "history.sqlite3")
    first = repo.ensure_occurrence(key())
    second = repo.ensure_occurrence(key())
    assert first.created is True
    assert second.created is False
    assert first.occurrence.id == second.occurrence.id


def test_confirmed_occurrence_cannot_submit_again(tmp_path: Path) -> None:
    repo = repository(tmp_path / "history.sqlite3")
    occurrence = repo.ensure_occurrence(key()).occurrence
    repo.update_status(occurrence.id, AttemptStatus.CONFIRMED, room="Study Room 311A")
    decision = repo.begin_submission(occurrence.id, "Study Room 311B", safety_timeout_minutes=15)
    assert decision.allowed is False
    assert decision.occurrence.status is AttemptStatus.CONFIRMED


def test_recent_submitting_occurrence_blocks_duplicate(tmp_path: Path) -> None:
    repo = repository(tmp_path / "history.sqlite3")
    occurrence = repo.ensure_occurrence(key()).occurrence
    first = repo.begin_submission(occurrence.id, "Study Room 311A", safety_timeout_minutes=15)
    second = repo.begin_submission(occurrence.id, "Study Room 311A", safety_timeout_minutes=15)
    assert first.allowed is True
    assert second.allowed is False
    assert second.occurrence.attempt_count == 1


def test_stale_submitting_requires_manual_review(tmp_path: Path) -> None:
    repo = repository(tmp_path / "history.sqlite3")
    occurrence = repo.ensure_occurrence(key()).occurrence
    repo.begin_submission(occurrence.id, "Study Room 311A", safety_timeout_minutes=15)
    repo.connection.execute(
        "UPDATE reservation_occurrences SET updated_at_utc = ? WHERE id = ?",
        ("2020-01-01T00:00:00Z", occurrence.id),
    )
    decision = repo.begin_submission(occurrence.id, "Study Room 311A", safety_timeout_minutes=15)
    assert decision.allowed is False
    assert decision.occurrence.status is AttemptStatus.MANUAL_REVIEW_REQUIRED


def test_database_never_has_secret_columns(tmp_path: Path) -> None:
    repo = repository(tmp_path / "history.sqlite3")
    names = {
        str(row[1]).lower()
        for row in repo.connection.execute("PRAGMA table_info(reservation_occurrences)")
    }
    assert not {"school_id", "password", "cookie", "token"} & names
