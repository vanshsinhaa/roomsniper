from __future__ import annotations

from dataclasses import dataclass

from hayden_booker.constants import ExitCode


@dataclass(slots=True)
class BookerError(Exception):
    code: str
    message: str
    exit_code: ExitCode
    next_action: str
    occurrence_id: str | None = None

    def __str__(self) -> str:
        return self.message


class SiteChangedError(BookerError):
    def __init__(self, message: str, *, occurrence_id: str | None = None) -> None:
        super().__init__(
            "SITE_CHANGED",
            message,
            ExitCode.SITE_CHANGED,
            "Run `hayden-booker doctor` and inspect local diagnostics.",
            occurrence_id,
        )


class NavigationError(BookerError):
    def __init__(self, message: str, *, occurrence_id: str | None = None) -> None:
        super().__init__(
            "NETWORK_ERROR",
            message,
            ExitCode.NETWORK_ERROR,
            "Check the network connection and retry once.",
            occurrence_id,
        )
