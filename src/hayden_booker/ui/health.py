from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from hayden_booker.browser.context import profile_lock_path
from hayden_booker.config import AppConfig, app_data_dir, database_path, load_config
from hayden_booker.constants import AttemptStatus
from hayden_booker.security.browser_state import (
    browser_auth_state_exists,
    browser_auth_state_path,
)
from hayden_booker.security.secrets import (
    SecretStoreError,
    google_calendar_credentials_exist,
    school_id_exists,
)
from hayden_booker.ui.scheduler_task import read_scheduler_task

OK = "ok"
ATTENTION = "attention"
ERROR = "error"
UNKNOWN = "unknown"

_SEVERITY = {OK: 0, UNKNOWN: 1, ATTENTION: 2, ERROR: 3}
_AUTH_STALE_DAYS = 14
_LOG_TAIL_BYTES = 256_000

WEEKDAY_NUMBERS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True, slots=True)
class Check:
    """One dashboard health row; `detail` never contains a secret value."""

    key: str
    label: str
    state: str
    detail: str
    remedy: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def build_status(config_path: Path) -> dict[str, Any]:
    config, config_check = _config_check(config_path)
    database_check, occurrence_summary = _database_check()
    events = read_log_events(limit=400)
    checks: list[Check] = [
        config_check,
        _secret_check(config),
        _calendar_check(config),
        _auth_check(events),
        _scheduler_check(config),
        database_check,
        _activity_check(events),
        _lock_check(),
    ]
    overall = _worst(check.state for check in checks)
    return {
        "generated_at_utc": _now_iso(),
        "overall": overall,
        "overall_label": _overall_label(overall),
        "checks": [asdict(check) for check in checks],
        "occurrences": occurrence_summary,
        "config": _config_summary(config, config_path),
        "upcoming": _upcoming(config),
        "paths": {
            "config": str(config_path),
            "data_dir": str(app_data_dir()),
            "database": str(database_path()),
            "logs": str(app_data_dir() / "logs"),
        },
    }


def _overall_label(overall: str) -> str:
    return {
        OK: "System active",
        ATTENTION: "Needs attention",
        ERROR: "Not running",
        UNKNOWN: "Unknown",
    }[overall]


def _worst(states: Any) -> str:
    return max(states, key=lambda state: _SEVERITY.get(state, 1), default=UNKNOWN)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _config_check(config_path: Path) -> tuple[AppConfig | None, Check]:
    try:
        config, warnings = load_config(config_path)
    except ValueError as exc:
        first_line = str(exc).strip().splitlines()[0]
        return None, Check(
            "config",
            "Configuration",
            ERROR,
            f"config.yaml is invalid: {first_line}",
            "Run `hayden-booker config validate` and correct config.yaml.",
        )
    enabled = [rule for rule in config.schedules if rule.enabled]
    mode = "live booking allowed" if config.live_booking_enabled else "dry run only"
    detail = f"{len(enabled)} of {len(config.schedules)} schedule(s) enabled; {mode}"
    state = OK if enabled else ATTENTION
    remedy = None if enabled else "Enable at least one schedule in config.yaml."
    if warnings:
        state = ATTENTION
        detail = f"{detail}; {len(warnings)} unknown configuration key(s)"
    return config, Check("config", "Configuration", state, detail, remedy, {"warnings": warnings})


def _secret_check(config: AppConfig | None) -> Check:
    try:
        present = school_id_exists()
    except SecretStoreError as exc:
        return Check(
            "secret",
            "School ID",
            ERROR,
            f"Credential store unavailable: {exc}",
            "Install a supported keyring backend, then run `hayden-booker secret set-school-id`.",
        )
    if present:
        return Check("secret", "School ID", OK, "Stored in the OS credential store.")
    live = bool(config and config.live_booking_enabled)
    return Check(
        "secret",
        "School ID",
        ERROR if live else ATTENTION,
        "No school ID is stored; live booking cannot submit.",
        "Run `hayden-booker secret set-school-id`.",
    )


def _calendar_check(config: AppConfig | None) -> Check:
    if config is None or not config.calendar.enabled:
        return Check(
            "calendar",
            "Google Calendar",
            OK,
            "Automatic adds are off.",
            "Connect Google Calendar, then set `calendar.enabled: true` to enable them.",
        )
    try:
        connected = google_calendar_credentials_exist()
    except SecretStoreError as exc:
        return Check(
            "calendar",
            "Google Calendar",
            ERROR,
            f"Credential store unavailable: {exc}",
            "Install a supported keyring backend, then reconnect Google Calendar.",
        )
    if connected:
        return Check(
            "calendar",
            "Google Calendar",
            OK,
            "Connected; confirmed bookings are added automatically.",
        )
    return Check(
        "calendar",
        "Google Calendar",
        ERROR,
        "Automatic adds are enabled, but Google Calendar is not connected.",
        "Run `hayden-booker calendar connect --credentials CLIENT_SECRET.json`.",
    )


