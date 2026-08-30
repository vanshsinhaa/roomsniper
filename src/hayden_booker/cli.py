from __future__ import annotations

import asyncio
import sys
import webbrowser
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from playwright.async_api import async_playwright

from hayden_booker.browser.auth import classify_auth_state, navigate_and_check_auth
from hayden_booker.browser.availability import navigate_to_availability
from hayden_booker.browser.context import BrowserSession, profile_lock_path
from hayden_booker.browser.selectors import FINAL_SUBMIT, SUBMIT_TIMES
from hayden_booker.config import AppConfig, app_data_dir, database_path, load_config
from hayden_booker.constants import AttemptStatus, AuthState, ExitCode
from hayden_booker.errors import BookerError
from hayden_booker.logging_setup import configure_logging
from hayden_booker.persistence.database import connect
from hayden_booker.persistence.repository import ReservationRepository
from hayden_booker.sample_config import SAMPLE_CONFIG
from hayden_booker.security.browser_state import (
    browser_auth_state_exists,
    save_browser_auth_state,
)
from hayden_booker.security.secrets import (
    SecretStoreError,
    google_calendar_credentials_exist,
    school_id_exists,
    set_school_id_interactive,
)
from hayden_booker.services.google_calendar import (
    CalendarAuthorizationError,
    authorize_google_calendar,
)
from hayden_booker.services.release_observer import observe_release
from hayden_booker.services.runner import BookingRunner
from hayden_booker.ui.server import DEFAULT_HOST, DEFAULT_PORT, create_server

app = typer.Typer(
    name="hayden-booker",
    help="Safely reserve ASU Hayden Library rooms from a local recurring schedule.",
    no_args_is_help=True,
)
auth_app = typer.Typer(help="Manage the dedicated ASU browser session.")
secret_app = typer.Typer(help="Store sensitive values in the operating-system credential store.")
config_app = typer.Typer(help="Validate configuration.")
schedule_app = typer.Typer(help="Inspect recurring schedules.")
calendar_app = typer.Typer(help="Connect automatic Google Calendar event delivery.")
app.add_typer(auth_app, name="auth")
app.add_typer(secret_app, name="secret")
app.add_typer(config_app, name="config")
app.add_typer(schedule_app, name="schedule")
app.add_typer(calendar_app, name="calendar")


@dataclass(slots=True)
class CliState:
    config_path: Path
    verbose: bool


