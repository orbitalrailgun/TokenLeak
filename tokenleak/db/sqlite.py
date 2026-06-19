"""SQLite database implementation."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

UTC = timezone.utc
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
        self._migrate_scans()
        self._migrate_scans_constraint()
        self._migrate_alerts_constraint()
        self._conn.commit()

    def _migrate_alerts(self) -> None:
        """Add new columns to alerts table for databases created before this migration."""
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(alerts)")}
        migrations = [
            ("repo_id",      "INTEGER REFERENCES repos(id)"),
            ("commit_sha",   "TEXT"),
            ("commit_date",  "DATETIME"),
            ("triggered_by", "TEXT"),
            ("ai_model",     "TEXT"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing:
                self._conn.execute(f"ALTER TABLE alerts ADD COLUMN {col_name} {col_def}")

    def _migrate_scans(self) -> None:
        """Add new columns to scans table for databases created before this migration."""
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(scans)")}
        if "scan_mode" not in existing:
            self._conn.execute("ALTER TABLE scans ADD COLUMN scan_mode TEXT")
        if "ai_model" not in existing:
            self._conn.execute("ALTER TABLE scans ADD COLUMN ai_model TEXT")
        if "input_tokens" not in existing:
            self._conn.execute("ALTER TABLE scans ADD COLUMN input_tokens INTEGER DEFAULT 0")
        if "output_tokens" not in existing:
            self._conn.execute("ALTER TABLE scans ADD COLUMN output_tokens INTEGER DEFAULT 0")
        if "branch" not in existing:
            self._conn.execute("ALTER TABLE scans ADD COLUMN branch TEXT")

    def _migrate_alerts_constraint(self) -> None:
        """Add UNIQUE(scan_id, file_path, line_start, alert_type) to alerts table.

        SQLite cannot ALTER a UNIQUE constraint, so the table is recreated.
        Existing duplicates are dropped — first occurrence (lowest id) is kept.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='alerts'"
        ).fetchone()
        if not row:
            return
        sql_norm = re.sub(r"\s+", "", (row["sql"] or "")).lower()
        if "unique(scan_id,file_path,line_start,alert_type)" in sql_norm:
            return  # already has constraint

        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self._conn.execute("""
                CREATE TABLE alerts_new (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id           INTEGER NOT NULL REFERENCES scans(id),
                    repo_id           INTEGER REFERENCES repos(id),
                    commit_sha        TEXT,
                    commit_date       DATETIME,
                    file_path         TEXT,
                    line_start        INTEGER DEFAULT 0,
                    line_end          INTEGER DEFAULT 0,
                    alert_type        TEXT,
                    severity          TEXT,
                    agent_json        TEXT,
                    triggered_by      TEXT,
                    ai_model          TEXT,
                    is_false_positive INTEGER DEFAULT 0,
                    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(scan_id, file_path, line_start, alert_type)
                )
            """)
            # ORDER BY id so the first (lowest id) occurrence wins on conflict
            self._conn.execute(
                "INSERT OR IGNORE INTO alerts_new SELECT * FROM alerts ORDER BY id"
            )
            self._conn.execute("DROP TABLE alerts")
            self._conn.execute("ALTER TABLE alerts_new RENAME TO alerts")
            self._conn.commit()
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")

    def _migrate_scans_constraint(self) -> None:
        """Recreate scans table to broaden UNIQUE(repo_id, commit_sha) → UNIQUE(repo_id, commit_sha, ai_model).

        SQLite cannot ALTER a UNIQUE constraint, so the table must be recreated.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='scans'"
        ).fetchone()
        if not row:
            return
        sql_norm = re.sub(r"\s+", "", (row["sql"] or "")).lower()
        old_key = "unique(repo_id,commit_sha)"
        new_key = "unique(repo_id,commit_sha,ai_model)"
        if old_key not in sql_norm or new_key in sql_norm:
            return  # already migrated or unrecognised schema

        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self._conn.execute("""
                CREATE TABLE scans_new (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_id          INTEGER NOT NULL REFERENCES repos(id),
                    commit_sha       TEXT NOT NULL,
                    commit_message   TEXT,
                    commit_author    TEXT,
                    commit_date      DATETIME,
                    scan_started_at  DATETIME,
                    scan_finished_at DATETIME,
                    status           TEXT NOT NULL DEFAULT 'pending',
                    scan_mode        TEXT,
                    ai_model         TEXT,
                    alert_count      INTEGER DEFAULT 0,
                    note_count       INTEGER DEFAULT 0,
                    tokens_used      INTEGER DEFAULT 0,
                    error_message    TEXT,
                    UNIQUE(repo_id, commit_sha, ai_model)
                )
            """)
            self._conn.execute("INSERT INTO scans_new SELECT * FROM scans")
            self._conn.execute("DROP TABLE scans")
            self._conn.execute("ALTER TABLE scans_new RENAME TO scans")
            self._conn.commit()
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")

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

    def get_scan(self, repo_id: int, commit_sha: str,
                 ai_model: Optional[str] = None) -> Optional[ScanRow]:
        if ai_model is not None:
            row = self._cx().execute(
                "SELECT * FROM scans WHERE repo_id = ? AND commit_sha = ? AND ai_model IS ?",
                (repo_id, commit_sha, ai_model or None),
            ).fetchone()
        else:
            row = self._cx().execute(
                "SELECT * FROM scans WHERE repo_id = ? AND commit_sha = ? ORDER BY id DESC LIMIT 1",
                (repo_id, commit_sha),
            ).fetchone()
        return ScanRow(**row) if row else None

    def get_scan_by_id(self, scan_id: int) -> Optional[ScanRow]:
        row = self._cx().execute(
            "SELECT * FROM scans WHERE id = ?", (scan_id,)
        ).fetchone()
        return ScanRow(**row) if row else None

    def create_scan(self, repo_id: int, commit_sha: str, commit_message: str,
                    commit_author: str, commit_date: Optional[datetime],
                    scan_mode: str = "diff", ai_model: str = "",
                    branch: Optional[str] = None) -> int:
        cx = self._cx()
        model = ai_model or None
        cx.execute(
            """INSERT OR IGNORE INTO scans
               (repo_id, commit_sha, commit_message, commit_author, commit_date,
                status, scan_mode, ai_model, branch)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (repo_id, commit_sha, commit_message, commit_author,
             commit_date.isoformat() if commit_date else None,
             ScanStatus.PENDING, scan_mode, model, branch or None),
        )
        cx.commit()
        # INSERT OR IGNORE silently skips on UNIQUE(repo_id, commit_sha, ai_model) conflict;
        # SELECT with ai_model filter ensures we return the row for this model, not another.
        row = cx.execute(
            "SELECT id FROM scans WHERE repo_id = ? AND commit_sha = ? AND ai_model IS ?",
            (repo_id, commit_sha, model),
        ).fetchone()
        return row["id"]

    def start_scan(self, scan_id: int) -> None:
        cx = self._cx()
        cx.execute(
            "UPDATE scans SET status = ?, scan_started_at = ? WHERE id = ?",
            (ScanStatus.RUNNING, datetime.now(UTC).isoformat(), scan_id),
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
            (status, datetime.now(UTC).isoformat(), alert_count, note_count, error, scan_id),
        )
        cx.commit()

    def update_scan_tokens(self, scan_id: int, input_tokens: int, output_tokens: int) -> None:
        cx = self._cx()
        cx.execute(
            """UPDATE scans
               SET tokens_used   = tokens_used   + ?,
                   input_tokens  = input_tokens  + ?,
                   output_tokens = output_tokens + ?
               WHERE id = ?""",
            (input_tokens + output_tokens, input_tokens, output_tokens, scan_id),
        )
        cx.commit()

    def list_scans(self, repo_id: Optional[int] = None,
                   ai_model: Optional[str] = None) -> list[ScanRow]:
        where: list[str] = []
        params: list = []
        if repo_id:
            where.append("repo_id = ?")
            params.append(repo_id)
        if ai_model is not None:
            where.append("ai_model IS ?")
            params.append(ai_model or None)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self._cx().execute(
            f"SELECT * FROM scans {clause} ORDER BY id DESC", params
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
        triggered_by: Optional[str] = None,
        ai_model: Optional[str] = None,
    ) -> int:
        cx = self._cx()
        cur = cx.execute(
            """INSERT OR IGNORE INTO alerts
               (scan_id, repo_id, commit_sha, commit_date,
                file_path, line_start, line_end, alert_type, severity,
                agent_json, triggered_by, ai_model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scan_id, repo_id, commit_sha,
                commit_date.isoformat() if commit_date else None,
                file_path, line_start, line_end, alert_type, severity,
                json.dumps(agent_json, ensure_ascii=False),
                triggered_by, ai_model or None,
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

    def list_alerts_for_repo(self, repo_id: int,
                             ai_model: Optional[str] = None) -> list[AlertRow]:
        if ai_model is not None:
            rows = self._cx().execute(
                "SELECT * FROM alerts WHERE repo_id = ? AND ai_model IS ? ORDER BY id",
                (repo_id, ai_model or None),
            ).fetchall()
        else:
            rows = self._cx().execute(
                "SELECT * FROM alerts WHERE repo_id = ? ORDER BY id", (repo_id,)
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
