from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import date

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from hayden_booker.browser.auth import classify_auth_state
from hayden_booker.browser.selectors import (
    CAPTCHA_TEXT,
    DATE_LABEL,
    RATE_LIMIT_TEXT,
    SHOW_AVAILABILITY,
)
from hayden_booker.config import AppConfig
from hayden_booker.constants import AuthState, ExitCode
from hayden_booker.domain.models import RoomAvailability, Slot
from hayden_booker.errors import BookerError, NavigationError, SiteChangedError
from hayden_booker.time_utils import parse_offered_date, parse_slot_label


@dataclass(frozen=True, slots=True)
class DateOption:
    local_date: date
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class SelectableSlot:
    room: str
    start: str
    end: str
    locator: Locator
    enabled: bool
    selected: bool


@dataclass(frozen=True, slots=True)
class AvailabilitySnapshot:
    target_date: date
    rooms: tuple[RoomAvailability, ...]
    selectable_slots: tuple[SelectableSlot, ...]


async def navigate_to_availability(page: Page, config: AppConfig) -> None:
    try:
        response = await page.goto(config.libcal.accessible_url, wait_until="domcontentloaded")
    except PlaywrightTimeoutError as exc:
        raise NavigationError("Timed out while loading the LibCal availability page.") from exc
    if response and response.status in (403, 429):
        raise BookerError(
            "RATE_LIMITED",
            f"LibCal returned HTTP {response.status}; the run stopped without retrying.",
            ExitCode.RATE_LIMITED,
            "Wait and use the site manually if access remains blocked.",
        )
    visible_text = await page.locator("body").inner_text()
    if RATE_LIMIT_TEXT.search(visible_text) or CAPTCHA_TEXT.search(visible_text):
        raise BookerError(
            "RATE_LIMITED",
            "LibCal displayed an access-control or human-verification page.",
            ExitCode.RATE_LIMITED,
            "Stop automatic retries and use the site manually.",
        )
    if await classify_auth_state(page) is AuthState.AUTH_REQUIRED:
        raise BookerError(
            "AUTH_REQUIRED",
            "ASU requires interactive authentication.",
            ExitCode.AUTH_REQUIRED,
            "Run `hayden-booker auth setup`.",
        )
    await ensure_availability_step(page)


async def ensure_availability_step(page: Page) -> None:
    """Advance the public location/category form to the dated availability page."""
    date_control = page.get_by_role("combobox", name=DATE_LABEL)
    if await date_control.count():
        return
    show_button = page.get_by_role("button", name=SHOW_AVAILABILITY)
    if await show_button.count() == 0:
        raise SiteChangedError(
            "LibCal showed neither the Date control nor the Show Availability button."
        )
    try:
        await show_button.first.click()
        await page.wait_for_load_state("domcontentloaded")
    except PlaywrightTimeoutError as exc:
        raise NavigationError("Timed out while opening dated LibCal availability.") from exc
    visible_text = await page.locator("body").inner_text()
    if RATE_LIMIT_TEXT.search(visible_text) or CAPTCHA_TEXT.search(visible_text):
        raise BookerError(
            "RATE_LIMITED",
            "LibCal displayed an access-control or human-verification page.",
            ExitCode.RATE_LIMITED,
            "Stop automatic retries and use the site manually.",
        )
    if await page.get_by_role("combobox", name=DATE_LABEL).count() == 0:
        raise SiteChangedError("The dated availability page did not contain a Date control.")


async def read_date_options(page: Page) -> list[DateOption]:
    combobox = page.get_by_role("combobox", name=DATE_LABEL)
    if await combobox.count() == 0:
        combobox = page.get_by_label(DATE_LABEL)
    if await combobox.count() == 0:
        raise SiteChangedError("The labeled LibCal Date control was not found.")
    raw_options = await combobox.first.locator("option").evaluate_all(
        "options => options.map(o => ({value: o.value, label: o.textContent || ''}))"
    )
    options: list[DateOption] = []
    for raw in raw_options:
        if not isinstance(raw, dict):
            continue
        value = str(raw.get("value", ""))
        label = str(raw.get("label", "")).strip()
        parsed = parse_offered_date(label, value)
        if parsed:
            options.append(DateOption(parsed, value, label))
    if not options:
        raise SiteChangedError("LibCal's Date control contained no parseable dates.")
    return options


