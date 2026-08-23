from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from hayden_booker.persistence.migrations import migrate


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_directory(path.parent)
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA journal_mode = WAL")
    migrate(connection)
    _restrict_file(path)
    return connection


def _restrict_directory(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700)


def _restrict_file(path: Path) -> None:
    if os.name != "nt" and path.exists():
        path.chmod(0o600)
