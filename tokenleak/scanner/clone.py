"""Safe git clone with malware mitigations.

Security measures applied after every clone:
  - GIT_TERMINAL_PROMPT=0  — never prompt for credentials
  - GIT_ASKPASS=echo        — fail-fast on auth challenge
  - Hooks directory is wiped immediately after clone
  - All execute bits are removed recursively on the working tree
  - Clone lives in a temp subdirectory and is deleted after scanning
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path

from tokenleak.config import Config
from tokenleak.logging_setup import get_logger

log = get_logger()

_SAFE_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "echo",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=no",
}


def _remove_exec_bits(path: Path) -> None:
    # Only strip execute bits from regular files.
    # Directories need the execute (traverse/search) bit to remain accessible —
    # without it no one can stat or open files inside them, even with read permission.
    for item in path.rglob("*"):
        if item.is_dir():
            continue
        try:
            mode = item.stat().st_mode
            item.chmod(mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        except OSError:
            pass


def clone(url: str, config: Config) -> Path:
    """Clone *url* into a fresh temp directory. Returns the working-tree path."""
    base = Path(config.clone_dir)
    base.mkdir(parents=True, exist_ok=True)
    dest = base / f"{uuid.uuid4().hex}"

    log.info("Cloning %s → %s", url, dest)
    try:
        subprocess.run(
            ["git", "clone", "--no-local", url, str(dest)],
            env=_SAFE_ENV,
            check=True,
            timeout=config.clone_timeout_sec,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"Clone timed out after {config.clone_timeout_sec}s: {url}")
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"Clone failed for {url}: {exc.stderr.decode(errors='replace').strip()}")

    # Wipe hooks — they can run arbitrary code
    hooks_dir = dest / ".git" / "hooks"
    if hooks_dir.exists():
        shutil.rmtree(hooks_dir)
        hooks_dir.mkdir()

    # Remove all exec bits on the working tree (not .git internals)
    _remove_exec_bits(dest)

    # Tell git not to track permission changes — we intentionally stripped
    # execute bits above, but git would otherwise see every file as "modified",
    # which causes `git checkout --detach` to fail when scanning branch tips.
    subprocess.run(
        ["git", "-C", str(dest), "config", "core.fileMode", "false"],
        capture_output=True, timeout=10,
    )

    log.info("Cloned %s successfully", url)
    return dest


def repo_size_mb(repo_path: Path) -> float:
    """Return on-disk size of the cloned repo in megabytes."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "count-objects", "-vH"],
            capture_output=True, text=True, timeout=30,
        )
        for line in result.stdout.splitlines():
            if line.startswith("size-pack:"):
                val = line.split(":")[1].strip()
                # val is like "12.34 MiB" or "512 KiB"
                num, unit = val.split()
                num = float(num)
                if "K" in unit:
                    num /= 1024
                elif "G" in unit:
                    num *= 1024
                return num
    except Exception:
        pass
    # Fallback: du-style walk
    total = sum(f.stat().st_size for f in repo_path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def remove(repo_path: Path) -> None:
    shutil.rmtree(repo_path, ignore_errors=True)
    log.debug("Removed clone: %s", repo_path)