@app.callback()
def main_callback(
    ctx: typer.Context,
    config_path: Annotated[
        Path, typer.Option("--config", help="Path to config.yaml.", dir_okay=False)
    ] = Path("config.yaml"),
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    ctx.obj = CliState(config_path.resolve(), verbose)


@app.command("init")
def init_command(ctx: typer.Context) -> None:
    """Create local configuration and runtime data without fake credentials."""
    state = _state(ctx)
    data = app_data_dir()
    for path in (data, data / "logs", data / "diagnostics"):
        path.mkdir(parents=True, exist_ok=True)
    if state.config_path.exists():
        typer.echo(f"Kept existing configuration: {state.config_path}")
    else:
        state.config_path.write_text(SAMPLE_CONFIG, encoding="utf-8")
        typer.echo(f"Created configuration: {state.config_path}")
    connection = connect(database_path())
    connection.close()
    _ensure_gitignore(Path.cwd() / ".gitignore")
    typer.echo(f"Initialized application data: {data}")
    typer.echo("No credentials were created. Review config.yaml before continuing.")


@config_app.command("validate")
def validate_command(ctx: typer.Context) -> None:
    """Validate syntax, safety limits, known rooms, and live-mode prerequisites."""
    try:
        config, warnings = _load(ctx)
        for warning in warnings:
            typer.echo(f"WARNING {warning}", err=True)
        if config.live_booking_enabled and not school_id_exists():
            _fail(
                "SCHOOL_ID_MISSING",
                "Live booking is enabled but no school ID exists in the credential store.",
                ExitCode.SECRET_MISSING,
                "Run `hayden-booker secret set-school-id`.",
            )
        if config.calendar.enabled and not google_calendar_credentials_exist():
            _fail(
                "GOOGLE_CALENDAR_AUTH_MISSING",
                "Automatic calendar adds are enabled, but Google Calendar is not connected.",
                ExitCode.AUTH_REQUIRED,
                "Run `hayden-booker calendar connect --credentials CLIENT_SECRET.json`.",
            )
        typer.echo(f"Configuration valid: {len(config.schedules)} schedule(s)")
    except (ValueError, SecretStoreError) as exc:
        _fail(
            "CONFIG_INVALID",
            str(exc),
            ExitCode.CONFIG_INVALID,
            "Correct config.yaml and run `hayden-booker config validate` again.",
        )


@schedule_app.command("show")
def schedule_show(ctx: typer.Context) -> None:
    """Print enabled state, weekday, interval, and ranked room preferences."""
    config, warnings = _load_or_exit(ctx)
    for warning in warnings:
        typer.echo(f"WARNING {warning}", err=True)
    for rule in config.schedules:
        state = "enabled" if rule.enabled else "disabled"
        rooms = " > ".join(rule.room_preferences)
        typer.echo(
            f"{rule.id}: {state}; {rule.weekday.title()} {rule.start_time}-{rule.end_time}; {rooms}"
        )


@secret_app.command("set-school-id")
def set_school_id_command() -> None:
    """Prompt without echo and store the school ID in the OS credential store."""
    try:
        set_school_id_interactive()
    except SecretStoreError as exc:
        _fail(
            "SECRET_STORE_UNAVAILABLE",
            str(exc),
            ExitCode.SECRET_MISSING,
            "Install a supported keyring backend and retry.",
        )
    typer.echo("School ID stored in the operating-system credential store.")


@calendar_app.command("connect")
def calendar_connect_command(
    ctx: typer.Context,
    credentials: Annotated[
        Path,
        typer.Option(
            "--credentials",
            help="Downloaded Google OAuth desktop-client JSON file.",
            dir_okay=False,
            exists=True,
            readable=True,
        ),
    ],
) -> None:
    """Authorize event creation and keep the resulting token in the OS credential store."""
    config, _ = _load_or_exit(ctx)
    try:
        authorize_google_calendar(credentials.resolve())
    except (CalendarAuthorizationError, SecretStoreError) as exc:
        _fail(
            "GOOGLE_CALENDAR_AUTH_FAILED",
            str(exc),
            ExitCode.AUTH_REQUIRED,
            "Check the OAuth desktop-client file and run `hayden-booker calendar connect` again.",
        )
    typer.echo("Google Calendar authorization stored in the operating-system credential store.")
    if config.calendar.enabled:
        typer.echo(f"Confirmed bookings will be added to calendar {config.calendar.calendar_id!r}.")
    else:
        typer.echo("Set `calendar.enabled: true` in config.yaml to turn on automatic adds.")


@calendar_app.command("status")
def calendar_status_command(ctx: typer.Context) -> None:
    """Show connection and automatic-add state without printing credentials."""
    config, _ = _load_or_exit(ctx)
    try:
        connected = google_calendar_credentials_exist()
    except SecretStoreError as exc:
        _fail(
            "SECRET_STORE_UNAVAILABLE",
            str(exc),
            ExitCode.SECRET_MISSING,
            "Install a supported keyring backend and retry.",
        )
    typer.echo(f"Google Calendar authorization: {'connected' if connected else 'not connected'}")
    typer.echo(f"Automatic adds: {'enabled' if config.calendar.enabled else 'disabled'}")
    typer.echo(f"Destination calendar: {config.calendar.calendar_id}")


@auth_app.command("setup")
def auth_setup_command(ctx: typer.Context) -> None:
    """Open the dedicated profile so the user can complete ASU and Duo sign-in."""
    config, _ = _load_or_exit(ctx)
    typer.echo("Opening the dedicated Hayden Booker browser profile.")
    typer.echo("Complete ASU/Duo sign-in only in that browser; never enter credentials here.")
    try:
        authenticated = asyncio.run(_manual_auth_setup(config))
    except BookerError as exc:
        _booker_fail(exc)
    if not authenticated:
        _fail(
            "AUTH_REQUIRED",
            "The browser did not return to an authenticated LibCal page.",
            ExitCode.AUTH_REQUIRED,
            "Run `hayden-booker auth setup` and finish ASU/Duo sign-in.",
        )
    typer.echo("Authenticated browser session saved locally for later automated runs.")


@auth_app.command("check")
def auth_check_command(ctx: typer.Context) -> None:
    """Inspect authentication signals without selecting or submitting slots."""
    config, _ = _load_or_exit(ctx)
    try:
        state = asyncio.run(_auth_check(config))
    except BookerError as exc:
        _booker_fail(exc)
    if state is AuthState.AUTH_REQUIRED:
        _fail(
            "AUTH_REQUIRED",
            "ASU requires interactive authentication.",
            ExitCode.AUTH_REQUIRED,
            "Run `hayden-booker auth setup`.",
        )
    if state is AuthState.AUTH_INDETERMINATE:
        _fail(
            "AUTH_INDETERMINATE",
            "The current page did not provide a reliable authentication signal.",
            ExitCode.SITE_CHANGED,
            "Run `hayden-booker auth setup`, then retry this check.",
        )
    typer.echo("Authentication profile is available (no reservation action was taken).")


@app.command("observe-release")
def observe_release_command(
    ctx: typer.Context,
    maximum_minutes: Annotated[int, typer.Option("--maximum-minutes", min=1, max=60)] = 15,
) -> None:
    """Observe the seventh date at a safe interval without selecting any slot."""
    config, _ = _load_or_exit(ctx)
    repository = ReservationRepository(connect(database_path()))
    try:
        visible = asyncio.run(observe_release(config, repository, maximum_minutes=maximum_minutes))
    except BookerError as exc:
        _booker_fail(exc)
    typer.echo("Target date became visible." if visible else "Observation window ended safely.")


@app.command("run")
def run_command(
    ctx: typer.Context,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Never click Submit Times.")] = False,
    live: Annotated[bool, typer.Option("--live", help="Allow gated live submission.")] = False,
    due: Annotated[bool, typer.Option("--due", help="Process only unprocessed due rules.")] = False,
    schedule_id: Annotated[str | None, typer.Option("--schedule-id")] = None,
    target_date: Annotated[
        str | None, typer.Option("--target-date", help="Target date in YYYY-MM-DD format.")
    ] = None,
) -> None:
    """Match availability; defaults to a non-submitting dry run."""
    if dry_run and live:
        _fail(
            "CONFIG_INVALID",
            "Choose either --dry-run or --live, not both.",
            ExitCode.CONFIG_INVALID,
            "Re-run with exactly one mode; omitting both is a dry run.",
        )
    config, warnings = _load_or_exit(ctx)
    parsed_target_date: date | None = None
    if target_date:
        try:
            parsed_target_date = date.fromisoformat(target_date)
        except ValueError:
            _fail(
                "CONFIG_INVALID",
                "--target-date must use YYYY-MM-DD format.",
                ExitCode.CONFIG_INVALID,
                "Correct the date and retry.",
            )
    state = _state(ctx)
    logger = configure_logging(app_data_dir() / "logs", verbose=state.verbose)
    for warning in warnings:
        logger.event("configuration_warning", level=30, error_code=warning)
    logger.event("configuration_loaded", status="valid")
    repository = ReservationRepository(connect(database_path()))
    runner = BookingRunner(config, repository, logger)
    try:
        results = asyncio.run(
            asyncio.wait_for(
                runner.run(
                    live=live,
                    due=due,
                    schedule_id=schedule_id,
                    target_date=parsed_target_date,
                ),
                timeout=config.browser.overall_run_timeout_seconds,
            )
        )
    except ValueError as exc:
        _fail(
            "CONFIG_INVALID",
            str(exc),
            ExitCode.CONFIG_INVALID,
            "Review the command arguments and configured schedule IDs.",
        )
    except TimeoutError:
        recent = repository.list_occurrences(limit=1)
        occurrence_id = recent[0].id if recent else None
        if recent:
            timed_out_status = (
                AttemptStatus.MANUAL_REVIEW_REQUIRED
                if recent[0].status is AttemptStatus.SUBMITTING
                else AttemptStatus.NETWORK_ERROR
            )
            repository.update_status(
                recent[0].id,
                timed_out_status,
                error_code="RUN_TIMEOUT",
                error_summary="The overall run timeout interrupted this occurrence.",
            )
        _fail(
            "RUN_TIMEOUT",
            "The overall booking run exceeded its configured timeout.",
            (
                ExitCode.UNKNOWN_RESULT
                if recent and recent[0].status is AttemptStatus.SUBMITTING
                else ExitCode.NETWORK_ERROR
            ),
            (
                "Check LibCal manually; do not resubmit automatically."
                if recent and recent[0].status is AttemptStatus.SUBMITTING
                else "Check the network and LibCal status before retrying."
            ),
            occurrence_id,
        )
    except BookerError as exc:
        _booker_fail(exc)
    exit_code = ExitCode.OK
    for result in results:
        identity = f" occurrence_id={result.occurrence_id}" if result.occurrence_id else ""
        room = f" room={result.room}" if result.room else ""
        if result.exit_code is ExitCode.OK:
            typer.echo(f"{result.status.value}: {result.message}{room}{identity}")
        else:
            typer.echo(f"ERROR {result.status.value}: {result.message}{room}{identity}", err=True)
            typer.echo(f"NEXT {_next_action_for(result.exit_code)}", err=True)
        if result.exit_code > exit_code:
            exit_code = result.exit_code
    if exit_code is not ExitCode.OK:
        raise typer.Exit(int(exit_code))


@app.command("history")
def history_command(
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 50,
) -> None:
    """Show sanitized local occurrence history."""
    repository = ReservationRepository(connect(database_path()))
    rows = repository.list_occurrences(limit=limit)
    if not rows:
        typer.echo("No reservation attempts recorded.")
        return
    for item in rows:
        typer.echo(
            f"{item.target_date.isoformat()} {item.start_time}-{item.end_time} "
            f"{item.schedule_id} {item.status.value} room={item.chosen_room or '-'} "
            f"attempts={item.attempt_count} id={item.id}"
        )


@app.command("ui")
def ui_command(
    ctx: typer.Context,
    port: Annotated[int, typer.Option("--port", min=1024, max=65535)] = DEFAULT_PORT,
    host: Annotated[str, typer.Option("--host", help="Loopback address to bind.")] = DEFAULT_HOST,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the dashboard in the default browser.")
    ] = True,
) -> None:
    """Serve the local status, history, and schedule-settings dashboard."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        _fail(
            "CONFIG_INVALID",
            "The dashboard may only bind a loopback address.",
            ExitCode.CONFIG_INVALID,
            "Use --host 127.0.0.1.",
        )
    state = _state(ctx)
    try:
        server = create_server(state.config_path, host=host, port=port)
    except OSError as exc:
        _fail(
            "UI_PORT_UNAVAILABLE",
            f"Could not bind {host}:{port}: {exc}",
            ExitCode.NETWORK_ERROR,
            "Choose another port with --port.",
        )
    url = f"http://{host}:{port}/"
    typer.echo(f"Hayden Booker dashboard: {url}")
    typer.echo(
        "Schedule edits affect future runs; the dashboard never submits a booking directly. "
        "Press Ctrl+C to stop."
    )
    if open_browser:
        webbrowser.open(url)
    try:
        with server:
            server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("Dashboard stopped.")


@app.command("acknowledge")
def acknowledge_command(
    occurrence_id: Annotated[str, typer.Argument(help="Occurrence ID shown by `history`.")],
) -> None:
    """Record that an occurrence was reviewed manually so the dashboard stops flagging it."""
    repository = ReservationRepository(connect(database_path()))
    try:
        occurrence = repository.acknowledge(occurrence_id)
    except KeyError:
        _fail(
            "UNKNOWN_OCCURRENCE",
            f"No occurrence has ID {occurrence_id}.",
            ExitCode.CONFIG_INVALID,
            "Run `hayden-booker history` to list occurrence IDs.",
        )
    typer.echo(
        f"Acknowledged {occurrence.target_date.isoformat()} "
        f"{occurrence.start_time}-{occurrence.end_time} ({occurrence.status.value})."
    )
    typer.echo("The status is unchanged, so automatic resubmission stays blocked.")


@app.command("doctor")
def doctor_command(ctx: typer.Context) -> None:
    """Check runtime, Chromium, configuration, lock, secret, and authentication."""
    failures: list[tuple[ExitCode, str]] = []
    typer.echo(f"Python: {sys.version.split()[0]} (ok)")
    config, warnings = _load_or_exit(ctx)
    typer.echo(f"Configuration: valid ({len(warnings)} warning(s))")
    try:
        browser_ok = asyncio.run(_browser_installed())
    except Exception:
        browser_ok = False
    typer.echo(f"Playwright Chromium: {'available' if browser_ok else 'missing'}")
    if not browser_ok:
        failures.append((ExitCode.NETWORK_ERROR, "Run `playwright install chromium`."))
    lock = FileLock(str(profile_lock_path()))
    try:
        lock.acquire(timeout=0)
        lock.release()
        typer.echo("Profile lock: available")
    except FileLockTimeout:
        typer.echo("Profile lock: held by another process")
        failures.append((ExitCode.LOCKED, "Wait for the current Hayden Booker run to finish."))
    try:
        secret_exists = school_id_exists()
    except SecretStoreError as exc:
        secret_exists = False
        typer.echo(f"Credential store: unavailable ({exc})")
    else:
        typer.echo(f"School-ID secret: {'present' if secret_exists else 'missing'}")
    if config.live_booking_enabled and not secret_exists:
        failures.append((ExitCode.SECRET_MISSING, "Run `hayden-booker secret set-school-id`."))
    if config.calendar.enabled:
        try:
            calendar_connected = google_calendar_credentials_exist()
        except SecretStoreError as exc:
            calendar_connected = False
            typer.echo(f"Google Calendar authorization: unavailable ({exc})")
        else:
            state_label = "connected" if calendar_connected else "missing"
            typer.echo(f"Google Calendar authorization: {state_label}")
        if not calendar_connected:
            failures.append(
                (
                    ExitCode.AUTH_REQUIRED,
                    "Run `hayden-booker calendar connect --credentials CLIENT_SECRET.json`.",
                )
            )
    auth_state_exists = browser_auth_state_exists()
    typer.echo(f"Saved browser session: {'present' if auth_state_exists else 'missing'}")
    if not auth_state_exists:
        failures.append((ExitCode.AUTH_REQUIRED, "Run `hayden-booker auth setup`."))
    if browser_ok:
        try:
            state = asyncio.run(_auth_check(config))
            typer.echo(f"Authentication: {state.value}")
            if state is AuthState.AUTH_REQUIRED:
                failures.append((ExitCode.AUTH_REQUIRED, "Run `hayden-booker auth setup`."))
        except BookerError as exc:
            failures.append((exc.exit_code, exc.next_action))
    if failures:
        for _, remedy in failures:
            typer.echo(f"REMEDIATION {remedy}", err=True)
        raise typer.Exit(int(max(code for code, _ in failures)))
    typer.echo("Doctor checks passed; no booking was created.")


async def _manual_auth_setup(config: AppConfig) -> bool:
    async with BrowserSession(config, headless=config.browser.bootstrap_headless) as session:
        page = await session.get_page()
        await navigate_to_availability(page, config)
        available = page.get_by_role("checkbox").and_(page.locator(":enabled"))
        if await available.count() == 0:
            raise BookerError(
                "NO_AUTH_BOOTSTRAP_SLOT",
                "No selectable slot is available to reach LibCal authentication.",
                ExitCode.NO_AVAILABILITY,
                "Retry auth setup when at least one public slot is available.",
            )
        await available.first.check()
        submit_times = page.get_by_role("button", name=SUBMIT_TIMES)
        if await submit_times.count() == 0:
            raise BookerError(
                "SITE_CHANGED",
                "The Submit Times button was not found during authentication setup.",
                ExitCode.SITE_CHANGED,
                "Run `hayden-booker doctor` and inspect diagnostics.",
            )
        await submit_times.first.click()
        await asyncio.to_thread(
            input,
            "After ASU returns to LibCal booking details, press Enter here to save the profile: ",
        )
        if session.context is None:
            return False
        authenticated = False
        # Some ASU/CAS paths return in a new tab, so inspect every page in the context.
        for candidate in reversed(session.context.pages):
            if candidate.is_closed():
                continue
            details_form = candidate.locator("#s-lc-eq-form")
            inline_details_visible = (
                await details_form.count() > 0 and await details_form.is_visible()
            )
            final_submit = candidate.get_by_role("button", name=FINAL_SUBMIT)
            full_page_details_visible = (
                await final_submit.count() > 0 and await final_submit.first.is_visible()
            )
            if (inline_details_visible or full_page_details_visible) and (
                await classify_auth_state(candidate) is AuthState.AUTHENTICATED
            ):
                authenticated = True
                break
        if not authenticated:
            return False
        await save_browser_auth_state(session.context)
        return True


async def _auth_check(config: AppConfig) -> AuthState:
    async with BrowserSession(config, headless=config.browser.scheduled_headless) as session:
        page = await session.get_page()
        return await navigate_and_check_auth(page, config)


async def _browser_installed() -> bool:
    playwright = await async_playwright().start()
    try:
        return Path(playwright.chromium.executable_path).exists()
    finally:
        await playwright.stop()


def _load(ctx: typer.Context) -> tuple[AppConfig, list[str]]:
    return load_config(_state(ctx).config_path)


def _load_or_exit(ctx: typer.Context) -> tuple[AppConfig, list[str]]:
    try:
        return _load(ctx)
    except ValueError as exc:
        _fail(
            "CONFIG_INVALID",
            str(exc),
            ExitCode.CONFIG_INVALID,
            "Correct config.yaml and run `hayden-booker config validate`.",
        )


def _state(ctx: typer.Context) -> CliState:
    state = ctx.find_root().obj
    if not isinstance(state, CliState):
        raise RuntimeError("CLI state is unavailable")
    return state


def _ensure_gitignore(path: Path) -> None:
    required = (
        "config.yaml",
        "*.sqlite3",
        "browser-profile/",
        "auth/",
        "logs/",
        "diagnostics/",
        ".env",
    )
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = [entry for entry in required if entry not in existing.splitlines()]
    if missing:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        path.write_text(existing + separator + "\n".join(missing) + "\n", encoding="utf-8")


def _next_action_for(exit_code: ExitCode) -> str:
    return {
        ExitCode.NO_AVAILABILITY: "Try the site manually or wait for the next occurrence.",
        ExitCode.AUTH_REQUIRED: "Run `hayden-booker auth setup`.",
        ExitCode.SECRET_MISSING: "Run `hayden-booker secret set-school-id`.",
        ExitCode.CONFIG_INVALID: "Correct config.yaml or the command arguments.",
        ExitCode.SITE_CHANGED: "Run `hayden-booker doctor` and inspect diagnostics.",
        ExitCode.NETWORK_ERROR: "Check the network connection before retrying.",
        ExitCode.RATE_LIMITED: "Wait; do not retry automatically.",
        ExitCode.VALIDATION_FAILED: "Review the LibCal form manually.",
        ExitCode.CONFLICT: "Try another room manually or wait for the next occurrence.",
        ExitCode.UNKNOWN_RESULT: "Check LibCal manually; do not resubmit automatically.",
        ExitCode.LOCKED: "Wait for the other Hayden Booker process to finish.",
    }.get(exit_code, "Review the sanitized local history before retrying.")


def _booker_fail(error: BookerError) -> None:
    _fail(error.code, error.message, error.exit_code, error.next_action, error.occurrence_id)


def _fail(
    code: str,
    message: str,
    exit_code: ExitCode,
    next_action: str,
    occurrence_id: str | None = None,
) -> NoReturn:
    suffix = f" occurrence_id={occurrence_id}" if occurrence_id else ""
    typer.echo(f"ERROR {code}: {message}{suffix}", err=True)
    typer.echo(f"NEXT {next_action}", err=True)
    raise typer.Exit(int(exit_code))


if __name__ == "__main__":
    app()
