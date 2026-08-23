from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Page


async def collect_semantic_diagnostics(page: Page) -> dict[str, object]:
    body = re.sub(r"\s+", " ", await page.locator("body").inner_text())
    return {
        "hostname": urlsplit(page.url).hostname,
        "title": (await page.title())[:200],
        "headings": await _texts(page, "h1,h2,h3"),
        "buttons": await _texts(page, "button,input[type=submit]"),
        "labels": await _texts(page, "label"),
        "booking_form_labels": await _texts(page, "#s-lc-eq-form label"),
        "booking_form_controls": await page.locator(
            "#s-lc-eq-form input,#s-lc-eq-form select,#s-lc-eq-form button"
        ).evaluate_all(
            "controls => controls.slice(0, 30).map(el => ({"
            "tag: el.tagName.toLowerCase(), type: el.type || null, name: el.name || null, "
            "id: el.id || null, ariaLabel: el.getAttribute('aria-label')}))"
        ),
        "body_text_length": len(body),
        "body_signals": [
            signal
            for signal in (
                "thank",
                "confirm",
                "success",
                "error",
                "invalid",
                "required",
                "booking",
                "reservation",
            )
            if re.search(rf"\b{signal}\w*\b", body, re.IGNORECASE)
        ],
    }


async def capture_screenshot(page: Page, directory: Path, stem: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "-", stem)[:80]
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{timestamp}-{safe_stem}.png"
    await page.screenshot(path=path, full_page=False)
    return path


def clean_old_screenshots(directory: Path, retention_days: int) -> None:
    if not directory.exists():
        return
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    for path in directory.glob("*.png"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified < cutoff:
            path.unlink(missing_ok=True)


async def _texts(page: Page, selector: str) -> list[str]:
    values = await page.locator(selector).all_inner_texts()
    return [re.sub(r"\s+", " ", value).strip()[:160] for value in values[:30] if value.strip()]
