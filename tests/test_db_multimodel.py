"""Tests for multi-model scanning in a single database."""

from __future__ import annotations

import pytest

from tokenleak.db.base import ScanStatus

MODEL_A = "openai/gpt-oss-120b"
MODEL_B = "deepseek-ai/DeepSeek-V4-Pro"
COMMIT = "deadbeef" * 2  # 16-char SHA


# ── create_scan isolation ─────────────────────────────────────────────────────

def test_two_models_get_separate_scan_rows(db):
    repo_id = db.upsert_repo("https://github.com/org/repo", "github")
    id_a = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_A)
    id_b = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_B)
    assert id_a != id_b, "each model must get its own scan row"


def test_same_model_same_commit_returns_existing_row(db):
    repo_id = db.upsert_repo("https://github.com/org/repo", "github")
    id1 = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_A)
    id2 = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_A)
    assert id1 == id2, "create_scan must be idempotent for the same model"


def test_no_model_and_model_a_are_separate_rows(db):
    repo_id = db.upsert_repo("https://github.com/org/repo", "github")
    id_none = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model="")
    id_a = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_A)
    assert id_none != id_a


# ── list_scans filtering ──────────────────────────────────────────────────────

def test_list_scans_by_ai_model_isolates_results(db):
    repo_id = db.upsert_repo("https://github.com/org/repo", "github")
    db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_A)
    db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_B)

    scans_a = db.list_scans(repo_id=repo_id, ai_model=MODEL_A)
    scans_b = db.list_scans(repo_id=repo_id, ai_model=MODEL_B)
    scans_all = db.list_scans(repo_id=repo_id)

    assert len(scans_a) == 1
    assert len(scans_b) == 1
    assert len(scans_all) == 2
    assert scans_a[0].ai_model == MODEL_A
    assert scans_b[0].ai_model == MODEL_B


def _cli_done_shas(db, repo_id: int, ai_model: str) -> set:
    """Reproduce the done_shas logic from cli.py for test assertions."""
    return {
        s.commit_sha
        for s in db.list_scans(repo_id=repo_id)
        if s.status == ScanStatus.DONE
        and (s.ai_model == ai_model or s.ai_model is None)
    }


def test_done_shas_are_model_specific(db):
    """Model B must not skip commits done only by Model A."""
    repo_id = db.upsert_repo("https://github.com/org/repo", "github")
    sha1, sha2 = "aaaa" * 4, "bbbb" * 4

    for sha in (sha1, sha2):
        sid = db.create_scan(repo_id, sha, "msg", "author", None, ai_model=MODEL_A)
        db.start_scan(sid)
        db.finish_scan(sid, ScanStatus.DONE)

    assert _cli_done_shas(db, repo_id, MODEL_B) == set(), \
        "Model B must not inherit Model A's done_shas"
    assert _cli_done_shas(db, repo_id, MODEL_A) == {sha1, sha2}


def test_legacy_null_ai_model_scans_count_as_done(db):
    """Scans with ai_model=NULL (created before per-model tracking) must not be re-scanned.

    This is the backward-compatibility case: a user upgrades from an old version
    where ai_model was not stored, and their existing scans have ai_model=NULL.
    The cli.py done_shas logic must include those scans for any model so that
    already-scanned commits are not scanned again after the upgrade.
    """
    repo_id = db.upsert_repo("https://github.com/org/repo", "github")
    sha1, sha2 = "cccc" * 4, "dddd" * 4

    # Simulate old scans with ai_model=NULL (ai_model="" stored as NULL)
    for sha in (sha1, sha2):
        sid = db.create_scan(repo_id, sha, "msg", "author", None, ai_model="")
        db.start_scan(sid)
        db.finish_scan(sid, ScanStatus.DONE)

    # Both models should see these legacy commits as "already done"
    assert _cli_done_shas(db, repo_id, MODEL_A) == {sha1, sha2}, \
        "Model A must treat NULL-ai_model scans as done"
    assert _cli_done_shas(db, repo_id, MODEL_B) == {sha1, sha2}, \
        "Model B must treat NULL-ai_model scans as done"


# ── get_scan filtering ────────────────────────────────────────────────────────

def test_get_scan_returns_model_specific_row(db):
    repo_id = db.upsert_repo("https://github.com/org/repo", "github")
    id_a = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_A)
    id_b = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_B)

    scan_a = db.get_scan(repo_id, COMMIT, ai_model=MODEL_A)
    scan_b = db.get_scan(repo_id, COMMIT, ai_model=MODEL_B)

    assert scan_a is not None and scan_a.id == id_a
    assert scan_b is not None and scan_b.id == id_b


