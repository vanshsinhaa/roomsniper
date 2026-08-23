from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from playwright.async_api import Page, Response
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from hayden_booker.browser.auth import classify_auth_state
from hayden_booker.browser.selectors import (
    CAPTCHA_TEXT,
    CONFLICT_TEXT,
    FINAL_SUBMIT,
    RATE_LIMIT_TEXT,
    SUBMIT_TIMES,
    VALIDATION_TEXT,
)
from hayden_booker.config import AppConfig
from hayden_booker.constants import AttemptStatus, AuthState, ExitCode
from hayden_booker.errors import BookerError, SiteChangedError
from hayden_booker.time_utils import human_time


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    status: AttemptStatus
    message: str
    confirmation_reference: str | None = None
    response_status: int | None = None
    response_path: str | None = None


async def open_booking_details(page: Page) -> AuthState:
    button = page.get_by_role("button", name=SUBMIT_TIMES)
    if await button.count() == 0:
        raise SiteChangedError("The `Submit Times` button was not found after selecting slots.")
    await button.first.click()
    with suppress(PlaywrightTimeoutError):
        await page.wait_for_load_state("domcontentloaded")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 30
    auth_redirect_started: float | None = None
    while loop.time() < deadline:
        visible_text = await page.locator("body").inner_text()
        if RATE_LIMIT_TEXT.search(visible_text) or CAPTCHA_TEXT.search(visible_text):
            raise BookerError(
                "RATE_LIMITED",
                "An access-control or human-verification page interrupted booking.",
                ExitCode.RATE_LIMITED,
                "Stop automatic retries and use the site manually.",
            )
        auth_state = await classify_auth_state(page)
        if auth_state is AuthState.AUTH_REQUIRED:
            if await _interactive_auth_form_visible(page):
                return auth_state
            if auth_redirect_started is None:
                auth_redirect_started = loop.time()
            if loop.time() - auth_redirect_started >= 10:
                return auth_state
            await asyncio.sleep(0.25)
            continue
        auth_redirect_started = None
        details_form = page.locator("#s-lc-eq-form")
        if (
            await details_form.count()
            and await details_form.is_visible()
            and await details_form.locator("input,button,select").count()
        ):
            return auth_state
        final_submit = page.get_by_role("button", name=FINAL_SUBMIT)
        if await final_submit.count() and await final_submit.first.is_visible():
            return auth_state
        await asyncio.sleep(0.25)
    raise SiteChangedError("LibCal did not load the booking-details form after Submit Times.")


async def _interactive_auth_form_visible(page: Page) -> bool:
    user_id = page.get_by_label("ASURITE User ID", exact=False)
    password = page.get_by_label("Password", exact=False)
    try:
        return bool(
            await user_id.count()
            and await password.count()
            and await user_id.first.is_visible()
            and await password.first.is_visible()
        )
    except Exception:
        return False


async def verify_booking_details(
    page: Page,
    *,
    room: str,
    target_date: date,
    start_time: str,
    end_time: str,
) -> None:
    body = re.sub(r"\s+", " ", await page.locator("body").inner_text()).lower()
    expected_date_tokens = (
        target_date.isoformat().lower(),
        target_date.strftime("%B %d, %Y").lower().replace(" 0", " "),
        target_date.strftime("%m/%d/%Y").lower(),
    )
    missing: list[str] = []
    if room.lower() not in body:
        missing.append("room")
    if not any(token in body for token in expected_date_tokens):
        missing.append("date")
    if human_time(start_time).lower() not in body:
        missing.append("start time")
    if human_time(end_time).lower() not in body:
        missing.append("end time")
    if missing:
        raise SiteChangedError(
            "Booking details did not match the intended occurrence: " + ", ".join(missing)
        )


async def fill_school_id(page: Page, config: AppConfig, school_id: str) -> None:
    pattern = re.compile(config.libcal.school_id_label_pattern, re.IGNORECASE)
    field = page.get_by_label(pattern)
    if await field.count() == 0:
        field = page.get_by_role("textbox", name=pattern)
    if await field.count() == 0:
        raise SiteChangedError("The labeled school-ID field was not found on the details form.")
    await field.first.fill(school_id)


async def submit_final_booking(page: Page, config: AppConfig) -> SubmissionResult:
    button = page.get_by_role("button", name=FINAL_SUBMIT)
    if await button.count() == 0:
        raise SiteChangedError("The final booking submission button was not found.")
    checkout_response: Response | None = None
    try:
        async with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and urlsplit(response.url).path == "/ajax/equipment/checkout"
            )
        ) as response_info:
            await button.first.click()
        checkout_response = await response_info.value
    except PlaywrightTimeoutError:
        # The state-changing click already happened. Never click again for diagnostics.
        pass
    with suppress(PlaywrightTimeoutError):
        await page.wait_for_load_state("domcontentloaded")
    # LibCal can finish its checkout navigation asynchronously. Give known success/failure
    # signals a short bounded window to appear before applying the no-retry unknown result.
    for _ in range(20):
        result = await classify_submission_result(page, config)
        if result.status is not AttemptStatus.UNKNOWN_RESULT:
            break
        await asyncio.sleep(0.25)
    return SubmissionResult(
        status=result.status,
        message=result.message,
        confirmation_reference=result.confirmation_reference,
        response_status=(checkout_response.status if checkout_response is not None else None),
        response_path=(
            urlsplit(checkout_response.url).path if checkout_response is not None else None
        ),
    )


async def classify_submission_result(page: Page, config: AppConfig) -> SubmissionResult:
    if await classify_auth_state(page) is AuthState.AUTH_REQUIRED:
        return SubmissionResult(AttemptStatus.AUTH_REQUIRED, "ASU authentication is required.")
    text = re.sub(r"\s+", " ", await page.locator("body").inner_text())
    if RATE_LIMIT_TEXT.search(text) or CAPTCHA_TEXT.search(text):
        return SubmissionResult(AttemptStatus.RATE_LIMITED, "Access control stopped the booking.")
    if CONFLICT_TEXT.search(text):
        return SubmissionResult(AttemptStatus.CONFLICT, "The chosen room became unavailable.")
    if VALIDATION_TEXT.search(text):
        return SubmissionResult(
            AttemptStatus.VALIDATION_FAILED, "LibCal rejected one or more booking fields."
        )
    confirmation_pattern = re.compile(config.libcal.confirmation_text_pattern, re.IGNORECASE)
    confirmation_heading = page.get_by_role("heading", name=confirmation_pattern)
    heading_visible = (
        bool(await confirmation_heading.count()) and await confirmation_heading.first.is_visible()
    )
    strict_confirmation_text = re.search(
        r"booking (?:is )?confirmed|reservation (?:is )?confirmed", text, re.IGNORECASE
    )
    if heading_visible or strict_confirmation_text:
        reference_match = re.search(
            r"(?:reference|confirmation)(?: number| code| id)?\s*[:#]?\s*([A-Z0-9-]{5,40})",
            text,
            re.IGNORECASE,
        )
        reference = reference_match.group(1) if reference_match else None
        return SubmissionResult(AttemptStatus.CONFIRMED, "Reservation confirmed.", reference)
    return SubmissionResult(
        AttemptStatus.UNKNOWN_RESULT,
        "The final page did not contain a known success or failure signal.",
    )
