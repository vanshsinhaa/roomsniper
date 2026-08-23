from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from hayden_booker.constants import DEFAULT_DATA_DIR_NAME, KNOWN_ROOMS

WEEKDAYS = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
}


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class LibCalConfig(ConfigModel):
    base_url: str = "https://asu.libcal.com"
    accessible_booking_path: str = "/r/accessible?lid=13858&gid=28619"
    location_id: int = 13858
    group_id: int = 28619
    school_id_label_pattern: str = r"(?:school|asu).*id|id number"
    confirmation_text_pattern: str = r"booking confirmed|reservation confirmed|confirmation"

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("libcal.base_url must use HTTPS")
        return value.rstrip("/")

    @field_validator("accessible_booking_path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("accessible_booking_path must start with '/'")
        return value

    @property
    def accessible_url(self) -> str:
        return f"{self.base_url}{self.accessible_booking_path}"


class BrowserConfig(ConfigModel):
    profile_directory: str = "~/.hayden-booker/browser-profile"
    scheduled_headless: bool = True
    bootstrap_headless: bool = False
    navigation_timeout_seconds: int = Field(default=30, ge=5, le=120)
    overall_run_timeout_seconds: int = Field(default=180, ge=30, le=900)
    lock_timeout_seconds: int = Field(default=5, ge=0, le=60)

    @property
    def profile_path(self) -> Path:
        return Path(self.profile_directory).expanduser().resolve()


class SchedulerConfig(ConfigModel):
    assumed_release_time: str = "00:00"
    release_observation_interval_seconds: int = Field(default=30, ge=30)
    release_grace_minutes: int = Field(default=15, ge=0, le=180)
    max_booking_attempts: int = Field(default=3, ge=1, le=3)
    retry_delay_seconds: int = Field(default=20, ge=10)
    submitting_safety_timeout_minutes: int = Field(default=15, ge=5, le=120)

    @field_validator("assumed_release_time")
    @classmethod
    def valid_release_time(cls, value: str) -> str:
        _parse_hhmm(value, "scheduler.assumed_release_time", half_hour=False)
        return value


class NotificationsConfig(ConfigModel):
    desktop_enabled: bool = True
    notify_on_success: bool = True
    notify_on_auth_required: bool = True
    notify_on_failure: bool = True


class DiagnosticsConfig(ConfigModel):
    capture_screenshots: bool = False
    screenshot_retention_days: int = Field(default=7, ge=1, le=30)


class ScheduleRule(ConfigModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    enabled: bool = True
    weekday: str
    start_time: str
    end_time: str
    room_preferences: list[str] = Field(min_length=1)
    exact_time_required: bool = True

    @field_validator("weekday")
    @classmethod
    def valid_weekday(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in WEEKDAYS:
            raise ValueError(f"weekday must be one of {', '.join(sorted(WEEKDAYS))}")
        return normalized

    @field_validator("start_time", "end_time")
    @classmethod
    def half_hour_time(cls, value: str, info: Any) -> str:
        _parse_hhmm(value, str(info.field_name), half_hour=True)
        return value

    @field_validator("room_preferences")
    @classmethod
    def known_unique_rooms(cls, rooms: list[str]) -> list[str]:
        unknown = sorted(set(rooms) - set(KNOWN_ROOMS))
        if unknown:
            raise ValueError(f"unknown Hayden room(s): {', '.join(unknown)}")
        if len(rooms) != len(set(rooms)):
            raise ValueError("room_preferences contains duplicates")
        return rooms

    @model_validator(mode="after")
    def valid_interval(self) -> Self:
        start = _minutes(self.start_time)
        end = _minutes(self.end_time)
        if end <= start:
            raise ValueError("end_time must be later than start_time")
        if end - start > 240:
            raise ValueError("requested duration cannot exceed four hours")
        if not self.exact_time_required:
            raise ValueError("MVP supports only exact_time_required: true")
        return self

    @property
    def duration_minutes(self) -> int:
        return _minutes(self.end_time) - _minutes(self.start_time)


class AppConfig(ConfigModel):
    version: int = 1
    timezone: str = "America/Phoenix"
    live_booking_enabled: bool = False
    libcal: LibCalConfig = Field(default_factory=LibCalConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
    schedules: list[ScheduleRule] = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def supported_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only configuration version 1 is supported")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def unique_schedule_ids_and_daily_limit(self) -> Self:
        ids = [rule.id for rule in self.schedules]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"duplicate schedule ID(s): {', '.join(duplicates)}")
        totals: dict[str, int] = {}
        for rule in self.schedules:
            if rule.enabled:
                totals[rule.weekday] = totals.get(rule.weekday, 0) + rule.duration_minutes
        too_long = [day for day, total in totals.items() if total > 240]
        if too_long:
            raise ValueError(
                "configured daily total exceeds four hours for: " + ", ".join(too_long)
            )
        return self

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _parse_hhmm(value: str, field: str, *, half_hour: bool) -> datetime:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"{field} must use 24-hour HH:MM format") from exc
    if half_hour and parsed.minute not in (0, 30):
        raise ValueError(f"{field} must align to a half hour")
    return parsed


def _minutes(value: str) -> int:
    parsed = datetime.strptime(value, "%H:%M")
    return parsed.hour * 60 + parsed.minute


def app_data_dir() -> Path:
    override = os.environ.get("HAYDEN_BOOKER_DATA_DIR")
    return (
        Path(override).expanduser().resolve() if override else Path.home() / DEFAULT_DATA_DIR_NAME
    )


def database_path() -> Path:
    return app_data_dir() / "hayden-booker.sqlite3"


def load_config(path: Path) -> tuple[AppConfig, list[str]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"configuration file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    return config, collect_unknown_keys(config)


def collect_unknown_keys(model: BaseModel, prefix: str = "") -> list[str]:
    warnings: list[str] = []
    if model.model_extra:
        warnings.extend(f"unknown configuration key: {prefix}{key}" for key in model.model_extra)
    for name in type(model).model_fields:
        value = getattr(model, name)
        child_prefix = f"{prefix}{name}."
        if isinstance(value, BaseModel):
            warnings.extend(collect_unknown_keys(value, child_prefix))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, BaseModel):
                    warnings.extend(collect_unknown_keys(item, f"{prefix}{name}[{index}]."))
    return warnings


def find_schedule(config: AppConfig, schedule_id: str) -> ScheduleRule:
    for rule in config.schedules:
        if rule.id == schedule_id:
            return rule
    raise ValueError(f"unknown schedule ID: {schedule_id}")
