from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from hayden_booker.browser.auth import classify_auth_state
from hayden_booker.browser.availability import (
    ensure_availability_step,
    load_snapshot,
    read_date_options,
    select_exact_slots,
)
from hayden_booker.browser.booking import (
    classify_submission_result,
    fill_school_id,
    verify_booking_details,
)
from hayden_booker.config import AppConfig
from hayden_booker.constants import AttemptStatus, AuthState
from hayden_booker.sample_config import SAMPLE_CONFIG

yaml = pytest.importorskip("yaml")

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def config() -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(SAMPLE_CONFIG))


@pytest.fixture
async def page():
    playwright = await async_playwright().start()
    try:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium is not installed")
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        yield page
        await browser.close()
    finally:
        await playwright.stop()


async def set_fixture(page, name: str) -> None:
    await page.set_content((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.browser
async def test_landing_step_advances_to_dated_availability(page) -> None:
    await page.set_content(
        """
        <label for="location">Location</label>
        <select id="location"><option selected>Hayden Study Rooms</option></select>
        <button onclick="document.body.innerHTML = `
          <label for='date'>Date</label>
          <select id='date'><option value='2026-08-24'>Monday, August 24, 2026</option></select>
        `">Show Availability</button>
        """
    )
    await ensure_availability_step(page)
    assert await page.get_by_role("combobox", name="Date").count() == 1


@pytest.mark.browser
async def test_accessible_availability_and_selection(page, config: AppConfig) -> None:
    await set_fixture(page, "availability.html")
    options = await read_date_options(page)
    assert [option.local_date for option in options] == [date(2026, 8, 24), date(2026, 8, 25)]
    snapshot = await load_snapshot(
        page,
        config,
        date(2026, 8, 24),
        ["Study Room 311A", "Study Room 311B"],
    )
    room = snapshot.rooms[0]
    assert room.enabled_starts == {"13:30", "14:00", "14:30", "15:00"}
    # Simulate form state restored by a persistent browser profile in another room.
    await snapshot.selectable_slots[-2].locator.check()
    await select_exact_slots(
        snapshot,
        room="Study Room 311A",
        required_starts=("13:30", "14:00", "14:30", "15:00"),
    )
    assert await page.get_by_role("checkbox", checked=True).count() == 4
    assert not await snapshot.selectable_slots[-2].locator.is_checked()


@pytest.mark.browser
async def test_auth_page_is_classified_without_typing(page) -> None:
    await set_fixture(page, "auth_required.html")
    assert await classify_auth_state(page) is AuthState.AUTH_REQUIRED
    assert await page.get_by_label("ASURITE User ID").input_value() == ""


@pytest.mark.browser
async def test_booking_form_verification_fills_only_school_id(page, config: AppConfig) -> None:
    await set_fixture(page, "booking_form.html")
    await verify_booking_details(
        page,
        room="Study Room 311A",
        target_date=date(2026, 8, 24),
        start_time="13:30",
        end_time="15:30",
    )
    await fill_school_id(page, config, "secret-test-value")
    assert await page.get_by_label("Name").input_value() == "Prefilled Student"
    assert await page.get_by_label("Email").input_value() == "student@asu.edu"
    assert await page.get_by_label("ASU School ID").input_value() == "secret-test-value"


@pytest.mark.browser
@pytest.mark.parametrize(
    ("fixture", "status"),
    [
        ("confirmation.html", AttemptStatus.CONFIRMED),
        ("conflict.html", AttemptStatus.CONFLICT),
        ("unknown_result.html", AttemptStatus.UNKNOWN_RESULT),
    ],
)
async def test_submission_result_classification(
    page, config: AppConfig, fixture: str, status: AttemptStatus
) -> None:
    await set_fixture(page, fixture)
    result = await classify_submission_result(page, config)
    assert result.status is status
