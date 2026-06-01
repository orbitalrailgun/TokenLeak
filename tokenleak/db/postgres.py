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
                ("repo_id",      "INTEGER REFERENCES repos(id)"),
                ("commit_sha",   "TEXT"),
                ("commit_date",  "TIMESTAMPTZ"),
                ("triggered_by", "TEXT"),
                ("ai_model",     "TEXT"),
            ]:
                cur.execute(
                    f"ALTER TABLE alerts ADD COLUMN IF NOT EXISTS {col_name} {col_def}"
                )
            for col_name, col_def in [
                ("scan_mode", "TEXT"),
                ("ai_model",  "TEXT"),
            ]:
                cur.execute(
                    f"ALTER TABLE scans ADD COLUMN IF NOT EXISTS {col_name} {col_def}"
                )
            self._migrate_scans_constraint(cur)
        self._conn.commit()

    def _migrate_scans_constraint(self, cur) -> None:
        """Change UNIQUE(repo_id, commit_sha) → UNIQUE(repo_id, commit_sha, ai_model).

        Drops the old 2-column constraint (if present) and adds the 3-column one.
        """
        # Find unique constraint on scans that does NOT include ai_model
        cur.execute("""
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE t.relname = 'scans'
              AND n.nspname = current_schema()
              AND c.contype = 'u'
              AND NOT EXISTS (
                  SELECT 1 FROM unnest(c.conkey) AS k
                  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k
                  WHERE a.attname = 'ai_model'
              )
        """)
        old = cur.fetchone()
        if old:
            cur.execute(f'ALTER TABLE scans DROP CONSTRAINT "{old[0]}"')

        # Add new 3-column constraint if not present
        cur.execute("""
            SELECT 1 FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE t.relname = 'scans'
              AND n.nspname = current_schema()
              AND c.contype = 'u'
              AND EXISTS (
                  SELECT 1 FROM unnest(c.conkey) AS k
                  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k
                  WHERE a.attname = 'ai_model'
              )
        """)
        if not cur.fetchone():
            cur.execute(
                "ALTER TABLE scans ADD CONSTRAINT scans_repo_id_commit_sha_ai_model_key "
                "UNIQUE (repo_id, commit_sha, ai_model)"
            )

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

    def get_scan(self, repo_id: int, commit_sha: str,
                 ai_model: Optional[str] = None) -> Optional[ScanRow]:
        if ai_model is not None:
            row = self._fetchone(
                "SELECT * FROM scans WHERE repo_id = %s AND commit_sha = %s AND ai_model IS NOT DISTINCT FROM %s",
                (repo_id, commit_sha, ai_model or None),
            )
        else:
            row = self._fetchone(
                "SELECT * FROM scans WHERE repo_id = %s AND commit_sha = %s ORDER BY id DESC LIMIT 1",
                (repo_id, commit_sha),
            )
        return ScanRow(**row) if row else None

    def get_scan_by_id(self, scan_id: int) -> Optional[ScanRow]:
        row = self._fetchone("SELECT * FROM scans WHERE id = %s", (scan_id,))
        return ScanRow(**row) if row else None

    def create_scan(self, repo_id: int, commit_sha: str, commit_message: str,
                    commit_author: str, commit_date: Optional[datetime],
                    scan_mode: str = "diff", ai_model: str = "") -> int:
        model = ai_model or None
        row = self._fetchone(
            """INSERT INTO scans
               (repo_id, commit_sha, commit_message, commit_author, commit_date,
                status, scan_mode, ai_model)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (repo_id, commit_sha, ai_model) DO NOTHING
               RETURNING id""",
            (repo_id, commit_sha, commit_message, commit_author, commit_date,
             ScanStatus.PENDING, scan_mode, model),
        )
        self._conn.commit()
        if row:
            return row["id"]
        # INSERT was skipped (same model already scanned this commit); return existing ID.
        row = self._fetchone(
            "SELECT id FROM scans WHERE repo_id = %s AND commit_sha = %s AND ai_model IS NOT DISTINCT FROM %s",
            (repo_id, commit_sha, model),
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

    def list_scans(self, repo_id: Optional[int] = None,
                   ai_model: Optional[str] = None) -> list[ScanRow]:
        where: list[str] = []
        params: list = []
        if repo_id:
            where.append("repo_id = %s")
            params.append(repo_id)
        if ai_model is not None:
            where.append("ai_model IS NOT DISTINCT FROM %s")
            params.append(ai_model or None)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self._fetchall(
            f"SELECT * FROM scans {clause} ORDER BY id DESC", tuple(params)
        )
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
        row = self._fetchone(
            """INSERT INTO alerts
               (scan_id, repo_id, commit_sha, commit_date,
                file_path, line_start, line_end, alert_type, severity,
                agent_json, triggered_by, ai_model)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                scan_id, repo_id, commit_sha, commit_date,
                file_path, line_start, line_end, alert_type, severity,
                json.dumps(agent_json, ensure_ascii=False),
                triggered_by, ai_model or None,
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

    def list_alerts_for_repo(self, repo_id: int,
                             ai_model: Optional[str] = None) -> list[AlertRow]:
        if ai_model is not None:
            rows = self._fetchall(
                "SELECT * FROM alerts WHERE repo_id = %s AND ai_model IS NOT DISTINCT FROM %s ORDER BY id",
                (repo_id, ai_model or None),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM alerts WHERE repo_id = %s ORDER BY id", (repo_id,)
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
