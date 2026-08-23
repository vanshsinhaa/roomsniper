from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field

WINDOWS_TASK_NAME = "Hayden Room Booker"
LAUNCHD_LABEL = "com.local.hayden-room-booker"
SYSTEMD_UNIT = "hayden-room-booker.timer"

_QUERY_TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class SchedulerTask:
    """Sanitized view of the operating-system scheduled task, if one is installed."""

    supported: bool
    installed: bool
    enabled: bool | None = None
    name: str | None = None
    next_run: str | None = None
    last_run: str | None = None
    last_result: str | None = None
    detail: str | None = None
    fields: dict[str, str] = field(default_factory=dict)


def read_scheduler_task() -> SchedulerTask:
    system = platform.system()
    if system == "Windows":
        return _windows_task()
    if system == "Darwin":
        return _launchd_task()
    if system == "Linux":
        return _systemd_task()
    return SchedulerTask(supported=False, installed=False, detail=f"unsupported platform {system}")


def _run(command: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_QUERY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return completed.returncode, completed.stdout


def _windows_task() -> SchedulerTask:
    executable = shutil.which("schtasks")
    if executable is None:
        return SchedulerTask(supported=False, installed=False, detail="schtasks is unavailable")
    code, output = _run([executable, "/query", "/tn", WINDOWS_TASK_NAME, "/fo", "LIST", "/v"])
    if code != 0 or not output.strip():
        return SchedulerTask(
            supported=True,
            installed=False,
            name=WINDOWS_TASK_NAME,
            detail="No scheduled task named 'Hayden Room Booker' is registered.",
        )
    values = _parse_list_output(output)
    status = values.get("Scheduled Task State") or values.get("Status") or ""
    enabled = status.strip().lower() in {"enabled", "ready", "running"}
    return SchedulerTask(
        supported=True,
        installed=True,
        enabled=enabled,
        name=WINDOWS_TASK_NAME,
        next_run=values.get("Next Run Time"),
        last_run=values.get("Last Run Time"),
        last_result=values.get("Last Result"),
        detail=status or None,
        fields={
            key: value
            for key, value in values.items()
            if key
            in {"Status", "Scheduled Task State", "Schedule Type", "Start Time", "Repeat: Every"}
        },
    )


def _parse_list_output(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key and value and key not in values:
            values[key] = value
    return values


def _launchd_task() -> SchedulerTask:
    executable = shutil.which("launchctl")
    if executable is None:
        return SchedulerTask(supported=False, installed=False, detail="launchctl is unavailable")
    code, output = _run([executable, "list", LAUNCHD_LABEL])
    if code != 0:
        return SchedulerTask(
            supported=True,
            installed=False,
            name=LAUNCHD_LABEL,
            detail="No launch agent is loaded.",
        )
    values = _parse_list_output(output)
    exit_status = values.get('"LastExitStatus"', "").rstrip(";")
    return SchedulerTask(
        supported=True,
        installed=True,
        enabled=True,
        name=LAUNCHD_LABEL,
        last_result=exit_status or None,
        detail="Loaded launch agent.",
    )


def _systemd_task() -> SchedulerTask:
    executable = shutil.which("systemctl")
    if executable is None:
        return SchedulerTask(supported=False, installed=False, detail="systemctl is unavailable")
    code, output = _run(
        [
            executable,
            "--user",
            "show",
            SYSTEMD_UNIT,
            "--property=ActiveState",
            "--property=UnitFileState",
            "--property=NextElapseUSecRealtime",
            "--property=LastTriggerUSec",
        ]
    )
    if code != 0 or not output.strip():
        return SchedulerTask(
            supported=True,
            installed=False,
            name=SYSTEMD_UNIT,
            detail="No systemd user timer is installed.",
        )
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, _, value = line.partition("=")
        if key:
            values[key.strip()] = value.strip()
    active = values.get("ActiveState", "")
    if not values.get("UnitFileState"):
        return SchedulerTask(
            supported=True, installed=False, name=SYSTEMD_UNIT, detail="Timer unit not found."
        )
    return SchedulerTask(
        supported=True,
        installed=True,
        enabled=active == "active",
        name=SYSTEMD_UNIT,
        next_run=values.get("NextElapseUSecRealtime") or None,
        last_run=values.get("LastTriggerUSec") or None,
        detail=active or None,
    )
