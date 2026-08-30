from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from hayden_booker.cli import app

runner = CliRunner()


def test_cli_help_builds_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "observe-release" in result.stdout
    assert "doctor" in result.stdout
    assert "calendar" in result.stdout


def test_config_validate_and_schedule_show() -> None:
    config_path = Path(__file__).parents[2] / "config.example.yaml"
    validation = runner.invoke(app, ["--config", str(config_path), "config", "validate"])
    assert validation.exit_code == 0
    assert "Configuration valid" in validation.stdout
    schedule = runner.invoke(app, ["--config", str(config_path), "schedule", "show"])
    assert schedule.exit_code == 0
    assert "monday-afternoon" in schedule.stdout


def test_target_date_rejects_non_iso_value() -> None:
    config_path = Path(__file__).parents[2] / "config.example.yaml"
    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "run",
            "--schedule-id",
            "monday-afternoon",
            "--target-date",
            "08/24/2026",
        ],
    )
    assert result.exit_code == 30
    assert "YYYY-MM-DD" in result.output
