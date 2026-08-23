from __future__ import annotations

from getpass import getpass

from hayden_booker.constants import KEYRING_SCHOOL_ID_KEY, KEYRING_SERVICE


class SecretStoreError(RuntimeError):
    pass


def _keyring_module() -> object:
    try:
        import keyring
    except ImportError as exc:
        raise SecretStoreError(
            "keyring is not installed; install the project dependencies first"
        ) from exc
    return keyring


def set_school_id_interactive() -> None:
    value = getpass("School ID (stored only in the OS credential store): ").strip()
    if not value:
        raise SecretStoreError("school ID cannot be empty")
    if len(value) > 64:
        raise SecretStoreError("school ID is unexpectedly long")
    keyring = _keyring_module()
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_SCHOOL_ID_KEY, value)  # type: ignore[attr-defined]
    except Exception as exc:
        raise SecretStoreError(
            f"credential store rejected the school ID: {type(exc).__name__}"
        ) from exc


def get_school_id() -> str | None:
    keyring = _keyring_module()
    try:
        value = keyring.get_password(KEYRING_SERVICE, KEYRING_SCHOOL_ID_KEY)  # type: ignore[attr-defined]
    except Exception as exc:
        raise SecretStoreError(f"credential store is unavailable: {type(exc).__name__}") from exc
    return str(value) if value else None


def school_id_exists() -> bool:
    return get_school_id() is not None
