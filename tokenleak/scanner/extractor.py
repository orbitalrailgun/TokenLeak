"""Extract readable text from files in a git repository snapshot.

Text files are read directly.  Binary files are processed with the
`strings`-equivalent approach: only sequences of printable ASCII characters
longer than MIN_STRING_LEN are extracted (no subprocess dependency).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB hard cap (overridden per config)
MIN_STRING_LEN = 6                  # min printable-ASCII run extracted from binaries

_PRINTABLE_RE = re.compile(rb"[ -~]{%d,}" % MIN_STRING_LEN)

# Extensions always treated as binary regardless of content sniff
_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
    ".mp3", ".mp4", ".wav", ".avi", ".mkv", ".mov",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".class", ".jar", ".war", ".ear",
    ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a",
    ".pyc", ".pyd", ".whl",
    ".db", ".sqlite", ".sqlite3",
    ".ttf", ".otf", ".woff", ".woff2",
}


def _is_binary_path(path: Path) -> bool:
    return path.suffix.lower() in _BINARY_EXTENSIONS


def _sniff_binary(data: bytes) -> bool:
    """Return True if the first 8 KB looks like binary content."""
    chunk = data[:8192]
    return b"\x00" in chunk


def read_file(path: Path, max_bytes: int = MAX_FILE_BYTES) -> tuple[str, bool]:
    """Read file content.

    Returns (content_string, is_binary).
    For binary files, returns strings-extracted printable runs joined by newlines.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return "", False

    if len(data) > max_bytes:
        return "", False

    if _is_binary_path(path) or _sniff_binary(data):
        strings = _PRINTABLE_RE.findall(data)
        return "\n".join(s.decode("ascii", errors="replace") for s in strings), True

    try:
        return data.decode("utf-8", errors="replace"), False
    except Exception:
        return data.decode("latin-1", errors="replace"), False


def iter_repo_files(repo_path: Path) -> Iterator[Path]:
    """Yield all non-.git files in the working tree."""
    git_dir = repo_path / ".git"
    for p in repo_path.rglob("*"):
        if p.is_file() and git_dir not in p.parents and p != git_dir:
            yield p
