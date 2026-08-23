from __future__ import annotations

from datetime import date, timedelta

from hayden_booker.config import ScheduleRule


def resolve_target_date(
    offered_dates: list[date],
    rule: ScheduleRule,
    *,
    today: date,
    processed_dates: set[date] | None = None,
    booking_horizon_days: int = 7,
) -> date | None:
    processed_dates = processed_dates or set()
    weekday = _weekday_number(rule.weekday)
    latest = today + timedelta(days=booking_horizon_days)
    candidates = sorted(
        offered
        for offered in set(offered_dates)
        if today <= offered <= latest
        and offered.weekday() == weekday
        and offered not in processed_dates
    )
    return candidates[0] if candidates else None


def _weekday_number(name: str) -> int:
    return (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ).index(name.lower())
