from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from hayden_booker.constants import AttemptStatus
from hayden_booker.domain.models import OccurrenceKey, ReservationOccurrence

BLOCKING_STATUSES = {
    AttemptStatus.CONFIRMED,
    AttemptStatus.UNKNOWN_RESULT,
    AttemptStatus.MANUAL_REVIEW_REQUIRED,
}


@dataclass(frozen=True, slots=True)
class OccurrenceAcquisition:
    occurrence: ReservationOccurrence
    created: bool


@dataclass(frozen=True, slots=True)
class SubmissionDecision:
    allowed: bool
    occurrence: ReservationOccurrence
    reason: str | None = None


class ReservationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ensure_occurrence(self, key: OccurrenceKey) -> OccurrenceAcquisition:
        occurrence_id = str(uuid.uuid4())
        now = _utc_now()
        cursor = self.connection.execute(
            """
            INSERT INTO reservation_occurrences(
                id, schedule_id, target_date, start_time, end_time, status,
                attempt_count, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(schedule_id, target_date, start_time, end_time) DO NOTHING
            """,
            (
                occurrence_id,
                key.schedule_id,
                key.target_date.isoformat(),
                key.start_time,
                key.end_time,
                AttemptStatus.PLANNED.value,
                now,
                now,
            ),
        )
        created = cursor.rowcount == 1
        occurrence = self.get_by_key(key)
        if occurrence is None:  # pragma: no cover - defensive database guard
            raise RuntimeError("occurrence insert/read invariant failed")
        if created:
            self.add_event(occurrence.id, AttemptStatus.PLANNED.value)
        return OccurrenceAcquisition(occurrence, created)

    def get_by_key(self, key: OccurrenceKey) -> ReservationOccurrence | None:
        row = self.connection.execute(
            """
            SELECT * FROM reservation_occurrences
            WHERE schedule_id = ? AND target_date = ? AND start_time = ? AND end_time = ?
            """,
            (key.schedule_id, key.target_date.isoformat(), key.start_time, key.end_time),
        ).fetchone()
        return _occurrence_from_row(row) if row else None

    def get(self, occurrence_id: str) -> ReservationOccurrence:
        row = self.connection.execute(
            "SELECT * FROM reservation_occurrences WHERE id = ?", (occurrence_id,)
        ).fetchone()
        if row is None:
            raise KeyError(occurrence_id)
        return _occurrence_from_row(row)

    def update_status(
        self,
        occurrence_id: str,
        status: AttemptStatus,
        *,
        room: str | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
        confirmation_reference: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ReservationOccurrence:
        if error_summary and len(error_summary) > 500:
            error_summary = error_summary[:497] + "..."
        now = _utc_now()
        confirmed_at = now if status is AttemptStatus.CONFIRMED else None
        with self.connection:
            self.connection.execute(
                """
                UPDATE reservation_occurrences
                SET status = ?, chosen_room = COALESCE(?, chosen_room), updated_at_utc = ?,
                    confirmed_at_utc = COALESCE(?, confirmed_at_utc),
                    confirmation_reference = COALESCE(?, confirmation_reference),
                    last_error_code = ?, last_error_summary = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    room,
                    now,
                    confirmed_at,
                    confirmation_reference,
                    error_code,
                    error_summary,
                    occurrence_id,
                ),
            )
            self.add_event(occurrence_id, status.value, room=room, details=details)
        return self.get(occurrence_id)

    def begin_submission(
        self,
        occurrence_id: str,
        room: str,
        *,
        safety_timeout_minutes: int,
    ) -> SubmissionDecision:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.get(occurrence_id)
            if current.status in BLOCKING_STATUSES:
                self.connection.rollback()
                return SubmissionDecision(False, current, f"status is {current.status.value}")
            if current.status is AttemptStatus.SUBMITTING:
                cutoff = datetime.now(UTC) - timedelta(minutes=safety_timeout_minutes)
                if current.updated_at_utc >= cutoff:
                    self.connection.rollback()
                    return SubmissionDecision(False, current, "another submission is in progress")
                now = _utc_now()
                self.connection.execute(
                    """
                    UPDATE reservation_occurrences
                    SET status = ?, updated_at_utc = ?, last_error_code = ?, last_error_summary = ?
                    WHERE id = ?
                    """,
                    (
                        AttemptStatus.MANUAL_REVIEW_REQUIRED.value,
                        now,
                        "STALE_SUBMISSION",
                        "A previous submission did not reach a known result.",
                        occurrence_id,
                    ),
                )
                self.add_event(
                    occurrence_id,
                    AttemptStatus.MANUAL_REVIEW_REQUIRED.value,
                    details={"reason": "stale_submission"},
                )
                self.connection.commit()
                return SubmissionDecision(
                    False,
                    self.get(occurrence_id),
                    "stale submission requires manual review",
                )
            now = _utc_now()
            self.connection.execute(
                """
                UPDATE reservation_occurrences
                SET status = ?, chosen_room = ?, attempt_count = attempt_count + 1,
                    updated_at_utc = ?, last_error_code = NULL, last_error_summary = NULL
                WHERE id = ?
                """,
                (AttemptStatus.SUBMITTING.value, room, now, occurrence_id),
            )
            self.add_event(
                occurrence_id,
                AttemptStatus.SUBMITTING.value,
                room=room,
                details={"submission_gate": "passed"},
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return SubmissionDecision(True, self.get(occurrence_id))

    def add_event(
        self,
        occurrence_id: str,
        event_type: str,
        *,
        room: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO attempt_events(
                id, occurrence_id, event_type, occurred_at_utc, room, details_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                occurrence_id,
                event_type,
                _utc_now(),
                room,
                json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
            ),
        )

    def list_occurrences(self, *, limit: int = 50) -> list[ReservationOccurrence]:
        rows = self.connection.execute(
            "SELECT * FROM reservation_occurrences ORDER BY updated_at_utc DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_occurrence_from_row(row) for row in rows]

    def processed_dates(self, schedule_id: str) -> set[date]:
        rows = self.connection.execute(
            """
            SELECT target_date FROM reservation_occurrences
            WHERE schedule_id = ? AND status IN (?, ?, ?, ?)
            """,
            (
                schedule_id,
                AttemptStatus.CONFIRMED.value,
                AttemptStatus.SUBMITTING.value,
                AttemptStatus.UNKNOWN_RESULT.value,
                AttemptStatus.MANUAL_REVIEW_REQUIRED.value,
            ),
        ).fetchall()
        return {date.fromisoformat(str(row[0])) for row in rows}

    def add_release_observation(
        self,
        *,
        local_date: date,
        furthest_visible_date: date | None,
        target_visible: bool,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO release_observations(
                observed_at_utc, local_date, furthest_visible_date, target_visible
            ) VALUES (?, ?, ?, ?)
            """,
            (
                _utc_now(),
                local_date.isoformat(),
                furthest_visible_date.isoformat() if furthest_visible_date else None,
                int(target_visible),
            ),
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_required_utc(value: str) -> datetime:
    parsed = _parse_utc(value)
    if parsed is None:  # pragma: no cover - constrained by the database schema
        raise ValueError("required UTC timestamp is null")
    return parsed


def _occurrence_from_row(row: sqlite3.Row) -> ReservationOccurrence:
    return ReservationOccurrence(
        id=str(row["id"]),
        schedule_id=str(row["schedule_id"]),
        target_date=date.fromisoformat(str(row["target_date"])),
        start_time=str(row["start_time"]),
        end_time=str(row["end_time"]),
        chosen_room=str(row["chosen_room"]) if row["chosen_room"] is not None else None,
        status=AttemptStatus(str(row["status"])),
        attempt_count=int(row["attempt_count"]),
        created_at_utc=_parse_required_utc(str(row["created_at_utc"])),
        updated_at_utc=_parse_required_utc(str(row["updated_at_utc"])),
        confirmed_at_utc=_parse_utc(
            str(row["confirmed_at_utc"]) if row["confirmed_at_utc"] is not None else None
        ),
        confirmation_reference=(
            str(row["confirmation_reference"])
            if row["confirmation_reference"] is not None
            else None
        ),
        last_error_code=(
            str(row["last_error_code"]) if row["last_error_code"] is not None else None
        ),
        last_error_summary=(
            str(row["last_error_summary"]) if row["last_error_summary"] is not None else None
        ),
    )
