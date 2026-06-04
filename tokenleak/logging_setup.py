"""Logging configuration: stderr + optional file + optional syslog."""

import logging
import logging.handlers
from typing import Optional

LOGGER_NAME = "tokenleak"


class _ConsoleHandler(logging.Handler):
    """Writes log records through Rich's animation console.

    Using _console.print() instead of sys.stderr.write() lets Rich coordinate
    the log output with any active Live display — preventing the animation block
    from being displaced by log messages written outside Rich's context.
    """

    def emit(self, record: logging.LogRecord) -> None:
        from tokenleak.animation import _console
        try:
            _console.print(self.format(record), markup=False, highlight=False, soft_wrap=True)
        except Exception:
            self.handleError(record)


def setup_logging(
    syslog_enabled: bool = True,
    syslog_host: Optional[str] = None,
    syslog_port: int = 514,
    log_file: Optional[str] = None,
) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    stderr_handler = _ConsoleHandler()
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(fmt)
    logger.addHandler(stderr_handler)

    if log_file:
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(fmt)
            logger.addHandler(file_handler)
        except Exception as exc:
            logger.warning("Cannot open log file %s: %s", log_file, exc)

    if syslog_enabled:
        try:
            if syslog_host:
                syslog_handler = logging.handlers.SysLogHandler(
                    address=(syslog_host, syslog_port),
                    facility=logging.handlers.SysLogHandler.LOG_DAEMON,
                )
            else:
                import platform
                socket_path = "/dev/log" if platform.system() == "Linux" else "/var/run/syslog"
                syslog_handler = logging.handlers.SysLogHandler(
                    address=socket_path,
                    facility=logging.handlers.SysLogHandler.LOG_DAEMON,
                )
            syslog_handler.setLevel(logging.INFO)
            syslog_handler.setFormatter(
                logging.Formatter("tokenleak: %(levelname)s %(message)s")
            )
            logger.addHandler(syslog_handler)
        except Exception as exc:
            logger.warning("Syslog unavailable: %s", exc)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
