from __future__ import annotations

from enum import IntEnum, StrEnum

APP_NAME = "hayden-booker"
KEYRING_SERVICE = "hayden-room-booker"
KEYRING_SCHOOL_ID_KEY = "school-id"
DEFAULT_DATA_DIR_NAME = ".hayden-booker"

KNOWN_ROOMS: tuple[str, ...] = (
    "Study Room 311A",
    "Study Room 311B",
    "Study Room 311C",
    "Study Room 336",
    "Study Room 342",
    "Study Room 351",
    "Study Room 353",
    "Study Room 355",
    "Study Room 357",
    "Study Room C13",
    "Study Room C15",
    "Study Room C17",
    "Study Room C19",
    "Study Room C38",
    "Study Room C40",
    "Study Room L52",
    "Study Room L54",
    "Study Room L56",
)

AUTH_HOSTS: frozenset[str] = frozenset(
    {
        "weblogin.asu.edu",
        "shibboleth.asu.edu",
        "webauth.asu.edu",
    }
)
AUTH_HOST_SUFFIXES: tuple[str, ...] = ("duosecurity.com", "libauth.com")


class AttemptStatus(StrEnum):
    PLANNED = "PLANNED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CHECKING_AVAILABILITY = "CHECKING_AVAILABILITY"
    NO_AVAILABILITY = "NO_AVAILABILITY"
    DRY_RUN_COMPLETE = "DRY_RUN_COMPLETE"
    SUBMITTING = "SUBMITTING"
    CONFLICT = "CONFLICT"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CONFIRMED = "CONFIRMED"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    SITE_CHANGED = "SITE_CHANGED"
    NETWORK_ERROR = "NETWORK_ERROR"
    RATE_LIMITED = "RATE_LIMITED"


class AuthState(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_INDETERMINATE = "AUTH_INDETERMINATE"


class ExitCode(IntEnum):
    OK = 0
    NO_AVAILABILITY = 10
    AUTH_REQUIRED = 20
    SECRET_MISSING = 21
    CONFIG_INVALID = 30
    SITE_CHANGED = 40
    NETWORK_ERROR = 41
    RATE_LIMITED = 42
    VALIDATION_FAILED = 50
    CONFLICT = 51
    UNKNOWN_RESULT = 60
    LOCKED = 70
