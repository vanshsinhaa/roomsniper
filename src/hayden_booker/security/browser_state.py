from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext

from hayden_booker.config import app_data_dir


def browser_auth_state_path() -> Path:
    """Return the local, git-ignored path used for authenticated browser state."""
    return app_data_dir() / "auth" / "storage-state.json"


def browser_auth_state_exists() -> bool:
    return browser_auth_state_path().is_file()


async def restore_browser_auth_state(context: BrowserContext) -> bool:
    """Restore saved cookies into a persistent context without exposing their values."""
    path = browser_auth_state_path()
    if not path.is_file():
        return False
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        cookies = raw.get("cookies") if isinstance(raw, dict) else None
        if not isinstance(cookies, list):
            return False
    except (OSError, ValueError, TypeError):
        return False
    try:
        set_storage_state = getattr(context, "set_storage_state", None)
        if set_storage_state is not None:
            await set_storage_state(raw)
        else:
            # Playwright before set_storage_state support can still restore session cookies.
            await context.add_cookies(cookies)
    except Exception:
        # A stale or browser-version-incompatible state must not prevent auth setup.
        return False
    return True


async def save_browser_auth_state(context: BrowserContext) -> Path:
    """Atomically save browser auth state with restrictive permissions where supported."""
    state = await context.storage_state()
    path = browser_auth_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, separators=(",", ":"))
        os.replace(temporary, path)
        with suppress(OSError):
            path.chmod(0o600)
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        with suppress(OSError):
            temporary.unlink()
        raise
    return path
