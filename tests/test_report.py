"""Tests for Markdown report generation and get_scan_by_id."""

from __future__ import annotations

from datetime import datetime

import pytest

from tokenleak.report.markdown import generate, write_report


# ── helpers ───────────────────────────────────────────────────────────────────

def _populate(db):
    """Insert one repo, one scan with two alerts and a note. Returns (repo_id, scan_id)."""
    repo_id = db.upsert_repo("https://github.com/org/repo", "github", "repo")
    scan_id = db.create_scan(
        repo_id=repo_id,
        commit_sha="abc123def456789",
        commit_message="add secrets",
        commit_author="dev@example.com",
        commit_date=datetime(2026, 5, 1, 10, 0, 0),
        scan_mode="diff",
    )
    db.start_scan(scan_id)
    db.save_alert(
        scan_id=scan_id,
        file_path=".env",
        line_start=1,
        line_end=1,
        alert_type="secret",
        severity="critical",
        agent_json={
            "description": "AWS key found",
            "code_snippet": "AKIA...",
            "how_used": "used in S3 calls",
            "confirmation": "valid prefix",
        },
        repo_id=repo_id,
        commit_sha="abc123def456789",
        commit_date=datetime(2026, 5, 1, 10, 0, 0),
        triggered_by="scan",
    )
    db.save_alert(
        scan_id=scan_id,
        file_path="config.py",
        line_start=42,
        line_end=42,
        alert_type="password",
        severity="high",
        agent_json={"description": "Hardcoded password"},
        repo_id=repo_id,
        commit_sha="abc123def456789",
        commit_date=datetime(2026, 5, 1, 10, 0, 0),
        triggered_by="scan",
    )
    db.save_note(scan_id, "Agent note: .env file is high risk.")
    db.finish_scan(scan_id, "done")
    return repo_id, scan_id


# ── get_scan_by_id ────────────────────────────────────────────────────────────

def test_get_scan_by_id_returns_correct_scan(db):
    repo_id = db.upsert_repo("https://github.com/org/r", "github", "r")
    scan_id = db.create_scan(repo_id, "sha1", "msg", "author", None, scan_mode="full")
    scan = db.get_scan_by_id(scan_id)
    assert scan is not None
    assert scan.id == scan_id
    assert scan.commit_sha == "sha1"
    assert scan.scan_mode == "full"


def test_get_scan_by_id_returns_none_for_missing(db):
    assert db.get_scan_by_id(99999) is None


def test_get_scan_by_id_vs_list_scans_consistency(db):
    repo_id = db.upsert_repo("https://github.com/org/r2", "github", "r2")
    scan_id = db.create_scan(repo_id, "sha2", "msg2", "author2", None)
    by_id = db.get_scan_by_id(scan_id)
    by_list = next(s for s in db.list_scans() if s.id == scan_id)
    assert by_id.commit_sha == by_list.commit_sha
    assert by_id.scan_mode == by_list.scan_mode


# ── generate() ────────────────────────────────────────────────────────────────

def test_generate_contains_repo_url(db):
    _, scan_id = _populate(db)
    md = generate(db, scan_id, "https://github.com/org/repo")
    assert "https://github.com/org/repo" in md


def test_generate_contains_scan_id(db):
    _, scan_id = _populate(db)
    md = generate(db, scan_id, "https://github.com/org/repo")
    assert f"**Scan ID:** {scan_id}" in md


def test_generate_contains_commit_sha(db):
    _, scan_id = _populate(db)
    md = generate(db, scan_id, "https://github.com/org/repo")
    assert "abc123def456" in md


def test_generate_contains_scan_mode(db):
    _, scan_id = _populate(db)
    md = generate(db, scan_id, "https://github.com/org/repo")
    assert "**Scan mode:** diff" in md


def test_generate_summary_counts_severities(db):
    _, scan_id = _populate(db)
    md = generate(db, scan_id, "https://github.com/org/repo")
    assert "CRITICAL" in md
    assert "HIGH" in md
    assert "2 alert(s)" in md


def test_generate_alert_details(db):
    _, scan_id = _populate(db)
    md = generate(db, scan_id, "https://github.com/org/repo")
    assert "`.env`" in md
    assert "AWS key found" in md
    assert "AKIA..." in md
    assert "used in S3 calls" in md
    assert "valid prefix" in md


def test_generate_alert_triggered_by(db):
    _, scan_id = _populate(db)
    md = generate(db, scan_id, "https://github.com/org/repo")
    assert "**Triggered by:** scan" in md


def test_generate_alert_commit_sha(db):
    _, scan_id = _populate(db)
    md = generate(db, scan_id, "https://github.com/org/repo")
    assert "abc123def456" in md


def test_generate_includes_notes(db):
    _, scan_id = _populate(db)
    md = generate(db, scan_id, "https://github.com/org/repo")
    assert "Agent note: .env file is high risk." in md
    assert "## Agent Notes" in md


def test_generate_no_alerts(db):
    repo_id = db.upsert_repo("https://github.com/org/empty", "github", "empty")
    scan_id = db.create_scan(repo_id, "deadbeef", "no secrets", "dev", None)
    db.start_scan(scan_id)
    db.finish_scan(scan_id, "done")
    md = generate(db, scan_id, "https://github.com/org/empty")
    assert "No alerts found" in md
    assert "## Alerts" not in md


def test_generate_missing_scan(db):
    """If scan_id doesn't exist, report should not crash."""
    md = generate(db, 99999, "https://github.com/org/gone")
    assert "scan record not found" in md


def test_generate_none_severity_safe(db):
    """Alerts with severity=None must not cause AttributeError."""
    repo_id = db.upsert_repo("https://github.com/org/ns", "github", "ns")
    scan_id = db.create_scan(repo_id, "cafe1234", "msg", "dev", None)
    db.start_scan(scan_id)
    db.save_alert(
        scan_id=scan_id,
        file_path=None,
        line_start=0,
        line_end=0,
        alert_type=None,
        severity=None,
        agent_json={},
    )
    db.finish_scan(scan_id, "done")
    md = generate(db, scan_id, "https://github.com/org/ns")
    assert "UNKNOWN" in md
    assert "unknown file" in md


def test_generate_uses_get_scan_by_id_not_list_scans(db, monkeypatch):
    """generate() must call get_scan_by_id(), not list_scans()."""
    _, scan_id = _populate(db)
    list_scans_called = []

    original = db.list_scans
    def spy(*args, **kwargs):
        list_scans_called.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "list_scans", spy)
    generate(db, scan_id, "https://github.com/org/repo")
    assert not list_scans_called, "generate() must not call list_scans()"


# ── write_report() ────────────────────────────────────────────────────────────

def test_write_report_to_file(tmp_path):
    path = tmp_path / "report.md"
    write_report("# Hello", str(path))
    assert path.read_text() == "# Hello\n"


def test_write_report_stdout(capsys):
    write_report("# Hello stdout", "-")
    captured = capsys.readouterr()
    assert "# Hello stdout" in captured.out


def test_write_report_none_is_noop(tmp_path):
    write_report("ignored", None)
    assert not list(tmp_path.iterdir())
