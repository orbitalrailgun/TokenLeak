"""Resolve a target specifier into a list of plain git clone URLs.

Supported formats in repos.txt / CLI:
  https://github.com/user/repo           — plain git URL
  github:username                        — all public repos of a GitHub user
  gitlab:username                        — all repos on configured gitlab_url
  gitlab:https://custom.host:username    — all repos on a specific GitLab host
  gitea:username                         — all repos on configured gitea_url
  gitea:https://custom.host:username     — all repos on a specific Gitea host
  server:https://gitlab.example.com      — ALL repos on that GitLab instance
"""

from __future__ import annotations

from typing import Generator

from tokenleak.config import Config
from tokenleak.providers.github import list_github_user_repos
from tokenleak.providers.gitlab import list_gitlab_user_repos, list_gitlab_server_repos
from tokenleak.providers.gitea import list_gitea_user_repos
from tokenleak.providers.generic import validate_git_url


def resolve_targets(targets: list[str], config: Config) -> Generator[str, None, None]:
    for target in targets:
        target = target.strip()
        if not target or target.startswith("#"):
            continue

        if target.startswith("github:"):
            username = target[len("github:"):]
            yield from list_github_user_repos(username, config)

        elif target.startswith("gitlab:"):
            rest = target[len("gitlab:"):]
            if rest.startswith("http://") or rest.startswith("https://"):
                base_url, _, username = rest.rpartition(":")
                yield from list_gitlab_user_repos(username, config, base_url=base_url)
            else:
                yield from list_gitlab_user_repos(rest, config)

        elif target.startswith("gitea:"):
            rest = target[len("gitea:"):]
            if rest.startswith("http://") or rest.startswith("https://"):
                base_url, _, username = rest.rpartition(":")
                yield from list_gitea_user_repos(username, config, base_url=base_url)
            else:
                yield from list_gitea_user_repos(rest, config)

        elif target.startswith("server:"):
            server_url = target[len("server:"):]
            yield from list_gitlab_server_repos(config, base_url=server_url)

        else:
            # plain URL — validate and yield as-is
            url = validate_git_url(target)
            if url:
                yield url
