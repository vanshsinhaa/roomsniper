from __future__ import annotations

from copy import deepcopy

import pytest
import yaml
from pydantic import ValidationError

from hayden_booker.config import AppConfig, collect_unknown_keys
from hayden_booker.sample_config import SAMPLE_CONFIG


def valid_data() -> dict[str, object]:
    value = yaml.safe_load(SAMPLE_CONFIG)
    assert isinstance(value, dict)
    return value


def test_sample_configuration_is_valid() -> None:
    config = AppConfig.model_validate(valid_data())
    assert config.schedules[0].duration_minutes == 120
    assert config.calendar.enabled is False
    assert config.calendar.calendar_id == "primary"
    assert collect_unknown_keys(config) == []


def test_calendar_id_cannot_be_blank_or_contain_whitespace() -> None:
    data = valid_data()
    data["calendar"]["calendar_id"] = "bad calendar"  # type: ignore[index]
    with pytest.raises(ValidationError, match="cannot contain whitespace"):
        AppConfig.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("weekday", "funday", "weekday"),
        ("start_time", "13:15", "half hour"),
        ("end_time", "13:00", "later"),
        ("room_preferences", ["Study Room 999"], "unknown Hayden room"),
    ],
)
def test_invalid_schedule_values_are_rejected(field: str, value: object, message: str) -> None:
    data = valid_data()
    schedules = data["schedules"]
    assert isinstance(schedules, list)
    schedules[0][field] = value
    with pytest.raises(ValidationError, match=message):
        AppConfig.model_validate(data)


def test_duration_over_four_hours_is_rejected() -> None:
    data = valid_data()
    data["schedules"][0]["start_time"] = "08:00"  # type: ignore[index]
    data["schedules"][0]["end_time"] = "12:30"  # type: ignore[index]
    with pytest.raises(ValidationError, match="four hours"):
        AppConfig.model_validate(data)


def test_duplicate_schedule_ids_are_rejected() -> None:
    data = valid_data()
    schedule = data["schedules"][0]  # type: ignore[index]
    data["schedules"].append(deepcopy(schedule))  # type: ignore[union-attr]
    with pytest.raises(ValidationError, match="duplicate schedule ID"):
        AppConfig.model_validate(data)


def test_polling_faster_than_thirty_seconds_is_rejected() -> None:
    data = valid_data()
    data["scheduler"]["release_observation_interval_seconds"] = 29  # type: ignore[index]
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_unknown_keys_become_warnings() -> None:
    data = valid_data()
    data["scheduler"]["retry_delai_seconds"] = 20  # type: ignore[index]
    config = AppConfig.model_validate(data)
    assert collect_unknown_keys(config) == [
        "unknown configuration key: scheduler.retry_delai_seconds"
    ]
