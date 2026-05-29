"""PostgreSQL database implementation (requires psycopg2-binary)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from tokenleak.config import Config
from tokenleak.db.base import (
    AlertRow, Database, RepoRow, SCHEMA_POSTGRES, ScanRow, ScanStatus,
)


class PostgresDB(Database):

    def __init__(self, config: Config) -> None:
        self._config = config
        self._conn = None

    def connect(self) -> None:
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError:
            raise ImportError(
                "psycopg2-binary is required for PostgreSQL. "
                "Install with: pip install tokenleak[postgres]"
            )

        self._conn = psycopg2.connect(
            host=self._config.db_host,
            port=self._config.db_port,
            dbname=self._config.db_name,
            user=self._config.db_user,
            password=self._config.db_password,
            options="-c search_path=tokenleak,public",
        )
        self._conn.autocommit = False

        with self._conn.cursor() as cur:
            # Apply schema
            for statement in SCHEMA_POSTGRES.split(";"):
                stmt = statement.strip()
                if stmt:
                    cur.execute(stmt)
            # Migrate: add columns introduced after initial schema
            for col_name, col_def in [
                ("repo_id",     "INTEGER REFERENCES repos(id)"),
                ("commit_sha",  "TEXT"),
                ("commit_date", "TIMESTAMPTZ"),
            ]:
                cur.execute(
                    f"ALTER TABLE alerts ADD COLUMN IF NOT EXISTS {col_name} {col_def}"
                )
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    def _cx(self):
        if not self._conn:
            raise RuntimeError("DB not connected")
        return self._conn

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        import psycopg2.extras
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        import psycopg2.extras
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def _execute(self, sql: str, params: tuple = ()) -> Optional[int]:
        import psycopg2.extras
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            self._conn.commit()
            return cur.fetchone()[0] if cur.description else None

    # ── Repos ──────────────────────────────────────────────────────────────────

    def upsert_repo(self, url: str, provider: str, name: Optional[str] = None) -> int:
        row = self._fetchone(
            """INSERT INTO repos (url, provider, name)
               VALUES (%s, %s, %s)
               ON CONFLICT (url) DO UPDATE SET provider = EXCLUDED.provider
               RETURNING id""",
            (url, provider, name),
        )
        self._conn.commit()
        return row["id"]

    def list_repos(self) -> list[RepoRow]:
        rows = self._fetchall("SELECT * FROM repos ORDER BY id")
        return [RepoRow(**r) for r in rows]

    # ── Scans ──────────────────────────────────────────────────────────────────

    def get_scan(self, repo_id: int, commit_sha: str) -> Optional[ScanRow]:
        row = self._fetchone(
            "SELECT * FROM scans WHERE repo_id = %s AND commit_sha = %s",
            (repo_id, commit_sha),
        )
        return ScanRow(**row) if row else None

    def create_scan(self, repo_id: int, commit_sha: str, commit_message: str,
                    commit_author: str, commit_date: Optional[datetime]) -> int:
        row = self._fetchone(
            """INSERT INTO scans
               (repo_id, commit_sha, commit_message, commit_author, commit_date, status)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (repo_id, commit_sha) DO NOTHING
               RETURNING id""",
            (repo_id, commit_sha, commit_message, commit_author, commit_date, ScanStatus.PENDING),
        )
        self._conn.commit()
        if row:
            return row["id"]
        row = self._fetchone(
            "SELECT id FROM scans WHERE repo_id = %s AND commit_sha = %s",
            (repo_id, commit_sha),
        )
        return row["id"]

    def start_scan(self, scan_id: int) -> None:
        self._execute(
            "UPDATE scans SET status = %s, scan_started_at = NOW() WHERE id = %s",
            (ScanStatus.RUNNING, scan_id),
        )

    def finish_scan(self, scan_id: int, status: str, error: Optional[str] = None) -> None:
        alert_count = self._fetchone(
            "SELECT COUNT(*) as c FROM alerts WHERE scan_id = %s", (scan_id,)
        )["c"]
        note_count = self._fetchone(
            "SELECT COUNT(*) as c FROM notes WHERE scan_id = %s", (scan_id,)
        )["c"]
        self._execute(
            """UPDATE scans
               SET status = %s, scan_finished_at = NOW(),
                   alert_count = %s, note_count = %s, error_message = %s
               WHERE id = %s""",
            (status, alert_count, note_count, error, scan_id),
        )

    def update_scan_tokens(self, scan_id: int, tokens: int) -> None:
        self._execute(
            "UPDATE scans SET tokens_used = tokens_used + %s WHERE id = %s",
            (tokens, scan_id),
        )

    def list_scans(self, repo_id: Optional[int] = None) -> list[ScanRow]:
        if repo_id:
            rows = self._fetchall(
                "SELECT * FROM scans WHERE repo_id = %s ORDER BY id DESC", (repo_id,)
            )
        else:
            rows = self._fetchall("SELECT * FROM scans ORDER BY id DESC")
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
        row = self._fetchone(
            """INSERT INTO alerts
               (scan_id, repo_id, commit_sha, commit_date,
                file_path, line_start, line_end, alert_type, severity, agent_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                scan_id, repo_id, commit_sha, commit_date,
                file_path, line_start, line_end, alert_type, severity,
                json.dumps(agent_json, ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return row["id"]

    def list_alerts(self, scan_id: int) -> list[AlertRow]:
        rows = self._fetchall(
            "SELECT * FROM alerts WHERE scan_id = %s ORDER BY id", (scan_id,)
        )
        result = []
        for r in rows:
            if isinstance(r.get("agent_json"), str):
                r["agent_json"] = json.loads(r["agent_json"] or "{}")
            result.append(AlertRow(**r))
        return result

    # ── Notes ──────────────────────────────────────────────────────────────────

    def save_note(self, scan_id: int, content: str) -> int:
        row = self._fetchone(
            "INSERT INTO notes (scan_id, content) VALUES (%s, %s) RETURNING id",
            (scan_id, content),
        )
        self._conn.commit()
        return row["id"]

    def list_notes(self, scan_id: int) -> list[str]:
        rows = self._fetchall(
            "SELECT content FROM notes WHERE scan_id = %s ORDER BY id", (scan_id,)
        )
        return [r["content"] for r in rows]

    # ── Summary ────────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        repos = self._fetchone("SELECT COUNT(*) as c FROM repos")["c"]
        scans = self._fetchall("SELECT status, COUNT(*) as c FROM scans GROUP BY status")
        alerts = self._fetchone("SELECT COUNT(*) as c FROM alerts")["c"]
        tokens = self._fetchone("SELECT COALESCE(SUM(tokens_used), 0) as s FROM scans")["s"]
        last = self._fetchone(
            "SELECT scan_finished_at FROM scans WHERE status = 'done' ORDER BY id DESC LIMIT 1"
        )
        return {
            "repos": repos,
            "scans": {r["status"]: r["c"] for r in scans},
            "alerts": alerts,
            "tokens_used": int(tokens),
            "last_scan_finished": str(last["scan_finished_at"]) if last else None,
        }
