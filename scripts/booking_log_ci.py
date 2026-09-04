"""Ledger maintenance for the booking-log workflow.

Runs on GitHub Actions with nothing but the standard library: it imports the pure half of
`hayden_booker.booking_log` straight from `src/`, so CI never installs Playwright or pydantic.

    python scripts/booking_log_ci.py ingest --payload event.json
    python scripts/booking_log_ci.py render
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hayden_booker.booking_log import (  # noqa: E402
    LEDGER_PATH,
    MARKDOWN_PATH,
    commit_message,
    load_ledger,
    merge_records,
    render_markdown,
    sort_records,
    write_ledger,
)

DEFAULT_AUTHOR_NAME = "VanshSinha18"
DEFAULT_AUTHOR_EMAIL = "54222353+VanshSinha18@users.noreply.github.com"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ingest", "render"))
    parser.add_argument("--payload", type=Path, help="JSON file with the dispatched bookings.")
    parser.add_argument("--ledger", type=Path, default=ROOT / LEDGER_PATH)
    parser.add_argument("--markdown", type=Path, default=ROOT / MARKDOWN_PATH)
    parser.add_argument("--no-backdate", action="store_true")
    arguments = parser.parse_args(argv)

    if arguments.command == "render":
        return _render(arguments.ledger, arguments.markdown)
    if arguments.payload is None:
        parser.error("--payload is required for ingest")
    return _ingest(
        arguments.payload,
        arguments.ledger,
        arguments.markdown,
        backdate=not arguments.no_backdate,
    )


def _render(ledger: Path, markdown: Path) -> int:
    records = load_ledger(ledger)
    write_ledger(ledger, records)
    markdown.write_text(render_markdown(records), encoding="utf-8")
    if not _dirty(ledger, markdown):
        print("Table already matches the ledger; nothing to commit.")
        _emit_output("committed", "0")
        return 0
    _commit([ledger, markdown], f"docs(bookings): re-render table for {len(records)} booking(s)")
    _emit_output("committed", "1")
    return 0


def _ingest(payload: Path, ledger: Path, markdown: Path, *, backdate: bool) -> int:
    incoming = _read_payload(payload)
    if not incoming:
        print("Dispatch carried no usable booking records.")
        _emit_output("committed", "0")
        return 0
    records = load_ledger(ledger)
    known = {record["id"] for record in records}
    pending = [record for record in sort_records(incoming) if record["id"] not in known]
    if not pending:
        print(f"All {len(incoming)} dispatched booking(s) are already logged.")
        _emit_output("committed", "0")
        return 0
    for record in pending:
        records = merge_records(records, [record])
        write_ledger(ledger, records)
        markdown.write_text(render_markdown(records), encoding="utf-8")
        _commit(
            [ledger, markdown],
            commit_message(record),
            date=record.get("logged_at_utc") if backdate else None,
        )
        print(commit_message(record).splitlines()[0])
    _emit_output("committed", str(len(pending)))
    return 0


def _read_payload(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("bookings", [])
    if not isinstance(raw, list):
        return []
    return [record for record in raw if isinstance(record, dict) and _valid(record)]


def _valid(record: dict[str, Any]) -> bool:
    """Dispatch payloads are attacker-shaped input; require the fields the table reads."""
    required = ("id", "target_date", "start_time", "end_time", "status", "outcome")
    return all(isinstance(record.get(field), str) and record[field] for field in required)


def _dirty(*paths: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", *[str(path) for path in paths]],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return bool(result.stdout.strip())


def _commit(paths: list[Path], message: str, *, date: str | None = None) -> None:
    name = os.environ.get("BOOKING_AUTHOR_NAME") or DEFAULT_AUTHOR_NAME
    email = os.environ.get("BOOKING_AUTHOR_EMAIL") or DEFAULT_AUTHOR_EMAIL
    environment = dict(os.environ)
    environment["GIT_AUTHOR_NAME"] = name
    environment["GIT_AUTHOR_EMAIL"] = email
    environment["GIT_COMMITTER_NAME"] = name
    environment["GIT_COMMITTER_EMAIL"] = email
    if date:
        environment["GIT_AUTHOR_DATE"] = date
        environment["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "add", "--", *[str(path) for path in paths]], check=True, cwd=ROOT)
    subprocess.run(["git", "commit", "-m", message], check=True, cwd=ROOT, env=environment)


def _emit_output(key: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"{key}={value}\n")


if __name__ == "__main__":
    raise SystemExit(main())
