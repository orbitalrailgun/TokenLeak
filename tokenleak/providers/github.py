"""GitHub provider — list all repos of a user via GitHub REST API."""

from __future__ import annotations

from typing import Generator

import httpx

from tokenleak.config import Config
from tokenleak.logging_setup import get_logger

log = get_logger()

_BASE = "https://api.github.com"


def list_github_user_repos(username: str, config: Config) -> Generator[str, None, None]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if config.github_token:
        headers["Authorization"] = f"Bearer {config.github_token}"

    page = 1
    per_page = 100
    with httpx.Client(headers=headers, timeout=30) as client:
        while True:
            resp = client.get(
                f"{_BASE}/users/{username}/repos",
                params={"per_page": per_page, "page": page, "type": "all"},
            )
            if resp.status_code == 404:
                log.warning("GitHub user not found: %s", username)
                return
            resp.raise_for_status()
            repos = resp.json()
            if not repos:
                break
            for repo in repos:
                yield repo["clone_url"]
            if len(repos) < per_page:
                break
            page += 1

    log.info("GitHub: enumerated repos for user %s", username)
