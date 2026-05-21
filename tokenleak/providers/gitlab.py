"""GitLab provider — list repos for a user or entire server."""

from __future__ import annotations

from typing import Generator, Optional

import httpx

from tokenleak.config import Config
from tokenleak.logging_setup import get_logger

log = get_logger()


def _headers(config: Config) -> dict:
    if config.gitlab_token:
        return {"PRIVATE-TOKEN": config.gitlab_token}
    return {}


def list_gitlab_user_repos(
    username: str,
    config: Config,
    base_url: Optional[str] = None,
) -> Generator[str, None, None]:
    base = (base_url or config.gitlab_url).rstrip("/")
    page = 1
    per_page = 100
    with httpx.Client(headers=_headers(config), timeout=30) as client:
        # Resolve username → user id
        resp = client.get(f"{base}/api/v4/users", params={"username": username})
        resp.raise_for_status()
        users = resp.json()
        if not users:
            log.warning("GitLab user not found: %s on %s", username, base)
            return
        user_id = users[0]["id"]

        while True:
            resp = client.get(
                f"{base}/api/v4/users/{user_id}/projects",
                params={"per_page": per_page, "page": page, "membership": True},
            )
            resp.raise_for_status()
            projects = resp.json()
            if not projects:
                break
            for p in projects:
                yield p["http_url_to_repo"]
            if len(projects) < per_page:
                break
            page += 1

    log.info("GitLab: enumerated repos for user %s on %s", username, base)


def list_gitlab_server_repos(
    config: Config,
    base_url: Optional[str] = None,
) -> Generator[str, None, None]:
    base = (base_url or config.gitlab_url).rstrip("/")
    page = 1
    per_page = 100
    with httpx.Client(headers=_headers(config), timeout=30) as client:
        while True:
            resp = client.get(
                f"{base}/api/v4/projects",
                params={"per_page": per_page, "page": page, "order_by": "id"},
            )
            resp.raise_for_status()
            projects = resp.json()
            if not projects:
                break
            for p in projects:
                yield p["http_url_to_repo"]
            if len(projects) < per_page:
                break
            page += 1

    log.info("GitLab: enumerated all repos on server %s", base)
