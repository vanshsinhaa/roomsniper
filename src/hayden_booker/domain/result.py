from __future__ import annotations

from dataclasses import dataclass

from hayden_booker.constants import AttemptStatus, ExitCode


@dataclass(frozen=True, slots=True)
class RunResult:
    status: AttemptStatus
    exit_code: ExitCode
    message: str
    occurrence_id: str | None = None
    room: str | None = None
    confirmation_reference: str | None = None
