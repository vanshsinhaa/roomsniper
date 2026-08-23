from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hayden_booker.browser.availability import read_date_options
from hayden_booker.errors import SiteChangedError


@pytest.mark.browser
async def test_missing_date_selector_is_site_changed() -> None:
    playwright = await async_playwright().start()
    try:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium is not installed")
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content("<html><body><h1>Changed page</h1></body></html>")
        with pytest.raises(SiteChangedError, match="Date control"):
            await read_date_options(page)
        await browser.close()
    finally:
        await playwright.stop()
