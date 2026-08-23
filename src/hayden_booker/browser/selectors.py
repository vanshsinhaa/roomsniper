from __future__ import annotations

import re

DATE_LABEL = re.compile(r"^date$", re.IGNORECASE)
SHOW_AVAILABILITY = re.compile(r"show availability", re.IGNORECASE)
SUBMIT_TIMES = re.compile(r"submit times", re.IGNORECASE)
FINAL_SUBMIT = re.compile(
    r"submit (?:my )?booking|confirm booking|complete reservation|book now", re.IGNORECASE
)
CONFLICT_TEXT = re.compile(
    r"no longer available|already (?:been )?(?:reserved|booked)|booking conflict",
    re.IGNORECASE,
)
VALIDATION_TEXT = re.compile(r"required field|please correct|validation error", re.IGNORECASE)
RATE_LIMIT_TEXT = re.compile(r"too many requests|rate limit|access denied|forbidden", re.IGNORECASE)
CAPTCHA_TEXT = re.compile(r"captcha|verify you are human|bot detection", re.IGNORECASE)
