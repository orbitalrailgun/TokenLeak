"""Tests for the safe clone module."""

import os
import stat
from pathlib import Path

import pytest

from tokenleak.scanner.clone import _remove_exec_bits, repo_size_mb


class TestRemoveExecBits:
    def test_exec_bits_removed(self, tmp_path):
        f = tmp_path / "script.sh"
        f.write_text("#!/bin/bash\necho hi")
        f.chmod(0o755)

        _remove_exec_bits(tmp_path)

        mode = f.stat().st_mode
        assert not (mode & stat.S_IXUSR)
        assert not (mode & stat.S_IXGRP)
        assert not (mode & stat.S_IXOTH)

    def test_read_write_bits_preserved(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello")
        f.chmod(0o644)

        _remove_exec_bits(tmp_path)

        mode = f.stat().st_mode
        assert mode & stat.S_IRUSR
        assert mode & stat.S_IWUSR


class TestRepoSizeMb:
    def test_returns_float(self, git_repo):
        size = repo_size_mb(git_repo)
        assert isinstance(size, float)
        assert size >= 0.0

    def test_nonexistent_path_returns_zero(self, tmp_path):
        size = repo_size_mb(tmp_path / "nonexistent")
        assert size == 0.0
