"""Walk the full git history of a cloned repository.

For each commit that has not yet been scanned (checked against the DB),
yields a CommitInfo with the commit metadata.  The caller is responsible for
choosing between full-file or diff-only extraction.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from tokenleak.logging_setup import get_logger

log = get_logger()

_GIT_LOG_FORMAT = "%H\x1f%ae\x1f%ai\x1f%s\x1f%P"  # sha, author, date, subject, parents


@dataclass
class CommitInfo:
    sha: str
    author: str
    date: Optional[datetime]
    message: str
    is_merge: bool = False


@dataclass
class FileAtCommit:
    sha: str
    path: str       # relative path inside the repo
    status: str     # A=added, M=modified, D=deleted


def list_commits(repo_path: Path, skip_merges: bool = False) -> list[CommitInfo]:
    """Return all commits in the repo ordered newest-first.

    Args:
        skip_merges: When True, merge commits are excluded. Merge commits don't
                     introduce new content — they only combine existing branches —
                     so skipping them avoids duplicate analysis in diff mode.
    """
    cmd = ["git", "-C", str(repo_path), "log", "--all", f"--format={_GIT_LOG_FORMAT}"]
    if skip_merges:
        cmd.append("--no-merges")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    commits = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f", 4)
        if len(parts) < 4:
            continue
        sha, author, date_str, message = parts[:4]
        parents = parts[4].strip() if len(parts) > 4 else ""
        try:
            dt = datetime.fromisoformat(date_str.strip())
        except ValueError:
            dt = None
        commits.append(CommitInfo(
            sha=sha.strip(),
            author=author.strip(),
            date=dt,
            message=message.strip(),
            is_merge=len(parents.split()) > 1,
        ))
    return commits


def list_changed_files(repo_path: Path, sha: str) -> list[FileAtCommit]:
    """Return files changed in this specific commit (diff vs parent)."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff-tree", "--no-commit-id", "-r",
         "--name-status", "--diff-filter=ADM", sha],
        capture_output=True, text=True, timeout=60,
    )
    files = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            files.append(FileAtCommit(sha=sha, status=parts[0].strip(), path=parts[1].strip()))
    return files


def get_file_at_commit(repo_path: Path, sha: str, file_path: str) -> Optional[str]:
    """Return file content at a given commit SHA, or None if unavailable."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "show", f"{sha}:{file_path}"],
        capture_output=True, timeout=30,
    )
    if result.returncode != 0:
        return None
    data = result.stdout
    # Binary sniff
    if b"\x00" in data[:8192]:
        import re
        strings = re.findall(rb"[ -~]{6,}", data)
        return "\n".join(s.decode("ascii", errors="replace") for s in strings)
    return data.decode("utf-8", errors="replace")


def get_commit_log_text(repo_path: Path, limit: int = 200) -> str:
    """Return a compact commit log for the agent's initial context."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log", "--all",
         "--oneline", f"-{limit}"],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


def get_file_tree(repo_path: Path) -> str:
    """Return the file tree of the current HEAD."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


# Type alias: file path → list of (line_number, line_content)
DiffAdditions = dict[str, list[tuple[int, str]]]

_DIFF_MAX_BYTES = 5 * 1024 * 1024   # 5 MB hard cap on raw diff output
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def get_commit_diff_additions(repo_path: Path, sha: str) -> DiffAdditions:
    """Return only the lines *added* by this commit, keyed by file path.

    Uses ``git show --unified=0`` so we get zero context lines — only the
    actual changed lines.  Binary files and deletions are ignored.

    Returns an empty dict for merge commits or commits with no text additions.
    """
    result = subprocess.run(
        [
            "git", "-C", str(repo_path), "show", sha,
            "--unified=0",        # no context lines
            "--diff-filter=ADM",  # Added, Deleted (for history), Modified
            "--no-color",
            "--no-prefix",
        ],
        capture_output=True,
        timeout=60,
    )

    raw = result.stdout
    if not raw:
        return {}

    # Hard cap — very large diffs are usually generated/minified files
    truncated = len(raw) > _DIFF_MAX_BYTES
    if truncated:
        raw = raw[:_DIFF_MAX_BYTES]
        log.warning("Diff for %s truncated at %d bytes", sha[:8], _DIFF_MAX_BYTES)

    text = raw.decode("utf-8", errors="replace")

    additions: DiffAdditions = {}
    current_file: Optional[str] = None
    current_line = 0

    for line in text.splitlines():
        # New file in the diff
        if line.startswith("+++ "):
            # strip a/ b/ prefixes if present
            path = line[4:].lstrip("b/").lstrip("a/")
            if path == "/dev/null":
                current_file = None
            else:
                current_file = path
                additions.setdefault(current_file, [])
            continue

        # Hunk header — update line counter
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if m:
                current_line = int(m.group(1))
            continue

        if current_file is None:
            continue

        if line.startswith("+"):
            additions[current_file].append((current_line, line[1:]))
            current_line += 1
        elif not line.startswith("-") and not line.startswith("\\"):
            current_line += 1

    # Drop files with no additions
    return {f: lines for f, lines in additions.items() if lines}
