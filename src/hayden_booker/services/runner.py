from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from time import monotonic

from playwright.async_api import Error as PlaywrightError

from hayden_booker.browser.auth import classify_auth_state
from hayden_booker.browser.availability import (
    load_snapshot,
    navigate_to_availability,
    read_date_options,
    select_exact_slots,
)
from hayden_booker.browser.booking import (
    fill_school_id,
    open_booking_details,
    submit_final_booking,
    verify_booking_details,
)
from hayden_booker.browser.context import BrowserSession
from hayden_booker.browser.diagnostics import (
    capture_screenshot,
    clean_old_screenshots,
    collect_semantic_diagnostics,
)
from hayden_booker.config import AppConfig, ScheduleRule, app_data_dir, find_schedule
from hayden_booker.constants import AttemptStatus, AuthState, ExitCode
from hayden_booker.domain.matching import choose_room
from hayden_booker.domain.models import OccurrenceKey
from hayden_booker.domain.result import RunResult
from hayden_booker.domain.schedule import resolve_target_date
from hayden_booker.errors import BookerError, SiteChangedError
from hayden_booker.logging_setup import EventLogger
from hayden_booker.persistence.repository import ReservationRepository
from hayden_booker.security.secrets import SecretStoreError, get_school_id
from hayden_booker.services.notifier import Notifier


