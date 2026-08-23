from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hayden_booker.security.browser_state import (
    browser_auth_state_exists,
    browser_auth_state_path,
    restore_browser_auth_state,
    save_browser_auth_state,
)


class FakeContext:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.restored: dict[str, Any] | None = None

    async def storage_state(self) -> dict[str, Any]:
        return self.state

    async def set_storage_state(self, state: dict[str, Any]) -> None:
        self.restored = state


async def test_browser_auth_state_round_trip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAYDEN_BOOKER_DATA_DIR", str(tmp_path))
    state = {
        "cookies": [
            {
                "name": "session",
                "value": "sensitive-test-value",
                "domain": ".example.test",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    source = FakeContext(state)

    saved_path = await save_browser_auth_state(source)  # type: ignore[arg-type]

    assert saved_path == tmp_path / "auth" / "storage-state.json"
    assert browser_auth_state_exists()
    assert json.loads(saved_path.read_text(encoding="utf-8")) == state

    destination = FakeContext({"cookies": [], "origins": []})
    assert await restore_browser_auth_state(destination)  # type: ignore[arg-type]
    assert destination.restored == state


async def test_invalid_browser_auth_state_is_ignored(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAYDEN_BOOKER_DATA_DIR", str(tmp_path))
    path = browser_auth_state_path()
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")

    context = FakeContext({"cookies": [], "origins": []})
    assert not await restore_browser_auth_state(context)  # type: ignore[arg-type]
    assert context.restored is None
