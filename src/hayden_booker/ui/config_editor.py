from __future__ import annotations

import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from hayden_booker.config import AppConfig, load_config
from hayden_booker.constants import KNOWN_ROOMS

_CONFIG_WRITE_LOCK = threading.Lock()
_EDITABLE_KEYS = frozenset({"live_booking_enabled", "schedules"})
_SCHEDULE_KEYS = frozenset(
    {
        "id",
        "enabled",
        "weekday",
        "start_time",
        "end_time",
        "room_preferences",
        "exact_time_required",
    }
)
_REQUIRED_SCHEDULE_KEYS = _SCHEDULE_KEYS - {"exact_time_required"}


class ConfigEditError(ValueError):
    """A safe, user-facing configuration edit error."""


def editable_config(path: Path) -> dict[str, Any]:
    config, _ = load_config(path)
    return _editable_payload(config)


def update_editable_config(path: Path, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ConfigEditError("Configuration changes must be a JSON object.")
    unknown = sorted(set(payload) - _EDITABLE_KEYS)
    missing = sorted(_EDITABLE_KEYS - set(payload))
    if unknown:
        raise ConfigEditError(f"Unsupported setting(s): {', '.join(unknown)}.")
    if missing:
        raise ConfigEditError(f"Missing setting(s): {', '.join(missing)}.")
    if not isinstance(payload["live_booking_enabled"], bool):
        raise ConfigEditError("Live booking must be turned on or off.")

    schedules = payload["schedules"]
    if not isinstance(schedules, list):
        raise ConfigEditError("Schedules must be a list.")
    for index, schedule in enumerate(schedules, start=1):
        if not isinstance(schedule, dict):
            raise ConfigEditError(f"Schedule {index} must be an object.")
        unknown_schedule = sorted(set(schedule) - _SCHEDULE_KEYS)
        missing_schedule = sorted(_REQUIRED_SCHEDULE_KEYS - set(schedule))
        if unknown_schedule:
            raise ConfigEditError(
                f"Schedule {index} has unsupported field(s): {', '.join(unknown_schedule)}."
            )
        if missing_schedule:
            raise ConfigEditError(
                f"Schedule {index} is missing field(s): {', '.join(missing_schedule)}."
            )

    with _CONFIG_WRITE_LOCK:
        raw = _read_mapping(path)
        candidate = dict(raw)
        candidate["live_booking_enabled"] = payload["live_booking_enabled"]
        candidate["schedules"] = [
            {**schedule, "exact_time_required": schedule.get("exact_time_required", True)}
            for schedule in schedules
        ]
        try:
            validated = AppConfig.model_validate(candidate)
        except ValidationError as exc:
            raise ConfigEditError(_validation_message(exc)) from exc

        # Persist normalized schedule values while leaving every non-editable section alone.
        candidate["live_booking_enabled"] = validated.live_booking_enabled
        candidate["schedules"] = [
            schedule.model_dump(mode="json") for schedule in validated.schedules
        ]
        _atomic_write(path, yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True))

    return _editable_payload(validated)


def _editable_payload(config: AppConfig) -> dict[str, Any]:
    return {
        "live_booking_enabled": config.live_booking_enabled,
        "schedules": [schedule.model_dump(mode="json") for schedule in config.schedules],
        "known_rooms": list(KNOWN_ROOMS),
        "timezone": config.timezone,
    }


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigEditError(f"Configuration file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigEditError(f"The configuration YAML is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigEditError("The configuration root must be a mapping.")
    return raw


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"])
    return f"{location}: {message}" if location else message


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, existing_mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
