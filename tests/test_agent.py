"""Tests for agent client and MCP server tool registry."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tokenleak.mcp_server import server as mcp_server


@pytest.fixture(autouse=True)
def reset_mcp_context(tmp_path, db):
    """Wire up MCP context with a real DB and temp repo path."""
    mcp_server.init_context(db=db, scan_id=1, repo_path=tmp_path)
    yield
    mcp_server.init_context(db=None, scan_id=None, repo_path=None)


class TestMCPToolRegistry:
    def test_all_tools_registered(self):
        expected = {
            "save_alert", "save_note", "get_notes", "read_file",
            "read_file_at_commit", "list_files", "search_content",
            "get_commit_log", "get_file_tree", "send_mattermost",
            "analyze_image_file",
        }
        assert expected == set(mcp_server.TOOLS.keys())

    def test_tool_schemas_match_registry(self):
        schema_names = {s["function"]["name"] for s in mcp_server.TOOL_SCHEMAS}
        registry_names = set(mcp_server.TOOLS.keys())
        assert schema_names == registry_names


class TestSaveAlert:
    def test_save_and_retrieve(self, db, tmp_path):
        repo_id = db.upsert_repo("https://example.com/repo.git", "generic")
        scan_id = db.create_scan(repo_id, "abc123", "test commit", "author@x.com", None)
        mcp_server.init_context(db=db, scan_id=scan_id, repo_path=tmp_path)

        result = mcp_server.save_alert(
            file_path="secrets/.env",
            alert_type="secret",
            severity="critical",
            description="AWS key found",
            code_snippet="AKIAIOSFODNN7EXAMPLE",
            line_start=3,
            line_end=3,
        )
        assert "saved" in result.lower()

        alerts = db.list_alerts(scan_id)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "secret"
        assert alerts[0].severity == "critical"
        assert alerts[0].file_path == "secrets/.env"


class TestSaveAndGetNotes:
    def test_round_trip(self, db, tmp_path):
        repo_id = db.upsert_repo("https://example.com/repo2.git", "generic")
        scan_id = db.create_scan(repo_id, "def456", "msg", "a@b.com", None)
        mcp_server.init_context(db=db, scan_id=scan_id, repo_path=tmp_path)

        mcp_server.save_note("This repo has many .env files.")
        mcp_server.save_note("Found AWS key in commit abc.")

        notes_text = mcp_server.get_notes()
        assert "AWS key" in notes_text
        assert ".env" in notes_text


class TestReadFile:
    def test_reads_existing_file(self, tmp_path, db):
        repo_id = db.upsert_repo("https://example.com/r3.git", "generic")
        scan_id = db.create_scan(repo_id, "ghi789", "msg", "a@b.com", None)
        (tmp_path / "secret.txt").write_text("password=hunter2")
        mcp_server.init_context(db=db, scan_id=scan_id, repo_path=tmp_path)

        content = mcp_server.read_file("secret.txt")
        assert "hunter2" in content

    def test_returns_not_found_for_missing(self, tmp_path, db):
        repo_id = db.upsert_repo("https://example.com/r4.git", "generic")
        scan_id = db.create_scan(repo_id, "jkl012", "msg", "a@b.com", None)
        mcp_server.init_context(db=db, scan_id=scan_id, repo_path=tmp_path)

        content = mcp_server.read_file("does_not_exist.txt")
        assert "not found" in content.lower()


class TestListFiles:
    def test_lists_files(self, tmp_path, db):
        repo_id = db.upsert_repo("https://example.com/r5.git", "generic")
        scan_id = db.create_scan(repo_id, "mno345", "msg", "a@b.com", None)
        (tmp_path / "a.py").write_text("x=1")
        (tmp_path / "b.txt").write_text("y=2")
        mcp_server.init_context(db=db, scan_id=scan_id, repo_path=tmp_path)

        listing = mcp_server.list_files()
        assert "a.py" in listing
        assert "b.txt" in listing

    def test_glob_pattern(self, tmp_path, db):
        repo_id = db.upsert_repo("https://example.com/r6.git", "generic")
        scan_id = db.create_scan(repo_id, "pqr678", "msg", "a@b.com", None)
        (tmp_path / "config.env").write_text("KEY=val")
        (tmp_path / "main.py").write_text("pass")
        mcp_server.init_context(db=db, scan_id=scan_id, repo_path=tmp_path)

        listing = mcp_server.list_files(pattern="*.env")
        assert "config.env" in listing
        assert "main.py" not in listing
