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
import unicodedata
import zlib
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
_ocr_client = None
_ocr_model: str = ""
_ai_model: str = ""
_repo_id: Optional[int] = None
_commit_sha: str = ""
_commit_date = None  # datetime | None
_triggered_by: Optional[str] = None


def init_context(
    db,
    scan_id: int,
    repo_path: Path,
    notifications=None,
    ocr_client=None,
    ocr_model: str = "",
    ai_model: str = "",
    repo_id: Optional[int] = None,
    commit_sha: str = "",
    commit_date=None,
    triggered_by: Optional[str] = None,
) -> None:
    global _db, _scan_id, _repo_path, _notifications
    global _ocr_client, _ocr_model, _ai_model
    global _repo_id, _commit_sha, _commit_date, _triggered_by
    _db = db
    _scan_id = scan_id
    _repo_path = repo_path
    _notifications = notifications
    _ocr_client = ocr_client
    _ocr_model = ocr_model
    _ai_model = ai_model
    _repo_id = repo_id
    _commit_sha = commit_sha
    _commit_date = commit_date
    _triggered_by = triggered_by


# ── Helpers ───────────────────────────────────────────────────────────────────

# Unicode characters that look like ASCII hyphen-minus (U+002D) but aren't.
# Models occasionally generate these when producing file paths.
_HYPHEN_LOOKALIKES = str.maketrans({
    '‐': '-',  # HYPHEN
    '‑': '-',  # NON-BREAKING HYPHEN
    '‒': '-',  # FIGURE DASH
    '–': '-',  # EN DASH
    '—': '-',  # EM DASH
    '―': '-',  # HORIZONTAL BAR
    '−': '-',  # MINUS SIGN
    '﹘': '-',  # SMALL EM DASH
    '﹣': '-',  # SMALL HYPHEN-MINUS
    '－': '-',  # FULLWIDTH HYPHEN-MINUS
})


def _sanitize_path(path: Optional[str]) -> Optional[str]:
    """Normalize Unicode confusable characters in file paths to ASCII equivalents.

    Some models produce non-breaking hyphens or other Unicode lookalikes in file
    paths, making the path impossible to resolve on the filesystem.  This function
    replaces all known hyphen/dash lookalikes with a plain ASCII hyphen and applies
    NFC normalization.  If the path was changed a WARNING is logged so the anomaly
    is visible in the log.
    """
    if not path:
        return path
    normalized = unicodedata.normalize("NFC", path).translate(_HYPHEN_LOOKALIKES).strip()
    if normalized != path:
        log.warning(
            "save_alert: file_path was normalized (Unicode confusables replaced): %r → %r",
            path, normalized,
        )
    return normalized


# ── Tools ─────────────────────────────────────────────────────────────────────