class BookingRunner:
    def __init__(
        self,
        config: AppConfig,
        repository: ReservationRepository,
        logger: EventLogger,
    ) -> None:
        self.config = config
        self.repository = repository
        self.logger = logger
        self.notifier = Notifier(config.notifications)

    async def run(
        self,
        *,
        live: bool,
        due: bool = False,
        schedule_id: str | None = None,
        target_date: date | None = None,
    ) -> list[RunResult]:
        started = monotonic()
        self.logger.event("run_started", status="live" if live else "dry-run")
        if target_date is not None and schedule_id is None:
            raise ValueError("--target-date requires --schedule-id")
        rules = self._selected_rules(schedule_id)
        school_id: str | None = None
        if live:
            if not self.config.live_booking_enabled:
                raise BookerError(
                    "LIVE_BOOKING_DISABLED",
                    "Live booking is disabled in config.yaml.",
                    ExitCode.CONFIG_INVALID,
                    "Set live_booking_enabled: true only after completing a dry run.",
                )
            try:
                school_id = get_school_id()
            except SecretStoreError as exc:
                raise BookerError(
                    "SECRET_STORE_UNAVAILABLE",
                    str(exc),
                    ExitCode.SECRET_MISSING,
                    "Run `hayden-booker secret set-school-id` after fixing the credential store.",
                ) from exc
            if not school_id:
                raise BookerError(
                    "SCHOOL_ID_MISSING",
                    "No school ID exists in the operating-system credential store.",
                    ExitCode.SECRET_MISSING,
                    "Run `hayden-booker secret set-school-id`.",
                )
        headless = self.config.browser.scheduled_headless
        results: list[RunResult] = []
        async with BrowserSession(self.config, headless=headless) as session:
            self.logger.event("lock_acquired")
            page = await session.get_page()
            await navigate_to_availability(page, self.config)
            auth_state = await classify_auth_state(page)
            self.logger.event("authentication_checked", status=auth_state.value)
            date_options = await read_date_options(page)
            offered_dates = [option.local_date for option in date_options]
            today = datetime.now(self.config.zone).date()
            for rule in rules:
                resolved = target_date or resolve_target_date(
                    offered_dates,
                    rule,
                    today=today,
                    processed_dates=(self.repository.processed_dates(rule.id) if due else set()),
                )
                if resolved is None:
                    self.logger.event(
                        "target_date_resolved", schedule_id=rule.id, status="nothing_due"
                    )
                    continue
                latest = today + timedelta(days=7)
                if (
                    resolved < today
                    or resolved > latest
                    or resolved.strftime("%A").lower() != rule.weekday
                ):
                    results.append(
                        RunResult(
                            AttemptStatus.SITE_CHANGED,
                            ExitCode.CONFIG_INVALID,
                            f"Target date {resolved.isoformat()} is outside the rule's "
                            "weekday or booking horizon.",
                        )
                    )
                    continue
                if resolved not in offered_dates:
                    results.append(
                        RunResult(
                            AttemptStatus.SITE_CHANGED,
                            ExitCode.CONFIG_INVALID,
                            f"Target date {resolved.isoformat()} is not currently "
                            "offered by LibCal.",
                        )
                    )
                    continue
                self.logger.event(
                    "target_date_resolved",
                    schedule_id=rule.id,
                    target_date=resolved.isoformat(),
                )
                results.append(
                    await self._run_occurrence(page, rule, resolved, live=live, school_id=school_id)
                )
                # Each submitted or dry-run flow can leave the page in a different state.
                if rule is not rules[-1]:
                    await navigate_to_availability(page, self.config)
        duration_ms = round((monotonic() - started) * 1000)
        self.logger.event("run_completed", duration_ms=duration_ms)
        if not results:
            results.append(RunResult(AttemptStatus.PLANNED, ExitCode.OK, "Nothing is due."))
        return results

    def _selected_rules(self, schedule_id: str | None) -> list[ScheduleRule]:
        if schedule_id:
            rule = find_schedule(self.config, schedule_id)
            if not rule.enabled:
                raise ValueError(f"schedule is disabled: {schedule_id}")
            return [rule]
        return [rule for rule in self.config.schedules if rule.enabled]

    async def _run_occurrence(
        self,
        page: object,
        rule: ScheduleRule,
        target_date: date,
        *,
        live: bool,
        school_id: str | None,
    ) -> RunResult:
        # Kept as a separate method so every browser failure can be tied to an occurrence.
        from playwright.async_api import Page

        assert isinstance(page, Page)
        key = OccurrenceKey(
            schedule_id=rule.id,
            target_date=target_date,
            start_time=rule.start_time,
            end_time=rule.end_time,
        )
        acquired = self.repository.ensure_occurrence(key)
        occurrence = acquired.occurrence
        if occurrence.status is AttemptStatus.CONFIRMED:
            return RunResult(
                AttemptStatus.CONFIRMED,
                ExitCode.OK,
                "Occurrence was already confirmed; no submission was made.",
                occurrence.id,
                occurrence.chosen_room,
                occurrence.confirmation_reference,
            )
        if occurrence.status in {
            AttemptStatus.UNKNOWN_RESULT,
            AttemptStatus.MANUAL_REVIEW_REQUIRED,
        }:
            self.notifier.failure(
                "Hayden booking needs review",
                f"Check occurrence {occurrence.id} manually; do not resubmit.",
            )
            return RunResult(
                occurrence.status,
                ExitCode.UNKNOWN_RESULT,
                "Occurrence requires manual review; automatic submission is blocked.",
                occurrence.id,
                occurrence.chosen_room,
            )
        if live and occurrence.status is AttemptStatus.SUBMITTING:
            decision = self.repository.begin_submission(
                occurrence.id,
                occurrence.chosen_room or "unknown",
                safety_timeout_minutes=self.config.scheduler.submitting_safety_timeout_minutes,
            )
            status = decision.occurrence.status
            if status is AttemptStatus.MANUAL_REVIEW_REQUIRED:
                self.notifier.failure(
                    "Hayden booking needs review",
                    f"Check occurrence {occurrence.id} manually; do not resubmit.",
                )
            return RunResult(
                status,
                ExitCode.UNKNOWN_RESULT
                if status is AttemptStatus.MANUAL_REVIEW_REQUIRED
                else ExitCode.LOCKED,
                decision.reason or "Submission is blocked.",
                occurrence.id,
                occurrence.chosen_room,
            )
        try:
            return await self._attempt_rooms(
                page,
                rule,
                target_date,
                occurrence.id,
                live=live,
                school_id=school_id,
            )
        except BookerError as exc:
            status = _status_for_exit(exc.exit_code)
            self.repository.update_status(
                occurrence.id,
                status,
                error_code=exc.code,
                error_summary=exc.message,
            )
            if status is AttemptStatus.SITE_CHANGED:
                await self._diagnose(page, occurrence.id)
                self.notifier.failure(
                    "Hayden Booker site changed",
                    "Run hayden-booker doctor and inspect local diagnostics.",
                )
            self.logger.event(
                "run_completed",
                level=logging.ERROR,
                schedule_id=rule.id,
                target_date=target_date.isoformat(),
                status=status.value,
                error_code=exc.code,
            )
            return RunResult(status, exc.exit_code, exc.message, occurrence.id)
        except PlaywrightError as exc:
            message = f"Playwright navigation failed: {type(exc).__name__}"
            self.repository.update_status(
                occurrence.id,
                AttemptStatus.NETWORK_ERROR,
                error_code="PLAYWRIGHT_ERROR",
                error_summary=message,
            )
            return RunResult(
                AttemptStatus.NETWORK_ERROR,
                ExitCode.NETWORK_ERROR,
                message,
                occurrence.id,
            )

    async def _attempt_rooms(
        self,
        page: object,
        rule: ScheduleRule,
        target_date: date,
        occurrence_id: str,
        *,
        live: bool,
        school_id: str | None,
    ) -> RunResult:
        from playwright.async_api import Page

        assert isinstance(page, Page)
        excluded: set[str] = set()
        max_attempts = self.config.scheduler.max_booking_attempts if live else 1
        for attempt_number in range(1, max_attempts + 1):
            self.repository.update_status(
                occurrence_id,
                AttemptStatus.CHECKING_AVAILABILITY,
                details={"attempt_number": attempt_number},
            )
            snapshot = await load_snapshot(page, self.config, target_date, rule.room_preferences)
            self.logger.event(
                "availability_loaded",
                schedule_id=rule.id,
                target_date=target_date.isoformat(),
                attempt_number=attempt_number,
            )
            match, rejected = choose_room(
                list(snapshot.rooms),
                rule.room_preferences,
                rule.start_time,
                rule.end_time,
                excluded=excluded,
            )
            for rejection in rejected:
                self.repository.add_event(
                    occurrence_id,
                    "room_rejected",
                    room=rejection.room,
                    details={"missing_blocks": rejection.missing_starts},
                )
                self.logger.event(
                    "room_rejected",
                    schedule_id=rule.id,
                    room=rejection.room,
                    status="missing_blocks",
                )
            if match is None:
                self.repository.update_status(
                    occurrence_id,
                    AttemptStatus.NO_AVAILABILITY,
                    error_code="NO_COMPLETE_INTERVAL",
                    error_summary="No preferred room contains every requested half-hour block.",
                )
                self.notifier.failure(
                    "No Hayden room available",
                    f"{target_date.isoformat()} {rule.start_time}-{rule.end_time}",
                )
                return RunResult(
                    AttemptStatus.NO_AVAILABILITY,
                    ExitCode.NO_AVAILABILITY,
                    "No preferred room contains the complete interval.",
                    occurrence_id,
                )
            self.logger.event(
                "room_selected",
                schedule_id=rule.id,
                target_date=target_date.isoformat(),
                room=match.room,
                attempt_number=attempt_number,
            )
            await select_exact_slots(
                snapshot, room=match.room, required_starts=match.required_starts
            )
            if not live:
                self.repository.update_status(
                    occurrence_id,
                    AttemptStatus.DRY_RUN_COMPLETE,
                    room=match.room,
                    details={"selected_blocks": len(match.required_starts)},
                )
                self.logger.event(
                    "dry_run_completed",
                    schedule_id=rule.id,
                    target_date=target_date.isoformat(),
                    room=match.room,
                    status=AttemptStatus.DRY_RUN_COMPLETE.value,
                )
                return RunResult(
                    AttemptStatus.DRY_RUN_COMPLETE,
                    ExitCode.OK,
                    f"Would reserve {match.room}.",
                    occurrence_id,
                    match.room,
                )
            assert school_id is not None
            decision = self.repository.begin_submission(
                occurrence_id,
                match.room,
                safety_timeout_minutes=self.config.scheduler.submitting_safety_timeout_minutes,
            )
            if not decision.allowed:
                return RunResult(
                    decision.occurrence.status,
                    ExitCode.UNKNOWN_RESULT,
                    decision.reason or "Submission gate rejected the occurrence.",
                    occurrence_id,
                    match.room,
                )
            self.logger.event(
                "submission_started",
                schedule_id=rule.id,
                target_date=target_date.isoformat(),
                room=match.room,
                attempt_number=attempt_number,
            )
            auth_state = await open_booking_details(page)
            if auth_state is AuthState.AUTH_REQUIRED:
                self.repository.update_status(
                    occurrence_id,
                    AttemptStatus.AUTH_REQUIRED,
                    room=match.room,
                    error_code="AUTH_REQUIRED",
                    error_summary="Interactive ASU authentication is required.",
                )
                self.logger.event("authentication_required", schedule_id=rule.id)
                self.notifier.auth_required()
                return RunResult(
                    AttemptStatus.AUTH_REQUIRED,
                    ExitCode.AUTH_REQUIRED,
                    "ASU sign-in is required; no credentials were entered.",
                    occurrence_id,
                    match.room,
                )
            if auth_state is AuthState.AUTH_INDETERMINATE:
                raise SiteChangedError(
                    "Could not determine authentication state after Submit Times.",
                    occurrence_id=occurrence_id,
                )
            await verify_booking_details(
                page,
                room=match.room,
                target_date=target_date,
                start_time=rule.start_time,
                end_time=rule.end_time,
            )
            await fill_school_id(page, self.config, school_id)
            # Final safety check immediately before the state-changing click.
            await verify_booking_details(
                page,
                room=match.room,
                target_date=target_date,
                start_time=rule.start_time,
                end_time=rule.end_time,
            )
            current = self.repository.get(occurrence_id)
            if current.status is not AttemptStatus.SUBMITTING:
                raise SiteChangedError("Occurrence state changed before final submission.")
            result = await submit_final_booking(page, self.config)
            self.repository.add_event(
                occurrence_id,
                "submission_response",
                room=match.room,
                details={
                    "http_status": result.response_status,
                    "path": result.response_path,
                },
            )
            if result.status is AttemptStatus.CONFIRMED:
                self.repository.update_status(
                    occurrence_id,
                    AttemptStatus.CONFIRMED,
                    room=match.room,
                    confirmation_reference=result.confirmation_reference,
                )
                self.logger.event(
                    "booking_confirmed",
                    schedule_id=rule.id,
                    target_date=target_date.isoformat(),
                    room=match.room,
                    status=AttemptStatus.CONFIRMED.value,
                )
                self.notifier.success(
                    f"{match.room}, {target_date.isoformat()}, {rule.start_time}-{rule.end_time}"
                )
                return RunResult(
                    AttemptStatus.CONFIRMED,
                    ExitCode.OK,
                    result.message,
                    occurrence_id,
                    match.room,
                    result.confirmation_reference,
                )
            if result.status is AttemptStatus.CONFLICT:
                self.repository.update_status(
                    occurrence_id,
                    AttemptStatus.CONFLICT,
                    room=match.room,
                    error_code="BOOKING_CONFLICT",
                    error_summary=result.message,
                )
                self.logger.event("booking_conflict", schedule_id=rule.id, room=match.room)
                excluded.add(match.room)
                if attempt_number < max_attempts:
                    await asyncio.sleep(self.config.scheduler.retry_delay_seconds)
                    await navigate_to_availability(page, self.config)
                    continue
                return RunResult(
                    AttemptStatus.CONFLICT,
                    ExitCode.CONFLICT,
                    "All bounded booking attempts ended in explicit conflicts.",
                    occurrence_id,
                    match.room,
                )
            if result.status is AttemptStatus.AUTH_REQUIRED:
                exit_code = ExitCode.AUTH_REQUIRED
                self.notifier.auth_required()
            elif result.status is AttemptStatus.RATE_LIMITED:
                exit_code = ExitCode.RATE_LIMITED
            elif result.status is AttemptStatus.VALIDATION_FAILED:
                exit_code = ExitCode.VALIDATION_FAILED
            else:
                exit_code = ExitCode.UNKNOWN_RESULT
            self.repository.update_status(
                occurrence_id,
                result.status,
                room=match.room,
                error_code=result.status.value,
                error_summary=result.message,
            )
            final_status = result.status
            if result.status is AttemptStatus.UNKNOWN_RESULT:
                await self._diagnose(page, occurrence_id)
                self.repository.update_status(
                    occurrence_id,
                    AttemptStatus.MANUAL_REVIEW_REQUIRED,
                    room=match.room,
                    error_code="UNKNOWN_RESULT",
                    error_summary=result.message,
                )
                final_status = AttemptStatus.MANUAL_REVIEW_REQUIRED
                self.logger.event("manual_review_required", schedule_id=rule.id, room=match.room)
                self.notifier.failure(
                    "Hayden booking needs review",
                    f"Check LibCal manually for {target_date.isoformat()}; do not resubmit.",
                )
            return RunResult(final_status, exit_code, result.message, occurrence_id, match.room)
        return RunResult(
            AttemptStatus.CONFLICT,
            ExitCode.CONFLICT,
            "Booking attempts were exhausted.",
            occurrence_id,
        )

    async def _diagnose(self, page: object, occurrence_id: str) -> None:
        from playwright.async_api import Page

        assert isinstance(page, Page)
        try:
            details = await collect_semantic_diagnostics(page)
            self.repository.add_event(occurrence_id, "site_diagnostics", details=details)
            if self.config.diagnostics.capture_screenshots:
                directory = app_data_dir() / "diagnostics"
                clean_old_screenshots(directory, self.config.diagnostics.screenshot_retention_days)
                await capture_screenshot(page, directory, occurrence_id)
        except PlaywrightError:
            pass


def _status_for_exit(exit_code: ExitCode) -> AttemptStatus:
    return {
        ExitCode.AUTH_REQUIRED: AttemptStatus.AUTH_REQUIRED,
        ExitCode.NO_AVAILABILITY: AttemptStatus.NO_AVAILABILITY,
        ExitCode.RATE_LIMITED: AttemptStatus.RATE_LIMITED,
        ExitCode.NETWORK_ERROR: AttemptStatus.NETWORK_ERROR,
        ExitCode.VALIDATION_FAILED: AttemptStatus.VALIDATION_FAILED,
        ExitCode.UNKNOWN_RESULT: AttemptStatus.MANUAL_REVIEW_REQUIRED,
    }.get(exit_code, AttemptStatus.SITE_CHANGED)
