from __future__ import annotations

from datetime import date

from hayden_booker.config import ScheduleRule
from hayden_booker.domain.schedule import resolve_target_date


def monday_rule() -> ScheduleRule:
    return ScheduleRule(
        id="monday",
        weekday="monday",
        start_time="13:30",
        end_time="15:30",
        room_preferences=["Study Room 311A"],
    )


def test_resolves_against_offered_dates_not_now_plus_seven() -> None:
    offered = [date(2026, 8, 22), date(2026, 8, 23), date(2026, 8, 24)]
    assert resolve_target_date(offered, monday_rule(), today=date(2026, 8, 22)) == date(2026, 8, 24)


def test_late_run_can_still_resolve_current_occurrence() -> None:
    offered = [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 31)]
    assert resolve_target_date(offered, monday_rule(), today=date(2026, 8, 24)) == date(2026, 8, 24)


def test_due_run_prefers_newly_released_occurrence() -> None:
    offered = [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 31)]
    assert resolve_target_date(
        offered,
        monday_rule(),
        today=date(2026, 8, 24),
        prefer_newly_released=True,
    ) == date(2026, 8, 31)


def test_due_run_waits_when_horizon_date_is_not_released() -> None:
    offered = [date(2026, 8, 24), date(2026, 8, 25)]
    assert (
        resolve_target_date(
            offered,
            monday_rule(),
            today=date(2026, 8, 24),
            prefer_newly_released=True,
        )
        is None
    )


def test_processed_and_out_of_horizon_dates_are_skipped() -> None:
    offered = [date(2026, 8, 24), date(2026, 8, 31)]
    result = resolve_target_date(
        offered,
        monday_rule(),
        today=date(2026, 8, 24),
        processed_dates={date(2026, 8, 24)},
    )
    assert result == date(2026, 8, 31)
