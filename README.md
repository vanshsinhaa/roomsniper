# Hayden Room Booker

Hayden Room Booker is a local, single-user Python CLI that matches recurring weekly room
preferences against ASU Hayden Library's accessible LibCal interface. It reuses a dedicated
Playwright browser profile, stores only sanitized booking history in SQLite, and defaults to a
dry run. It never accepts or stores an ASURITE password or Duo code and never attempts to bypass
CAPTCHA, rate limits, or any other access control.

The product contract and safety rationale are in
[`Hayden_Room_Booker_PRD.md`](Hayden_Room_Booker_PRD.md).

## Install

Python 3.12 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
playwright install chromium
hayden-booker init
```

On macOS/Linux, activate with `source .venv/bin/activate`. `init` creates `config.yaml`, the
local data directories, and the SQLite schema; it never creates credentials. Review the sample
schedule and leave `live_booking_enabled: false` until a dry run succeeds.

## First run

```powershell
hayden-booker config validate
hayden-booker schedule show
hayden-booker secret set-school-id
hayden-booker auth setup
hayden-booker auth check
hayden-booker run --dry-run
```

`auth setup` opens a dedicated browser profile and automatically selects a temporary public slot
to reach ASU sign-in. Complete ASU and Duo in that browser. When the **Booking Details** page
appears, leave the ASU ID field empty, do not click **Submit my Booking**, and press Enter in the
terminal. The CLI securely saves the browser session under `~/.hayden-booker/auth` and closes the
unsubmitted page. It never asks for or stores an ASURITE password or Duo code.
`auth check` follows only a non-submitting public path; the definitive check happens after
`Submit Times` during a gated live run, where an authentication redirect stops immediately.

Dry run is the default, so `hayden-booker run` and `hayden-booker run --dry-run` are equivalent.
A dry run may select checkboxes in its temporary page state, but it does not click `Submit Times`.

## Live safety gate

Live submission requires all of the following:

1. A successful configuration validation and dry run.
2. A school ID stored by `hayden-booker secret set-school-id` in the OS credential store.
3. A manually authenticated dedicated browser profile.
4. `live_booking_enabled: true` in `config.yaml`.
5. An explicit `--live` command.

```powershell
hayden-booker run --schedule-id monday-afternoon --target-date 2026-08-24 --live
hayden-booker run --due --live
```

SQLite commits the unique occurrence to `SUBMITTING` before the first booking action. Confirmed,
in-flight, unknown-result, and stale-submission occurrences cannot be resubmitted automatically.
Explicit room conflicts are the only post-submit outcome eligible for a bounded retry.

## Automatic Google / Notion Calendar events

The optional Google Calendar integration creates an event immediately after LibCal confirms a
room. Notion Calendar displays events from connected Google accounts, so the event appears there
without a separate Notion integration or the manual **Add to Google Calendar** confirmation step.
Calendar delivery is deliberately downstream of booking confirmation: an expired Google token or
Calendar API outage cannot change a confirmed room into a failed booking. Upcoming unsynced events
are retried on later live runs, using a deterministic Google event ID to prevent duplicates.

One-time setup:

1. In Google Cloud, enable the
   [Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
   for a project.
2. Configure its OAuth consent screen, then create an OAuth client with application type
   **Desktop app** and download the client JSON. Keep that file outside this repository.
3. Authorize Hayden Booker in your browser:

   ```powershell
   hayden-booker calendar connect --credentials C:\path\to\client_secret.json
   ```

4. Enable delivery in `config.yaml`:

   ```yaml
   calendar:
     enabled: true
     calendar_id: "primary"
     request_timeout_seconds: 20
   ```

5. Confirm the local state with `hayden-booker calendar status` and `hayden-booker doctor`.

`primary` is the signed-in account's main calendar. To use another calendar you own, put its
Google Calendar ID in `calendar_id`. The OAuth refresh token is stored only in the operating-system
credential store; neither the token nor the OAuth client file is written to SQLite or logs. If an
automatic add fails, the booking detail shows the reason and retains the manual Google/`.ics`
fallbacks. Re-run `calendar connect` if Google authorization is revoked or expires.

## Dashboard

```powershell
hayden-booker ui
```

`ui` serves a local dashboard on `http://127.0.0.1:8787` (loopback only, `--port` to change,
`--no-open` to skip launching a browser). It shows whether the system is active — configuration,
school-ID secret, ASU sign-in, scheduled task, database, recent run activity, and profile lock —
plus the booking history with a detail card per occurrence, its attempt timeline, automatic
Google Calendar delivery state, and manual **Add to Google Calendar** / `.ics` fallbacks.

The **Booking schedules** workspace edits schedule names, active/paused state, weekday, start/end
time, preferred room, and the master Live/Dry-run setting. **Save changes** validates the entire
configuration and atomically replaces `config.yaml`; the next scheduled run sees the new values.
Saving never starts a run or submits a booking immediately. Sign-in health is judged by what the
last real run observed, because the ASU/Duo session rides on session cookies that carry no expiry.
The dashboard never reads any credential value. Its only outbound request is the Google Fonts
stylesheet for Google Sans; offline it falls back to the local system font.

Keep it running across logons with an opt-in scheduled task, after reviewing the command:

```powershell
.\scripts\install_windows_ui_task.ps1 -Port 8787
```

It launches the dashboard through `pythonw.exe`, so no console window stays open, restarts it
if it exits, and lifts the default task execution time limit. Remove it with
`.\scripts\remove_windows_task.ps1 -TaskName "Hayden Room Booker UI"`. The booking runs stay on
their own daily task; the dashboard task serves the views and validated schedule editor only.

## Operations

```powershell
hayden-booker observe-release
hayden-booker history
hayden-booker doctor
```

Runtime data defaults to `~/.hayden-booker`. Set `HAYDEN_BOOKER_DATA_DIR` to relocate it. Logs are
rotated daily and retained for 14 files. Screenshots are off by default, stay local, and may
contain personal information. URLs are recorded without query strings. The database and logs
never contain the school ID, Google OAuth token, passwords, Duo data, cookies, or local-storage
contents.

Install the Windows scheduled task only after manually reviewing the command:

```powershell
.\scripts\install_windows_task.ps1 -ReleaseTime "00:00"
```

Use `scripts/remove_windows_task.ps1` to remove it. Equivalent opt-in installers are provided for
a macOS launch agent and a Linux systemd user timer. All default schedules use Arizona time;
systems outside Arizona should use the documented OS-specific timezone setting in the scripts.

## Verification

```powershell
ruff format --check .
ruff check .
mypy src
pytest
```

Fixture browser tests use local sanitized HTML and never submit to ASU. Tests marked `live` are
excluded by default. The current field label, confirmation text, exact release time, session
lifetime, headless behavior, and ASU policy approval still require the first user-assisted run,
as identified by the PRD. Site-specific label patterns are isolated in `libcal` configuration and
`browser/selectors.py` so those findings do not require an architectural change.
