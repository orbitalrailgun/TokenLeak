"""Tests for the git history walker."""

import pytest

from tokenleak.scanner.walker import (
    list_commits,
    list_changed_files,
    get_file_at_commit,
    get_file_tree,
    get_commit_log_text,
)


class TestListCommits:
    def test_returns_commits(self, git_repo):
        commits = list_commits(git_repo)
        assert len(commits) >= 2

    def test_commit_has_sha(self, git_repo):
        commits = list_commits(git_repo)
        for c in commits:
            assert len(c.sha) == 40

    def test_commit_has_message(self, git_repo):
        commits = list_commits(git_repo)
        messages = [c.message for c in commits]
        assert "init" in messages
        assert "add config" in messages


class TestListChangedFiles:
    def test_env_file_changed(self, git_repo):
        commits = list_commits(git_repo)
        # The most recent commit added .env
        latest = commits[0]
        files = list_changed_files(git_repo, latest.sha)
        paths = [f.path for f in files]
        assert ".env" in paths


class TestGetFileAtCommit:
    def test_reads_env_file(self, git_repo):
        commits = list_commits(git_repo)
        latest = commits[0]
        content = get_file_at_commit(git_repo, latest.sha, ".env")
        assert content is not None
        assert "AWS_ACCESS_KEY_ID" in content

    def test_returns_none_for_missing(self, git_repo):
        commits = list_commits(git_repo)
        content = get_file_at_commit(git_repo, commits[0].sha, "nonexistent.txt")
        assert content is None


class TestGetFileTree:
    def test_returns_string(self, git_repo):
        tree = get_file_tree(git_repo)
        assert isinstance(tree, str)
        assert ".env" in tree


class TestGetCommitLogText:
    def test_returns_string(self, git_repo):
        log = get_commit_log_text(git_repo)
        assert isinstance(log, str)
        assert len(log) > 0