def _synthetic_line(alert_type: str, description: str, code_snippet: str) -> int:
    """Return a stable negative line number for binary/unpositioned findings.

    Binary files have no real line numbers, so line_start=0 would collide in
    UNIQUE(scan_id, file_path, line_start, alert_type) when the same file has
    multiple distinct findings of the same type.  We use a content-derived CRC32
    to produce a unique-per-finding negative integer:

      - Negative → clearly not a real line number; report/UI shows "N/A"
      - Content-stable → same finding saved twice → same value → deduplicated
      - Different content → different value → all distinct findings are stored
    """
    key = f"{alert_type}|{description[:200]}|{code_snippet[:200]}"
    crc = zlib.crc32(key.encode()) & 0x7FFF_FFFF  # 31-bit positive
    return -max(crc, 1)


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
        line_start: First line number of the finding (0 if unknown / binary file).
        line_end: Last line number of the finding (0 = same as line_start).
    """
    if line_start == 0:
        # Binary or unpositioned finding — derive a content-stable synthetic
        # line number so different findings in the same file don't collide on
        # UNIQUE(scan_id, file_path, line_start, alert_type).
        line_start = _synthetic_line(alert_type, description, code_snippet)
        line_end = line_start

    agent_json = {
        "description": description,
        "code_snippet": code_snippet,
        "how_used": how_used,
        "confirmation": confirmation,
    }
    alert_id = _db.save_alert(
        _scan_id, _sanitize_path(file_path), line_start, line_end or line_start,
        alert_type, severity, agent_json,
        repo_id=_repo_id,
        commit_sha=_commit_sha or None,
        commit_date=_commit_date,
        triggered_by=_triggered_by,
        ai_model=_ai_model or None,
    )
    log.info("[ALERT #%d] %s severity=%s file=%s", alert_id, alert_type, severity, file_path)
    if _notifications is not None:
        try:
            sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(severity, "⚪")
            line_info = f" line {line_start}" if line_start > 0 else ""
            _notifications.send(
                f"{sev_icon} **{severity.upper()} [{alert_type}]** "
                f"`{file_path}`{line_info}\n{description}"
            )
        except Exception as exc:
            log.warning("Mattermost auto-notify failed for alert #%d: %s", alert_id, exc)
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
def read_file(path: str, offset: int = 0, limit: int = 0) -> str:
    """Read a file from the cloned repository (current HEAD).

    Large files MUST be read in chunks — always specify limit, and use offset
    to advance through the file:

        read_file("large.py", offset=0, limit=50000)        # chunk 1
        read_file("large.py", offset=50000, limit=50000)    # chunk 2
        ...repeat until returned content is shorter than limit (= last chunk).

    Args:
        path:   Path relative to the repository root.
        offset: Character offset to start reading from (default 0 = beginning).
        limit:  Maximum chars to return from offset (0 = all remaining from offset).
    """
    full = _repo_path / path
    if not full.exists():
        return f"File not found: {path}"
    if full.stat().st_size > 10 * 1024 * 1024:
        return (
            f"File too large to read at once: {path} "
            f"({full.stat().st_size // 1024:,} KB). "
            f"Use read_file(\"{path}\", offset=0, limit=60000) to read in chunks."
        )
    try:
        content = full.read_text(errors="replace")
    except OSError as exc:
        return f"Cannot read {path}: {exc}"

    total_chars = len(content)

    if offset and offset >= total_chars:
        return (
            f"[Offset {offset:,} is past end of file — "
            f"{total_chars:,} chars total in {path}]"
        )

    chunk = content[offset:] if offset else content

    if limit and len(chunk) > limit:
        chunk = chunk[:limit]
        next_offset = offset + limit
        remaining = total_chars - next_offset
        chunk += (
            f"\n\n[CHUNK: chars {offset:,}–{next_offset:,} of {total_chars:,} total. "
            f"{remaining:,} chars remaining. "
            f"Next chunk: read_file(\"{path}\", offset={next_offset}, limit={limit})]"
        )

    return chunk


@mcp.tool()
def read_file_at_commit(commit_sha: str, path: str, offset: int = 0, limit: int = 0) -> str:
    """Read a file as it existed at a specific commit SHA.

    Useful for inspecting historical versions of files where a secret
    may have been committed and later deleted.

    Large files MUST be read in chunks — always specify limit, then advance
    with offset until the returned content is shorter than limit:

        read_file_at_commit("abc123", "big.py", offset=0, limit=60000)
        read_file_at_commit("abc123", "big.py", offset=60000, limit=60000)
        ...

    Args:
        commit_sha: Full or abbreviated commit SHA.
        path:       Path relative to the repository root.
        offset:     Character offset to start reading from (default 0).
        limit:      Maximum chars to return from offset (0 = all remaining).
    """
    content = get_file_at_commit(_repo_path, commit_sha, path)
    if content is None:
        return f"Could not read {path} at {commit_sha}"

    total_chars = len(content)

    if offset and offset >= total_chars:
        return (
            f"[Offset {offset:,} is past end of file — "
            f"{total_chars:,} chars total in {path} at {commit_sha}]"
        )

    chunk = content[offset:] if offset else content

    if limit and len(chunk) > limit:
        chunk = chunk[:limit]
        next_offset = offset + limit
        remaining = total_chars - next_offset
        chunk += (
            f"\n\n[CHUNK: chars {offset:,}–{next_offset:,} of {total_chars:,} total. "
            f"{remaining:,} chars remaining. "
            f"Next chunk: read_file_at_commit(\"{commit_sha}\", \"{path}\", "
            f"offset={next_offset}, limit={limit})]"
        )

    return chunk


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
    """Send a final scan summary to Mattermost.

    Call this ONCE after all save_alert() calls are done, with a brief summary
    of everything found (or nothing found). Do NOT call this for individual
    findings — alert notifications are sent automatically by save_alert().

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