async def load_snapshot(
    page: Page,
    config: AppConfig,
    target_date: date,
    room_names: list[str],
) -> AvailabilitySnapshot:
    options = await read_date_options(page)
    selected = next((option for option in options if option.local_date == target_date), None)
    if selected is None:
        raise SiteChangedError(f"Target date {target_date.isoformat()} is not offered by LibCal.")
    combobox = page.get_by_role("combobox", name=DATE_LABEL)
    if await combobox.count() == 0:
        combobox = page.get_by_label(DATE_LABEL)
    await combobox.first.select_option(value=selected.value)
    button = page.get_by_role("button", name=SHOW_AVAILABILITY)
    if await button.count():
        await button.first.click()
    try:
        await page.get_by_role("checkbox").first.wait_for(state="attached")
    except PlaywrightTimeoutError:
        # A valid no-availability page may have room groups but zero checkboxes.
        room_group_found = False
        for room in room_names:
            if await _room_group(page, room) is not None:
                room_group_found = True
                break
        if not room_group_found:
            raise SiteChangedError(
                "No configured room groups or availability checkboxes were found."
            ) from None
    parsed_rooms: list[RoomAvailability] = []
    selectable: list[SelectableSlot] = []
    found_any_room = False
    for room_name in room_names:
        group = await _room_group(page, room_name)
        if group is None:
            parsed_rooms.append(RoomAvailability(room=room_name, slots=()))
            continue
        found_any_room = True
        slots: list[Slot] = []
        checkboxes = group.get_by_role("checkbox")
        for index in range(await checkboxes.count()):
            checkbox = checkboxes.nth(index)
            label = await _checkbox_label(checkbox)
            parsed = parse_slot_label(label)
            if not parsed:
                continue
            start, end = parsed
            enabled = await checkbox.is_enabled()
            checked = await checkbox.is_checked()
            slots.append(Slot(start=start, end=end, enabled=enabled, selected=checked))
            selectable.append(
                SelectableSlot(room_name, start, end, checkbox, enabled=enabled, selected=checked)
            )
        parsed_rooms.append(RoomAvailability(room=room_name, slots=tuple(slots)))
    if not found_any_room:
        raise SiteChangedError(
            "None of the configured room groups could be found by accessible name."
        )
    return AvailabilitySnapshot(target_date, tuple(parsed_rooms), tuple(selectable))


async def select_exact_slots(
    snapshot: AvailabilitySnapshot,
    *,
    room: str,
    required_starts: tuple[str, ...],
) -> None:
    targets = [
        slot
        for slot in snapshot.selectable_slots
        if slot.room == room and slot.start in required_starts and slot.enabled
    ]
    selected_by_start = {slot.start: slot for slot in targets}
    if set(selected_by_start) != set(required_starts):
        raise SiteChangedError(
            "The selected room no longer contains the complete requested interval."
        )
    try:
        # Persistent profiles can restore form state. Clear anything outside the exact request.
        for slot in snapshot.selectable_slots:
            is_target = slot.room == room and slot.start in required_starts
            if not is_target and await slot.locator.is_checked():
                await slot.locator.uncheck()
        for start in required_starts:
            locator = selected_by_start[start].locator
            await locator.check()
        selected_slots = [
            slot for slot in snapshot.selectable_slots if await slot.locator.is_checked()
        ]
        selected_identity = {(slot.room, slot.start) for slot in selected_slots}
        expected_identity = {(room, start) for start in required_starts}
        if len(selected_slots) != len(required_starts) or selected_identity != expected_identity:
            raise SiteChangedError(
                "LibCal did not retain exactly the requested single-room slot selection."
            )
    except Exception:
        for slot in snapshot.selectable_slots:
            with suppress(Exception):
                if await slot.locator.is_checked():
                    await slot.locator.uncheck()
        raise


async def _room_group(page: Page, room_name: str) -> Locator | None:
    exact_name = re.compile(rf"\b{re.escape(room_name)}\b", re.IGNORECASE)
    group = page.get_by_role("group", name=exact_name)
    if await group.count():
        return group.first
    fieldset = page.locator("fieldset").filter(has_text=exact_name)
    if await fieldset.count():
        return fieldset.first
    return None


async def _checkbox_label(checkbox: Locator) -> str:
    aria = await checkbox.get_attribute("aria-label")
    if aria:
        return aria
    label = await checkbox.evaluate(
        "el => {"
        " const labels = el.labels ? "
        "Array.from(el.labels).map(x => x.textContent || '').join(' ') : '';"
        " return labels || el.getAttribute('title') || el.value || '';"
        "}"
    )
    return str(label)
