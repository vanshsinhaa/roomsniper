from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from hayden_booker.config import AppConfig
from hayden_booker.constants import AUTH_HOST_SUFFIXES, AUTH_HOSTS, AuthState


async def classify_auth_state(page: Page) -> AuthState:
    hostname = (urlsplit(page.url).hostname or "").lower()
    if _is_auth_host(hostname):
        return AuthState.AUTH_REQUIRED
    user_id = page.get_by_label("ASURITE User ID", exact=False)
    password = page.get_by_label("Password", exact=False)
    try:
        if (
            await user_id.count()
            and await password.count()
            and await user_id.first.is_visible()
            and await password.first.is_visible()
        ):
            return AuthState.AUTH_REQUIRED
    except Exception:
        pass
    if hostname == "asu.libcal.com" or hostname.endswith(".libcal.com"):
        date_control = page.get_by_role("combobox", name="Date", exact=False)
        booking_text = page.get_by_text("Hayden", exact=False)
        details_heading = page.get_by_role(
            "heading", name=re.compile(r"booking details|reservation details", re.IGNORECASE)
        )
        try:
            if (
                (await date_control.count() and await date_control.first.is_visible())
                or (await booking_text.count() and await booking_text.first.is_visible())
                or (await details_heading.count() and await details_heading.first.is_visible())
            ):
                return AuthState.AUTHENTICATED
        except Exception:
            pass
    return AuthState.AUTH_INDETERMINATE


async def navigate_and_check_auth(page: Page, config: AppConfig) -> AuthState:
    try:
        await page.goto(config.libcal.accessible_url, wait_until="domcontentloaded")
    except PlaywrightTimeoutError:
        return await classify_auth_state(page)
    return await classify_auth_state(page)


async def wait_for_manual_auth(
    page: Page, config: AppConfig, *, timeout_seconds: int = 600
) -> bool:
    await page.goto(config.libcal.accessible_url, wait_until="domcontentloaded")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        if page.is_closed():
            return False
        if await classify_auth_state(page) is AuthState.AUTHENTICATED:
            return True
        await asyncio.sleep(1)
    return False


def _is_auth_host(hostname: str) -> bool:
    return hostname in AUTH_HOSTS or any(hostname.endswith(suffix) for suffix in AUTH_HOST_SUFFIXES)