def _auth_check(events: list[dict[str, Any]]) -> Check:
    """Judge sign-in by what the last real run observed.

    Cookie expiry cannot answer this: the ASU/Duo session rides mostly on session
    cookies with no expiry, so only a run that reached LibCal proves the state.
    """
    path = browser_auth_state_path()
    remedy = "Run `hayden-booker auth setup` to sign in again."
    if not browser_auth_state_exists():
        return Check(
            "auth",
            "ASU sign-in",
            ERROR,
            "No saved browser session exists.",
            "Run `hayden-booker auth setup` and finish ASU/Duo sign-in.",
        )
    saved_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    age_days = (datetime.now(UTC) - saved_at).days
    extra: dict[str, Any] = {
        "saved_at_utc": saved_at.isoformat().replace("+00:00", "Z"),
        "age_days": age_days,
    }
    observed_at, authenticated = _last_auth_observation(events, not_before=saved_at)
    if observed_at is not None:
        extra["observed_at_utc"] = observed_at.isoformat().replace("+00:00", "Z")
        if not authenticated:
            return Check(
                "auth",
                "ASU sign-in",
                ERROR,
                "The last run was sent back to ASU sign-in; the saved session is no longer valid.",
                remedy,
                extra,
            )
        return Check(
            "auth",
            "ASU sign-in",
            OK,
            f"Verified during the run on {observed_at.date().isoformat()}.",
            None,
            extra,
        )
    if age_days >= _AUTH_STALE_DAYS:
        return Check(
            "auth",
            "ASU sign-in",
            ATTENTION,
            f"Session saved {age_days} day(s) ago and not verified since; ASU may have expired it.",
            remedy,
            extra,
        )
    return Check(
        "auth",
        "ASU sign-in",
        OK,
        f"Session saved {age_days} day(s) ago; no run has contradicted it yet.",
        None,
        extra,
    )


def _last_auth_observation(
    events: list[dict[str, Any]], *, not_before: datetime
) -> tuple[datetime | None, bool]:
    """Return the newest authentication signal logged after the session was saved."""
    for event in reversed(events):
        name = event.get("event")
        if name not in {"authentication_checked", "authentication_required"}:
            continue
        moment = _parse_utc(str(event.get("timestamp_utc") or ""))
        if moment is None or moment < not_before:
            continue
        if name == "authentication_required":
            return moment, False
        return moment, str(event.get("status")) == "AUTHENTICATED"
    return None, False


def _parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _scheduler_check(config: AppConfig | None) -> Check:
    task = read_scheduler_task()
    extra: dict[str, Any] = {
        "name": task.name,
        "next_run": task.next_run,
        "last_run": task.last_run,
        "last_result": task.last_result,
        "release_time": config.scheduler.assumed_release_time if config else None,
    }
    if not task.supported:
        return Check(
            "scheduler",
            "Automatic runs",
            UNKNOWN,
            task.detail or "Scheduled-task state cannot be read on this platform.",
            None,
            extra,
        )
    if not task.installed:
        return Check(
            "scheduler",
            "Automatic runs",
            ERROR,
            task.detail or "No scheduled task is installed; nothing runs automatically.",
            "Install the scheduled task from `scripts/` after reviewing the command.",
            extra,
        )
    if task.enabled is False:
        return Check(
            "scheduler",
            "Automatic runs",
            ERROR,
            f"Scheduled task '{task.name}' is disabled.",
            "Re-enable the task in the operating-system scheduler.",
            extra,
        )
    detail = f"Scheduled task '{task.name}' is active."
    if task.next_run:
        detail = f"{detail} Next run: {task.next_run}."
    return Check("scheduler", "Automatic runs", OK, detail, None, extra)


def _database_check() -> tuple[Check, dict[str, Any]]:
    path = database_path()
    summary: dict[str, Any] = {
        "total": 0,
        "by_status": {},
        "needs_review": 0,
        "last_updated_utc": None,
    }
    if not path.exists():
        return (
            Check(
                "database",
                "Local history",
                ATTENTION,
                "No local database exists yet.",
                "Run `hayden-booker init`.",
            ),
            summary,
        )
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        rows = connection.execute(
            "SELECT status, COUNT(*) FROM reservation_occurrences GROUP BY status"
        ).fetchall()
        unreviewed = connection.execute(
            """
            SELECT COUNT(*) FROM reservation_occurrences
            WHERE status IN (?, ?) AND acknowledged_at_utc IS NULL
            """,
            (
                AttemptStatus.MANUAL_REVIEW_REQUIRED.value,
                AttemptStatus.UNKNOWN_RESULT.value,
            ),
        ).fetchone()
        latest = connection.execute(
            "SELECT MAX(updated_at_utc) FROM reservation_occurrences"
        ).fetchone()
        connection.close()
    except sqlite3.Error as exc:
        return (
            Check("database", "Local history", ERROR, f"Database unreadable: {exc}", None),
            summary,
        )
    by_status = {str(status): int(count) for status, count in rows}
    summary["by_status"] = by_status
    summary["total"] = sum(by_status.values())
    summary["last_updated_utc"] = latest[0] if latest else None
    needs_review = int(unreviewed[0]) if unreviewed else 0
    summary["needs_review"] = needs_review
    if needs_review:
        return (
            Check(
                "database",
                "Local history",
                ATTENTION,
                f"{needs_review} occurrence(s) need manual review in LibCal.",
                "Check LibCal manually; do not resubmit automatically.",
                {"needs_review": needs_review},
            ),
            summary,
        )
    confirmed = by_status.get(AttemptStatus.CONFIRMED.value, 0)
    return (
        Check(
            "database",
            "Local history",
            OK,
            f"{summary['total']} occurrence(s) recorded; {confirmed} confirmed.",
        ),
        summary,
    )


