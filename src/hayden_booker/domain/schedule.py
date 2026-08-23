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
    prefer_newly_released: bool = False,
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
    if not candidates:
        return None
    return candidates[-1] if prefer_newly_released else candidates[0]


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
