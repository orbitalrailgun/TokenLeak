"""SQLite database implementation."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from tokenleak.config import Config
from tokenleak.db.base import (
    AlertRow, Database, RepoRow, SCHEMA_SQLITE, ScanRow, ScanStatus,
)


def _row_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return {col[0]: val for col, val in zip(cursor.description, row)}


class SQLiteDB(Database):

    def __init__(self, config: Config) -> None:
        self._path = config.db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = _row_factory
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA_SQLITE)
        self._migrate_alerts()
        self._conn.commit()

    def _migrate_alerts(self) -> None:
        """Add new columns to alerts table for databases created before this migration."""
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(alerts)")}
        migrations = [
            ("repo_id",     "INTEGER REFERENCES repos(id)"),
            ("commit_sha",  "TEXT"),
            ("commit_date", "DATETIME"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing:
                self._conn.execute(f"ALTER TABLE alerts ADD COLUMN {col_name} {col_def}")

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    def _cx(self) -> sqlite3.Connection:
        if not self._conn:
            raise RuntimeError("DB not connected")
        return self._conn

    # ── Repos ──────────────────────────────────────────────────────────────────

    def upsert_repo(self, url: str, provider: str, name: Optional[str] = None) -> int:
        cx = self._cx()
        cx.execute(
            "INSERT OR IGNORE INTO repos (url, provider, name) VALUES (?, ?, ?)",
            (url, provider, name),
        )
        cx.commit()
        row = cx.execute("SELECT id FROM repos WHERE url = ?", (url,)).fetchone()
        return row["id"]

    def list_repos(self) -> list[RepoRow]:
        rows = self._cx().execute("SELECT * FROM repos ORDER BY id").fetchall()
        return [RepoRow(**r) for r in rows]

    # ── Scans ──────────────────────────────────────────────────────────────────

    def get_scan(self, repo_id: int, commit_sha: str) -> Optional[ScanRow]:
        row = self._cx().execute(
            "SELECT * FROM scans WHERE repo_id = ? AND commit_sha = ?",
            (repo_id, commit_sha),
        ).fetchone()
        return ScanRow(**row) if row else None

    def create_scan(self, repo_id: int, commit_sha: str, commit_message: str,
                    commit_author: str, commit_date: Optional[datetime]) -> int:
        cx = self._cx()
        cx.execute(
            """INSERT OR IGNORE INTO scans
               (repo_id, commit_sha, commit_message, commit_author, commit_date, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (repo_id, commit_sha, commit_message, commit_author,
             commit_date.isoformat() if commit_date else None,
             ScanStatus.PENDING),
        )
        cx.commit()
        # Always SELECT — INSERT OR IGNORE may silently skip on UNIQUE conflict,
        # and cursor.lastrowid is unreliable (returns stale connection rowid) in that case.
        row = cx.execute(
            "SELECT id FROM scans WHERE repo_id = ? AND commit_sha = ?",
            (repo_id, commit_sha),
        ).fetchone()
        return row["id"]

    def start_scan(self, scan_id: int) -> None:
        cx = self._cx()
        cx.execute(
            "UPDATE scans SET status = ?, scan_started_at = ? WHERE id = ?",
            (ScanStatus.RUNNING, datetime.utcnow().isoformat(), scan_id),
        )
        cx.commit()

    def finish_scan(self, scan_id: int, status: str, error: Optional[str] = None) -> None:
        cx = self._cx()
        alert_count = cx.execute(
            "SELECT COUNT(*) as c FROM alerts WHERE scan_id = ?", (scan_id,)
        ).fetchone()["c"]
        note_count = cx.execute(
            "SELECT COUNT(*) as c FROM notes WHERE scan_id = ?", (scan_id,)
        ).fetchone()["c"]
        cx.execute(
            """UPDATE scans
               SET status = ?, scan_finished_at = ?,
                   alert_count = ?, note_count = ?, error_message = ?
               WHERE id = ?""",
            (status, datetime.utcnow().isoformat(), alert_count, note_count, error, scan_id),
        )
        cx.commit()

    def update_scan_tokens(self, scan_id: int, tokens: int) -> None:
        cx = self._cx()
        cx.execute(
            "UPDATE scans SET tokens_used = tokens_used + ? WHERE id = ?",
            (tokens, scan_id),
        )
        cx.commit()

    def list_scans(self, repo_id: Optional[int] = None) -> list[ScanRow]:
        if repo_id:
            rows = self._cx().execute(
                "SELECT * FROM scans WHERE repo_id = ? ORDER BY id DESC", (repo_id,)
            ).fetchall()
        else:
            rows = self._cx().execute(
                "SELECT * FROM scans ORDER BY id DESC"
            ).fetchall()
        return [ScanRow(**r) for r in rows]

    # ── Alerts ─────────────────────────────────────────────────────────────────

    def save_alert(
        self,
        scan_id: int,
        file_path: str,
        line_start: int,
        line_end: int,
        alert_type: str,
        severity: str,
        agent_json: dict,
        repo_id: Optional[int] = None,
        commit_sha: Optional[str] = None,
        commit_date=None,
    ) -> int:
        cx = self._cx()
        cur = cx.execute(
            """INSERT INTO alerts
               (scan_id, repo_id, commit_sha, commit_date,
                file_path, line_start, line_end, alert_type, severity, agent_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scan_id, repo_id, commit_sha,
                commit_date.isoformat() if commit_date else None,
                file_path, line_start, line_end, alert_type, severity,
                json.dumps(agent_json, ensure_ascii=False),
            ),
        )
        cx.commit()
        return cur.lastrowid

    def list_alerts(self, scan_id: int) -> list[AlertRow]:
        rows = self._cx().execute(
            "SELECT * FROM alerts WHERE scan_id = ? ORDER BY id", (scan_id,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["agent_json"] = json.loads(d["agent_json"] or "{}")
            d["is_false_positive"] = bool(d["is_false_positive"])
            result.append(AlertRow(**d))
        return result

    # ── Notes ──────────────────────────────────────────────────────────────────

    def save_note(self, scan_id: int, content: str) -> int:
        cx = self._cx()
        cur = cx.execute(
            "INSERT INTO notes (scan_id, content) VALUES (?, ?)", (scan_id, content)
        )
        cx.commit()
        return cur.lastrowid

    def list_notes(self, scan_id: int) -> list[str]:
        rows = self._cx().execute(
            "SELECT content FROM notes WHERE scan_id = ? ORDER BY id", (scan_id,)
        ).fetchall()
        return [r["content"] for r in rows]

    # ── Summary ────────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        cx = self._cx()
        repos = cx.execute("SELECT COUNT(*) as c FROM repos").fetchone()["c"]
        scans = cx.execute("SELECT status, COUNT(*) as c FROM scans GROUP BY status").fetchall()
        alerts = cx.execute("SELECT COUNT(*) as c FROM alerts").fetchone()["c"]
        tokens = cx.execute("SELECT SUM(tokens_used) as s FROM scans").fetchone()["s"] or 0
        last = cx.execute(
            "SELECT scan_finished_at FROM scans WHERE status = 'done' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "repos": repos,
            "scans": {r["status"]: r["c"] for r in scans},
            "alerts": alerts,
            "tokens_used": tokens,
            "last_scan_finished": last["scan_finished_at"] if last else None,
        }
