"""Walk the full git history of a cloned repository.

For each commit that has not yet been scanned (checked against the DB),
yields a CommitInfo with the commit metadata.  The caller is responsible for
checking out / extracting individual file contents at that SHA.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from tokenleak.logging_setup import get_logger

log = get_logger()

_GIT_LOG_FORMAT = "%H\x1f%ae\x1f%ai\x1f%s"   # sha, author-email, date, subject


@dataclass
class CommitInfo:
    sha: str
    author: str
    date: Optional[datetime]
    message: str


@dataclass
class FileAtCommit:
    sha: str
    path: str       # relative path inside the repo
    status: str     # A=added, M=modified, D=deleted


def list_commits(repo_path: Path) -> list[CommitInfo]:
    """Return all commits in the repo ordered newest-first."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log", "--all", f"--format={_GIT_LOG_FORMAT}"],
        capture_output=True, text=True, timeout=120,
    )
    commits = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f", 3)
        if len(parts) < 4:
            continue
        sha, author, date_str, message = parts
        try:
            dt = datetime.fromisoformat(date_str.strip())
        except ValueError:
            dt = None
        commits.append(CommitInfo(sha=sha.strip(), author=author.strip(), date=dt, message=message.strip()))
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
