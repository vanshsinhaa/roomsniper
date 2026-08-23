from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from hayden_booker.browser.availability import navigate_to_availability, read_date_options
from hayden_booker.browser.context import BrowserSession
from hayden_booker.config import AppConfig
from hayden_booker.persistence.repository import ReservationRepository


async def observe_release(
    config: AppConfig,
    repository: ReservationRepository,
    *,
    maximum_minutes: int = 15,
) -> bool:
    interval = max(30, config.scheduler.release_observation_interval_seconds)
    async with BrowserSession(config, headless=config.browser.scheduled_headless) as session:
        page = await session.get_page()
        deadline = datetime.now(config.zone) + timedelta(minutes=maximum_minutes)
        target = datetime.now(config.zone).date() + timedelta(days=7)
        while datetime.now(config.zone) <= deadline:
            await navigate_to_availability(page, config)
            dates = [option.local_date for option in await read_date_options(page)]
            visible = target in dates
            repository.add_release_observation(
                local_date=datetime.now(config.zone).date(),
                furthest_visible_date=max(dates) if dates else None,
                target_visible=visible,
            )
            if visible:
                return True
            await asyncio.sleep(interval)
    return False
