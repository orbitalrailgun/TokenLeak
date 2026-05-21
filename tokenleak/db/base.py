"""Abstract database interface and shared data models."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ── DDL shared between SQLite and PostgreSQL ───────────────────────────────────
# PostgreSQL uses SERIAL; SQLite uses AUTOINCREMENT — handled per-implementation.

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS repos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    url       TEXT NOT NULL UNIQUE,
    provider  TEXT NOT NULL,
    name      TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id         INTEGER NOT NULL REFERENCES repos(id),
    commit_sha      TEXT NOT NULL,
    commit_message  TEXT,
    commit_author   TEXT,
    commit_date     DATETIME,
    scan_started_at DATETIME,
    scan_finished_at DATETIME,
    status          TEXT NOT NULL DEFAULT 'pending',
    alert_count     INTEGER DEFAULT 0,
    note_count      INTEGER DEFAULT 0,
    tokens_used     INTEGER DEFAULT 0,
    error_message   TEXT,
    UNIQUE(repo_id, commit_sha)
);

CREATE TABLE IF NOT EXISTS alerts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id           INTEGER NOT NULL REFERENCES scans(id),
    file_path         TEXT,
    line_start        INTEGER DEFAULT 0,
    line_end          INTEGER DEFAULT 0,
    alert_type        TEXT,
    severity          TEXT,
    agent_json        TEXT,
    is_false_positive INTEGER DEFAULT 0,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id    INTEGER NOT NULL REFERENCES scans(id),
    content    TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS repos (
    id         SERIAL PRIMARY KEY,
    url        TEXT NOT NULL UNIQUE,
    provider   TEXT NOT NULL,
    name       TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scans (
    id               SERIAL PRIMARY KEY,
    repo_id          INTEGER NOT NULL REFERENCES repos(id),
    commit_sha       TEXT NOT NULL,
    commit_message   TEXT,
    commit_author    TEXT,
    commit_date      TIMESTAMPTZ,
    scan_started_at  TIMESTAMPTZ,
    scan_finished_at TIMESTAMPTZ,
    status           TEXT NOT NULL DEFAULT 'pending',
    alert_count      INTEGER DEFAULT 0,
    note_count       INTEGER DEFAULT 0,
    tokens_used      INTEGER DEFAULT 0,
    error_message    TEXT,
    UNIQUE(repo_id, commit_sha)
);

CREATE TABLE IF NOT EXISTS alerts (
    id                SERIAL PRIMARY KEY,
    scan_id           INTEGER NOT NULL REFERENCES scans(id),
    file_path         TEXT,
    line_start        INTEGER DEFAULT 0,
    line_end          INTEGER DEFAULT 0,
    alert_type        TEXT,
    severity          TEXT,
    agent_json        JSONB,
    is_false_positive BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notes (
    id         SERIAL PRIMARY KEY,
    scan_id    INTEGER NOT NULL REFERENCES scans(id),
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""


@dataclass
class ScanStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED_SCANNED = "skipped_already_scanned"
    SKIPPED_TOO_LARGE = "skipped_too_large"


@dataclass
class RepoRow:
    id: int
    url: str
    provider: str
    name: Optional[str]
    created_at: datetime


@dataclass
class ScanRow:
    id: int
    repo_id: int
    commit_sha: str
    status: str
    alert_count: int = 0
    note_count: int = 0
    tokens_used: int = 0
    commit_message: Optional[str] = None
    commit_author: Optional[str] = None
    scan_started_at: Optional[datetime] = None
    scan_finished_at: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class AlertRow:
    id: int
    scan_id: int
    file_path: Optional[str]
    line_start: int
    line_end: int
    alert_type: Optional[str]
    severity: Optional[str]
    agent_json: dict = field(default_factory=dict)
    is_false_positive: bool = False


class Database(ABC):

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    # ── Repos ──────────────────────────────────────────────────────────────────

    @abstractmethod
    def upsert_repo(self, url: str, provider: str, name: Optional[str] = None) -> int:
        """Insert repo or return existing id."""
        ...

    @abstractmethod
    def list_repos(self) -> list[RepoRow]: ...

    # ── Scans ──────────────────────────────────────────────────────────────────

    @abstractmethod
    def get_scan(self, repo_id: int, commit_sha: str) -> Optional[ScanRow]: ...

    @abstractmethod
    def create_scan(self, repo_id: int, commit_sha: str, commit_message: str,
                    commit_author: str, commit_date: Optional[datetime]) -> int: ...

    @abstractmethod
    def start_scan(self, scan_id: int) -> None: ...

    @abstractmethod
    def finish_scan(self, scan_id: int, status: str, error: Optional[str] = None) -> None: ...

    @abstractmethod
    def update_scan_tokens(self, scan_id: int, tokens: int) -> None: ...

    @abstractmethod
    def list_scans(self, repo_id: Optional[int] = None) -> list[ScanRow]: ...

    # ── Alerts ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def save_alert(self, scan_id: int, file_path: str, line_start: int, line_end: int,
                   alert_type: str, severity: str, agent_json: dict) -> int: ...

    @abstractmethod
    def list_alerts(self, scan_id: int) -> list[AlertRow]: ...

    # ── Notes ──────────────────────────────────────────────────────────────────

    @abstractmethod
    def save_note(self, scan_id: int, content: str) -> int: ...

    @abstractmethod
    def list_notes(self, scan_id: int) -> list[str]: ...

    # ── Summary ────────────────────────────────────────────────────────────────

    @abstractmethod
    def summary(self) -> dict: ...
