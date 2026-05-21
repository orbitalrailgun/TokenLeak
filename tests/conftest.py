"""Shared fixtures for TokenLeak tests."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tokenleak.config import Config
from tokenleak.db.sqlite import SQLiteDB


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def config(tmp_path):
    """Minimal Config backed by a temp SQLite DB."""
    cfg = Config()
    cfg.db_type = "sqlite"
    cfg.db_path = str(tmp_path / "test.db")
    cfg.clone_dir = str(tmp_path / "clones")
    cfg.ai_provider = "openai"
    cfg.ai_api_key = "test"
    cfg.prefilter_enabled = True
    cfg.max_repo_size_mb = 2048
    cfg.max_file_size_mb = 10
    cfg.clone_timeout_sec = 60
    cfg.lock_file = str(tmp_path / "tokenleak.pid")
    return cfg


@pytest.fixture
def db(config):
    database = SQLiteDB(config)
    database.connect()
    yield database
    database.close()


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repository with a few commits for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"}

    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"],
                   check=True, capture_output=True)

    # Commit 1: innocent file
    (repo / "README.md").write_text("# Test repo")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True,
                   capture_output=True, env=env)

    # Commit 2: file with a "secret"
    (repo / ".env").write_text("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\nDB_PASSWORD=hunter2\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add config"], check=True,
                   capture_output=True, env=env)

    return repo
