from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from types import TracebackType

from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from hayden_booker.config import AppConfig, app_data_dir
from hayden_booker.constants import ExitCode
from hayden_booker.errors import BookerError, NavigationError
from hayden_booker.security.browser_state import restore_browser_auth_state


class BrowserSession:
    def __init__(self, config: AppConfig, *, headless: bool) -> None:
        self.config = config
        self.headless = headless
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self._lock = FileLock(str(app_data_dir() / "browser-profile.lock"))

    async def __aenter__(self) -> BrowserSession:
        app_data_dir().mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire(timeout=self.config.browser.lock_timeout_seconds)
        except FileLockTimeout as exc:
            raise BookerError(
                "APPLICATION_LOCKED",
                "Another Hayden Booker process is using the browser profile.",
                ExitCode.LOCKED,
                "Wait for the other process to finish; do not delete browser lock files.",
            ) from exc
        profile_path = self.config.browser.profile_path
        profile_path.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = await async_playwright().start()
            self.context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
            )
            self.context.set_default_timeout(self.config.browser.navigation_timeout_seconds * 1000)
            self.context.set_default_navigation_timeout(
                self.config.browser.navigation_timeout_seconds * 1000
            )
            await restore_browser_auth_state(self.context)
            return self
        except Exception as exc:
            await self._cleanup()
            raise NavigationError(
                "Chromium could not start. Run `playwright install chromium` and try again."
            ) from exc

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._cleanup()

    @property
    def page(self) -> Page:
        if self.context is None:
            raise RuntimeError("browser session has not been started")
        return self.context.pages[0] if self.context.pages else self._new_page_error()

    def _new_page_error(self) -> Page:
        raise RuntimeError("persistent browser context has no open page")

    async def get_page(self) -> Page:
        if self.context is None:
            raise RuntimeError("browser session has not been started")
        return self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def _cleanup(self) -> None:
        if self.context is not None:
            with suppress(Exception):
                await self.context.close()
            self.context = None
        if self._playwright is not None:
            with suppress(Exception):
                await self._playwright.stop()
            self._playwright = None
        if self._lock.is_locked:
            self._lock.release()


def profile_lock_path() -> Path:
    return app_data_dir() / "browser-profile.lock"
