"""Gitea / Forgejo provider — list repos for a user."""

from __future__ import annotations

from typing import Generator, Optional

import httpx

from tokenleak.config import Config
from tokenleak.logging_setup import get_logger

log = get_logger()


def list_gitea_user_repos(
    username: str,
    config: Config,
    base_url: Optional[str] = None,
) -> Generator[str, None, None]:
    base = (base_url or config.gitea_url or "").rstrip("/")
    if not base:
        log.error("TOKENLEAK_GITEA_URL is not set")
        return

    headers = {}
    if config.gitea_token:
        headers["Authorization"] = f"token {config.gitea_token}"

    page = 1
    limit = 50
    with httpx.Client(headers=headers, timeout=30) as client:
        while True:
            resp = client.get(
                f"{base}/api/v1/repos/search",
                params={"q": "", "owner": username, "limit": limit, "page": page},
            )
            if resp.status_code == 404:
                log.warning("Gitea user not found: %s on %s", username, base)
                return
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break
            for repo in data:
                yield repo["clone_url"]
            if len(data) < limit:
                break
            page += 1

    log.info("Gitea: enumerated repos for user %s on %s", username, base)
