"""FastMCP server — tools available to the scanning agent.

The server is used in two ways:
  1. Embedded in the agent runner: tool functions are called directly from Python
     using the TOOLS registry (no MCP transport overhead).
  2. Standalone MCP server: run `python -m tokenleak mcp` to expose tools over
     stdio so external MCP clients (Claude Desktop, etc.) can connect.

State (db, current scan context, repo path) is injected before each agent run
via init_context().
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastmcp import FastMCP

from tokenleak.logging_setup import get_logger
from tokenleak.scanner.walker import (
    get_file_at_commit,
    get_file_tree as _walker_file_tree,
    get_commit_log_text as _walker_commit_log,
)

log = get_logger()
mcp = FastMCP("TokenLeak")

# ── Mutable context — set by agent.runner before each scan ────────────────────
_db = None
_scan_id: Optional[int] = None
_repo_path: Optional[Path] = None
_notifications = None   # notifications.mattermost module reference


def init_context(db, scan_id: int, repo_path: Path, notifications=None) -> None:
    global _db, _scan_id, _repo_path, _notifications
    _db = db
    _scan_id = scan_id
    _repo_path = repo_path
    _notifications = notifications


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def save_alert(
    file_path: str,
    alert_type: str,
    severity: str,
    description: str,
    code_snippet: str = "",
    how_used: str = "",
    confirmation: str = "",
    line_start: int = 0,
    line_end: int = 0,
) -> str:
    """Save a security alert found in the repository.

    Args:
        file_path: Path to the file (relative to repo root).
        alert_type: Category — secret | token | pii | corporate_secret | password | key.
        severity: critical | high | medium | low.
        description: What was found and why it is sensitive.
        code_snippet: The relevant code or value (redact actual secret value if very long).
        how_used: How the secret appears to be used in the code.
        confirmation: Evidence confirming this is a real secret, not a placeholder.
        line_start: First line number of the finding.
        line_end: Last line number of the finding (0 = same as line_start).
    """
    agent_json = {
        "description": description,
        "code_snippet": code_snippet,
        "how_used": how_used,
        "confirmation": confirmation,
    }
    alert_id = _db.save_alert(
        _scan_id, file_path, line_start, line_end or line_start,
        alert_type, severity, agent_json,
    )
    log.info("[ALERT #%d] %s severity=%s file=%s", alert_id, alert_type, severity, file_path)
    return f"Alert #{alert_id} saved."


@mcp.tool()
def save_note(content: str) -> str:
    """Save an intermediate analysis note / summary for later reference.

    Use this to record findings, observations, or a map of the repository
    so you can refer back to them without re-reading files.
    """
    note_id = _db.save_note(_scan_id, content)
    return f"Note #{note_id} saved."


@mcp.tool()
def get_notes() -> str:
    """Retrieve all notes saved during this scan."""
    notes = _db.list_notes(_scan_id)
    if not notes:
        return "No notes yet."
    return "\n\n---\n\n".join(notes)


@mcp.tool()
def read_file(path: str) -> str:
    """Read a file from the cloned repository (current HEAD).

    Args:
        path: Path relative to the repository root.
    """
    full = _repo_path / path
    if not full.exists():
        return f"File not found: {path}"
    if full.stat().st_size > 10 * 1024 * 1024:
        return f"File too large to read: {path}"
    try:
        return full.read_text(errors="replace")
    except OSError as exc:
        return f"Cannot read {path}: {exc}"


@mcp.tool()
def read_file_at_commit(commit_sha: str, path: str) -> str:
    """Read a file as it existed at a specific commit SHA.

    Useful for inspecting historical versions of files where a secret
    may have been committed and later deleted.

    Args:
        commit_sha: Full or abbreviated commit SHA.
        path: Path relative to the repository root.
    """
    content = get_file_at_commit(_repo_path, commit_sha, path)
    if content is None:
        return f"Could not read {path} at {commit_sha}"
    return content


@mcp.tool()
def list_files(subdir: str = "", pattern: str = "") -> str:
    """List files in the repository.

    Args:
        subdir: Sub-directory to list (empty = root).
        pattern: Optional glob pattern, e.g. '**/*.env' or '*.json'.
    """
    base = _repo_path / subdir if subdir else _repo_path
    if not base.exists():
        return f"Directory not found: {subdir}"

    if pattern:
        paths = list(base.glob(pattern))
    else:
        paths = [p for p in base.rglob("*") if p.is_file()]

    git_dir = _repo_path / ".git"
    paths = [p for p in paths if git_dir not in p.parents]
    rel = [str(p.relative_to(_repo_path)) for p in sorted(paths)[:500]]
    return "\n".join(rel) or "No files found."


@mcp.tool()
def search_content(pattern: str, subdir: str = "") -> str:
    """Search file contents for a pattern (literal string, case-insensitive).

    Returns up to 50 matches with file path and line number.

    Args:
        pattern: Literal string to search for (not regex).
        subdir: Restrict search to this sub-directory.
    """
    import subprocess
    base = str(_repo_path / subdir) if subdir else str(_repo_path)
    result = subprocess.run(
        ["git", "-C", str(_repo_path), "grep", "-i", "-n", "--max-count=50", pattern],
        capture_output=True, text=True, timeout=30,
    )
    output = result.stdout.strip()
    return output if output else "No matches found."


@mcp.tool()
def get_commit_log(limit: int = 100) -> str:
    """Return the git commit log (newest first).

    Args:
        limit: Maximum number of commits to return (max 500).
    """
    limit = min(limit, 500)
    return _walker_commit_log(_repo_path, limit=limit)


@mcp.tool()
def get_file_tree() -> str:
    """Return the full file tree of the repository at HEAD."""
    return _walker_file_tree(_repo_path)


@mcp.tool()
def send_mattermost(message: str, channel: str = "") -> str:
    """Send a message to Mattermost.

    Args:
        message: Markdown message to send.
        channel: Override the default channel (leave empty to use configured default).
    """
    if _notifications is None:
        return "Mattermost is not configured."
    try:
        _notifications.send(message, channel=channel or None)
        return "Message sent."
    except Exception as exc:
        return f"Mattermost error: {exc}"


# ── OpenAI-compatible tool schema list ────────────────────────────────────────
# Defined explicitly so we don't depend on FastMCP internals for schema export.

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "save_alert",
            "description": save_alert.__doc__.split("\n")[0],
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path":    {"type": "string"},
                    "alert_type":   {"type": "string", "enum": ["secret", "token", "pii", "corporate_secret", "password", "key"]},
                    "severity":     {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "description":  {"type": "string"},
                    "code_snippet": {"type": "string"},
                    "how_used":     {"type": "string"},
                    "confirmation": {"type": "string"},
                    "line_start":   {"type": "integer"},
                    "line_end":     {"type": "integer"},
                },
                "required": ["file_path", "alert_type", "severity", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Save an intermediate analysis note / summary",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_notes",
            "description": "Retrieve all notes saved during this scan",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the cloned repository (current HEAD)",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_at_commit",
            "description": "Read a file as it existed at a specific commit SHA",
            "parameters": {
                "type": "object",
                "properties": {
                    "commit_sha": {"type": "string"},
                    "path":       {"type": "string"},
                },
                "required": ["commit_sha", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the repository, optionally filtered by glob pattern",
            "parameters": {
                "type": "object",
                "properties": {
                    "subdir":  {"type": "string"},
                    "pattern": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_content",
            "description": "Search file contents for a pattern (literal, case-insensitive)",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "subdir":  {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_commit_log",
            "description": "Return the git commit log (newest first)",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_tree",
            "description": "Return the full file tree of the repository at HEAD",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_mattermost",
            "description": "Send a message to Mattermost",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "channel": {"type": "string"},
                },
                "required": ["message"],
            },
        },
    },
]

# Direct Python call registry — used by agent runner without MCP transport
TOOLS: dict[str, Any] = {
    "save_alert":           save_alert,
    "save_note":            save_note,
    "get_notes":            get_notes,
    "read_file":            read_file,
    "read_file_at_commit":  read_file_at_commit,
    "list_files":           list_files,
    "search_content":       search_content,
    "get_commit_log":       get_commit_log,
    "get_file_tree":        get_file_tree,
    "send_mattermost":      send_mattermost,
}