def _activity_check(events: list[dict[str, Any]]) -> Check:
    if not events:
        return Check(
            "activity",
            "Recent activity",
            ATTENTION,
            "No run has been logged yet.",
            "Run `hayden-booker run --dry-run` to verify the pipeline.",
        )
    last_run = next(
        (event for event in reversed(events) if event.get("event") == "run_completed"), None
    )
    last_error = next(
        (event for event in reversed(events) if event.get("level") in {"ERROR", "CRITICAL"}), None
    )
    latest = events[-1]
    extra: dict[str, Any] = {
        "last_event": latest.get("event"),
        "last_event_at_utc": latest.get("timestamp_utc"),
        "last_run_at_utc": last_run.get("timestamp_utc") if last_run else None,
        "last_error_code": last_error.get("error_code") if last_error else None,
        "last_error_at_utc": last_error.get("timestamp_utc") if last_error else None,
    }
    if last_run is None:
        return Check(
            "activity",
            "Recent activity",
            ATTENTION,
            "Logs exist but no completed run was recorded.",
            "Run `hayden-booker doctor`.",
            extra,
        )
    stamp = str(last_run.get("timestamp_utc") or "")
    if last_error is not None and str(last_error.get("timestamp_utc") or "") >= stamp:
        return Check(
            "activity",
            "Recent activity",
            ATTENTION,
            f"The last run reported {last_error.get('error_code') or 'an error'}.",
            "Open the failing occurrence below and check LibCal.",
            extra,
        )
    age = _age_days(stamp)
    if age is not None and age > 8:
        return Check(
            "activity",
            "Recent activity",
            ATTENTION,
            f"No run in {age} day(s); the scheduler may not be firing.",
            "Verify the scheduled task and run `hayden-booker doctor`.",
            extra,
        )
    return Check("activity", "Recent activity", OK, f"Last completed run: {stamp}.", None, extra)


def _age_days(timestamp: str) -> int | None:
    parsed = _parse_utc(timestamp)
    return None if parsed is None else (datetime.now(UTC) - parsed).days


def _lock_check() -> Check:
    lock = FileLock(str(profile_lock_path()))
    try:
        lock.acquire(timeout=0)
    except FileLockTimeout:
        return Check(
            "lock",
            "Browser profile",
            OK,
            "A Hayden Booker run is using the browser profile right now.",
            None,
            {"busy": True},
        )
    lock.release()
    return Check("lock", "Browser profile", OK, "Idle and available.", None, {"busy": False})


def read_log_events(*, limit: int = 200) -> list[dict[str, Any]]:
    """Read the tail of the rotating JSONL log; malformed lines are skipped."""
    path = app_data_dir() / "logs" / "hayden-booker.jsonl"
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _LOG_TAIL_BYTES:
                handle.seek(size - _LOG_TAIL_BYTES)
                handle.readline()
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events[-limit:]


def _config_summary(config: AppConfig | None, config_path: Path) -> dict[str, Any]:
    if config is None:
        return {"valid": False, "path": str(config_path)}
    return {
        "valid": True,
        "path": str(config_path),
        "timezone": config.timezone,
        "live_booking_enabled": config.live_booking_enabled,
        "calendar_enabled": config.calendar.enabled,
        "release_time": config.scheduler.assumed_release_time,
        "schedules": [
            {
                "id": rule.id,
                "enabled": rule.enabled,
                "weekday": rule.weekday,
                "start_time": rule.start_time,
                "end_time": rule.end_time,
                "room_preferences": list(rule.room_preferences),
            }
            for rule in config.schedules
        ],
    }


def _upcoming(config: AppConfig | None, *, horizon_days: int = 7) -> list[dict[str, Any]]:
    if config is None:
        return []
    today = datetime.now(config.zone).date()
    upcoming: list[dict[str, Any]] = []
    for rule in config.schedules:
        if not rule.enabled:
            continue
        target = _next_weekday(today, rule.weekday)
        upcoming.append(
            {
                "schedule_id": rule.id,
                "weekday": rule.weekday,
                "target_date": target.isoformat(),
                "start_time": rule.start_time,
                "end_time": rule.end_time,
                "bookable_now": (target - today).days <= horizon_days,
            }
        )
    return sorted(upcoming, key=lambda item: (item["target_date"], item["start_time"]))


def _next_weekday(today: date, weekday: str) -> date:
    delta = (WEEKDAY_NUMBERS[weekday] - today.weekday()) % 7
    return today + timedelta(days=delta)
