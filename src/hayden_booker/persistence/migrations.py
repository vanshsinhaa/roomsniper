from __future__ import annotations

import sqlite3

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS reservation_occurrences (
            id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            target_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            chosen_room TEXT,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            confirmed_at_utc TEXT,
            confirmation_reference TEXT,
            last_error_code TEXT,
            last_error_summary TEXT,
            UNIQUE(schedule_id, target_date, start_time, end_time)
        );

        CREATE TABLE IF NOT EXISTS attempt_events (
            id TEXT PRIMARY KEY,
            occurrence_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at_utc TEXT NOT NULL,
            room TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(occurrence_id) REFERENCES reservation_occurrences(id)
        );

        CREATE INDEX IF NOT EXISTS idx_attempt_events_occurrence
            ON attempt_events(occurrence_id, occurred_at_utc);

        CREATE TABLE IF NOT EXISTS release_observations (
            observed_at_utc TEXT NOT NULL,
            local_date TEXT NOT NULL,
            furthest_visible_date TEXT,
            target_visible INTEGER NOT NULL CHECK(target_visible IN (0, 1))
        );
        """,
    ),
    (
        2,
        """
        ALTER TABLE reservation_occurrences ADD COLUMN acknowledged_at_utc TEXT;
        """,
    ),
)


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL)"
    )
    applied = {
        int(row[0])
        for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        with connection:
            connection.executescript(sql)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at_utc) "
                "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                (version,),
            )
