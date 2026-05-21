"""PID-file based process lock — prevents concurrent scan instances."""

import os
import signal
import sys
from pathlib import Path

from tokenleak.logging_setup import get_logger

log = get_logger()


class LockError(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — treat as alive.
        return True


def acquire(lock_file: str) -> None:
    path = Path(lock_file)

    if path.exists():
        try:
            pid = int(path.read_text().strip())
        except (ValueError, OSError):
            pid = None

        if pid and _pid_alive(pid):
            raise LockError(
                f"Another tokenleak instance is already running (PID {pid}). "
                f"Lock file: {lock_file}"
            )

        log.warning("Stale lock file found (PID %s dead). Removing.", pid)
        path.unlink(missing_ok=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()))
    log.debug("Lock acquired: %s (PID %d)", lock_file, os.getpid())

    # Release on exit/signal automatically
    def _cleanup(*_):
        release(lock_file)
        sys.exit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _cleanup)
        except (OSError, ValueError):
            pass


def release(lock_file: str) -> None:
    path = Path(lock_file)
    try:
        path.unlink(missing_ok=True)
        log.debug("Lock released: %s", lock_file)
    except OSError as exc:
        log.warning("Could not remove lock file %s: %s", lock_file, exc)