def test_get_scan_without_model_returns_most_recent(db):
    repo_id = db.upsert_repo("https://github.com/org/repo", "github")
    db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_A)
    id_b = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_B)

    scan = db.get_scan(repo_id, COMMIT)  # no ai_model filter
    assert scan is not None and scan.id == id_b  # most recent


# ── list_alerts_for_repo ──────────────────────────────────────────────────────

def _make_alert(db, scan_id, repo_id, commit_sha, ai_model, severity="high"):
    db.save_alert(
        scan_id=scan_id,
        file_path=".env",
        line_start=1,
        line_end=1,
        alert_type="hardcoded_secret",
        severity=severity,
        agent_json={"key": "value"},
        repo_id=repo_id,
        commit_sha=commit_sha,
        ai_model=ai_model,
    )


def test_list_alerts_for_repo_filters_by_model(db):
    repo_id = db.upsert_repo("https://github.com/org/repo", "github")
    id_a = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_A)
    id_b = db.create_scan(repo_id, COMMIT, "msg", "author", None, ai_model=MODEL_B)

    _make_alert(db, id_a, repo_id, COMMIT, MODEL_A)
    _make_alert(db, id_a, repo_id, COMMIT, MODEL_A)
    _make_alert(db, id_b, repo_id, COMMIT, MODEL_B)

    alerts_a = db.list_alerts_for_repo(repo_id, ai_model=MODEL_A)
    alerts_b = db.list_alerts_for_repo(repo_id, ai_model=MODEL_B)
    alerts_all = db.list_alerts_for_repo(repo_id)

    assert len(alerts_a) == 2
    assert len(alerts_b) == 1
    assert len(alerts_all) == 3
    assert all(a.ai_model == MODEL_A for a in alerts_a)
    assert all(a.ai_model == MODEL_B for a in alerts_b)


# ── existing DB migration ─────────────────────────────────────────────────────

def test_migration_preserves_existing_scans(tmp_path):
    """Opening an old-schema DB must preserve existing rows after constraint migration."""
    import sqlite3
    from tokenleak.config import Config
    from tokenleak.db.sqlite import SQLiteDB

    db_path = str(tmp_path / "old.db")

    # Simulate an old database with UNIQUE(repo_id, commit_sha)
    old_schema = """
    CREATE TABLE repos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL UNIQUE,
        provider TEXT NOT NULL,
        name TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id INTEGER NOT NULL REFERENCES repos(id),
        commit_sha TEXT NOT NULL,
        commit_message TEXT,
        commit_author TEXT,
        commit_date DATETIME,
        scan_started_at DATETIME,
        scan_finished_at DATETIME,
        status TEXT NOT NULL DEFAULT 'pending',
        scan_mode TEXT,
        ai_model TEXT,
        alert_count INTEGER DEFAULT 0,
        note_count INTEGER DEFAULT 0,
        tokens_used INTEGER DEFAULT 0,
        error_message TEXT,
        UNIQUE(repo_id, commit_sha)
    );
    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id INTEGER NOT NULL REFERENCES scans(id),
        file_path TEXT,
        line_start INTEGER DEFAULT 0,
        line_end INTEGER DEFAULT 0,
        alert_type TEXT,
        severity TEXT,
        agent_json TEXT,
        triggered_by TEXT,
        ai_model TEXT,
        is_false_positive INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id INTEGER NOT NULL REFERENCES scans(id),
        content TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """
    with sqlite3.connect(db_path) as cx:
        cx.executescript(old_schema)
        cx.execute("INSERT INTO repos (url, provider) VALUES ('https://github.com/x/y', 'github')")
        cx.execute(
            "INSERT INTO scans (repo_id, commit_sha, status, ai_model) VALUES (1, 'abc', 'done', 'gpt-4o')"
        )

    # Open with new SQLiteDB — migration must run and preserve the row
    cfg = Config()
    cfg.db_path = db_path
    upgraded = SQLiteDB(cfg)
    upgraded.connect()
    try:
        scans = upgraded.list_scans()
        assert len(scans) == 1
        assert scans[0].commit_sha == "abc"
        assert scans[0].ai_model == "gpt-4o"

        # Must now allow two models on the same commit
        upgraded.create_scan(1, "abc", "msg", "author", None, ai_model="new-model")
        assert len(upgraded.list_scans()) == 2
    finally:
        upgraded.close()
