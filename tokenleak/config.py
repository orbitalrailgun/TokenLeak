"""Configuration loaded from environment variables and .env file."""

import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes")


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


@dataclass
class Config:
    # Database
    db_type: str = field(default_factory=lambda: os.getenv("TOKENLEAK_DB_TYPE", "sqlite"))
    db_path: str = field(default_factory=lambda: os.getenv("TOKENLEAK_DB_PATH", "tokenleak.db"))
    db_host: str = field(default_factory=lambda: os.getenv("TOKENLEAK_DB_HOST", "localhost"))
    db_port: int = field(default_factory=lambda: _int("TOKENLEAK_DB_PORT", 5432))
    db_name: str = field(default_factory=lambda: os.getenv("TOKENLEAK_DB_NAME", "tokenleak"))
    db_user: str = field(default_factory=lambda: os.getenv("TOKENLEAK_DB_USER", "tokenleak"))
    db_password: str = field(default_factory=lambda: os.getenv("TOKENLEAK_DB_PASSWORD", ""))

    # AI
    ai_provider: str = field(default_factory=lambda: os.getenv("TOKENLEAK_AI_PROVIDER", "openai"))
    ai_api_key: str = field(default_factory=lambda: os.getenv("TOKENLEAK_AI_API_KEY", ""))
    ai_api_url: Optional[str] = field(default_factory=lambda: os.getenv("TOKENLEAK_AI_API_URL") or None)
    ai_model: str = field(default_factory=lambda: os.getenv("TOKENLEAK_AI_MODEL", "gpt-4o"))
    ai_context_window: int = field(default_factory=lambda: _int("TOKENLEAK_AI_CONTEXT_WINDOW", 262144))
    ai_max_iterations: int = field(default_factory=lambda: _int("TOKENLEAK_AI_MAX_ITERATIONS", 50))
    ocr_model: str = field(default_factory=lambda: os.getenv("TOKENLEAK_OCR_MODEL", ""))

    # Scanner
    prefilter_enabled: bool = field(default_factory=lambda: _bool("TOKENLEAK_PREFILTER_ENABLED", True))
    scan_all_branches: bool = field(default_factory=lambda: _bool("TOKENLEAK_SCAN_ALL_BRANCHES", True))
    max_repo_size_mb: int = field(default_factory=lambda: _int("TOKENLEAK_MAX_REPO_SIZE_MB", 2048))
    max_file_size_mb: int = field(default_factory=lambda: _int("TOKENLEAK_MAX_FILE_SIZE_MB", 10))
    clone_timeout_sec: int = field(default_factory=lambda: _int("TOKENLEAK_CLONE_TIMEOUT_SEC", 300))
    clone_dir: str = field(default_factory=lambda: os.getenv("TOKENLEAK_CLONE_DIR", "/tmp/tokenleak_clones"))
    parallel_repos: int = field(default_factory=lambda: _int("TOKENLEAK_PARALLEL_REPOS", 1))

    # Git Providers
    github_token: Optional[str] = field(default_factory=lambda: os.getenv("TOKENLEAK_GITHUB_TOKEN") or None)
    gitlab_token: Optional[str] = field(default_factory=lambda: os.getenv("TOKENLEAK_GITLAB_TOKEN") or None)
    gitlab_url: str = field(default_factory=lambda: os.getenv("TOKENLEAK_GITLAB_URL", "https://gitlab.com"))
    gitea_token: Optional[str] = field(default_factory=lambda: os.getenv("TOKENLEAK_GITEA_TOKEN") or None)
    gitea_url: Optional[str] = field(default_factory=lambda: os.getenv("TOKENLEAK_GITEA_URL") or None)

    # Input
    repos_list_path: str = field(default_factory=lambda: os.getenv("TOKENLEAK_REPOS_LIST_PATH", "repos.txt"))
    config_repo_url: Optional[str] = field(default_factory=lambda: os.getenv("TOKENLEAK_CONFIG_REPO_URL") or None)
    config_repo_token: Optional[str] = field(default_factory=lambda: os.getenv("TOKENLEAK_CONFIG_REPO_TOKEN") or None)
    agent_md_path: str = field(default_factory=lambda: os.getenv("TOKENLEAK_AGENT_MD_PATH", "agent.md"))

    # Mattermost
    mattermost_url: Optional[str] = field(default_factory=lambda: os.getenv("TOKENLEAK_MATTERMOST_URL") or None)
    mattermost_token: Optional[str] = field(default_factory=lambda: os.getenv("TOKENLEAK_MATTERMOST_TOKEN") or None)
    mattermost_channel: str = field(default_factory=lambda: os.getenv("TOKENLEAK_MATTERMOST_CHANNEL", "tokenleak-alerts"))

    # Syslog
    syslog_enabled: bool = field(default_factory=lambda: _bool("TOKENLEAK_SYSLOG_ENABLED", True))
    syslog_host: Optional[str] = field(default_factory=lambda: os.getenv("TOKENLEAK_SYSLOG_HOST") or None)
    syslog_port: int = field(default_factory=lambda: _int("TOKENLEAK_SYSLOG_PORT", 514))

    # Lock
    lock_file: str = field(default_factory=lambda: os.getenv("TOKENLEAK_LOCK_FILE", "/tmp/tokenleak.pid"))

    # Log file (empty string = disabled)
    log_file: str = field(default_factory=lambda: os.getenv("TOKENLEAK_LOG_FILE", "tokenleak.log"))

    # Runtime (overridden by CLI flags)
    animation_enabled: bool = True
    report_output: Optional[str] = None  # None = no report; "-" = stdout; path = file


_instance: Optional[Config] = None


def get_config() -> Config:
    global _instance
    if _instance is None:
        _instance = Config()
    return _instance