@mcp.tool()
def analyze_image_file(path: str) -> str:
    """Analyze an image file or Jupyter notebook for sensitive information using OCR.

    Pass an image file path (.png, .jpg, .jpeg, .gif, .webp) or a Jupyter notebook
    path (.ipynb) — the tool automatically extracts and analyzes all embedded
    image outputs from notebooks.

    Returns a description of any sensitive content found, or a "nothing found" message.
    Returns an error message if OCR is not configured (TOKENLEAK_OCR_MODEL not set).

    Args:
        path: Path relative to the repository root.
    """
    if not _ocr_model or not _ocr_client:
        return "OCR not configured — set TOKENLEAK_OCR_MODEL to enable image analysis."

    from tokenleak.scanner.ocr import (
        analyze_image,
        extract_notebook_images,
        mime_for_extension,
        SUPPORTED_EXTENSIONS,
    )

    full = _repo_path / path
    if not full.exists():
        return f"File not found: {path}"

    if path.endswith(".ipynb"):
        try:
            nb_text = full.read_text(errors="replace")
        except OSError as exc:
            return f"Cannot read {path}: {exc}"
        images = extract_notebook_images(nb_text)
        if not images:
            return "No embedded images found in this notebook."
        findings = []
        for cell_idx, mime, img_bytes in images:
            finding, _ = analyze_image(
                _ocr_client, _ocr_model, img_bytes, mime,
                context=f"{path} cell[{cell_idx}]",
            )
            if finding:
                findings.append(f"Cell {cell_idx}: {finding}")
        return "\n\n".join(findings) if findings else "No sensitive information found in notebook images."

    mime = mime_for_extension(full.suffix)
    if not mime:
        return f"Unsupported file type for OCR: {full.suffix}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"

    try:
        img_bytes = full.read_bytes()
    except OSError as exc:
        return f"Cannot read {path}: {exc}"

    finding, _ = analyze_image(_ocr_client, _ocr_model, img_bytes, mime, context=path)
    return finding or "No sensitive information found."


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
            "description": (
                "Read a file from the cloned repository (current HEAD). "
                "For large files use offset + limit to read in chunks: "
                "read_file(path, offset=0, limit=60000), then "
                "read_file(path, offset=60000, limit=60000), etc., "
                "until the returned content is shorter than limit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":   {"type": "string"},
                    "offset": {"type": "integer", "description": "Char offset to start reading from (default 0)"},
                    "limit":  {"type": "integer", "description": "Max chars to return from offset (0 = all remaining)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_at_commit",
            "description": (
                "Read a file as it existed at a specific commit SHA. "
                "For large files use offset + limit to read in chunks: "
                "read_file_at_commit(sha, path, offset=0, limit=60000), then "
                "read_file_at_commit(sha, path, offset=60000, limit=60000), etc., "
                "until the returned content is shorter than limit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "commit_sha": {"type": "string"},
                    "path":       {"type": "string"},
                    "offset":     {"type": "integer", "description": "Char offset to start reading from (default 0)"},
                    "limit":      {"type": "integer", "description": "Max chars to return from offset (0 = all remaining)"},
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
            "description": (
                "Send a final scan summary to Mattermost. "
                "Call ONCE after all save_alert() calls, never for individual findings — "
                "alert notifications are sent automatically by save_alert()."
            ),
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
    {
        "type": "function",
        "function": {
            "name": "analyze_image_file",
            "description": (
                "Analyze an image file (.png, .jpg, .jpeg, .gif, .webp) or "
                "Jupyter notebook (.ipynb) for sensitive information using OCR. "
                "For notebooks, all embedded image outputs are analyzed automatically. "
                "Returns findings or 'No sensitive information found.' "
                "Returns an error if TOKENLEAK_OCR_MODEL is not configured."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to repository root"}},
                "required": ["path"],
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
    "analyze_image_file":   analyze_image_file,
}
