"""Logging configuration: stderr + optional syslog (local socket or remote UDP)."""

import logging
import logging.handlers
import sys
from typing import Optional

LOGGER_NAME = "tokenleak"


def setup_logging(
    syslog_enabled: bool = True,
    syslog_host: Optional[str] = None,
    syslog_port: int = 514,
) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.INFO)
    stderr_handler.setFormatter(fmt)
    logger.addHandler(stderr_handler)

    if syslog_enabled:
        try:
            if syslog_host:
                syslog_handler = logging.handlers.SysLogHandler(
                    address=(syslog_host, syslog_port),
                    facility=logging.handlers.SysLogHandler.LOG_DAEMON,
                )
            else:
                # Local syslog socket
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
