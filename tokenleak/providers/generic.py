"""Generic git URL validation (no API — just clone URL pass-through)."""

from __future__ import annotations

import re
from typing import Optional

from tokenleak.logging_setup import get_logger

log = get_logger()

_GIT_URL_RE = re.compile(
    r"^(https?://|git@|ssh://|git://)"
    r"[\w.\-]+(:\d+)?[/:][\w.\-/]+(?:\.git)?$"
)


def validate_git_url(url: str) -> Optional[str]:
    url = url.strip()
    if _GIT_URL_RE.match(url):
        return url
    log.warning("Skipping unrecognised target (not a valid git URL): %s", url)
    return None
