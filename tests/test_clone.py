"""Tests for the safe clone module."""

import os
import stat
from pathlib import Path

import pytest

from tokenleak.scanner.clone import _remove_exec_bits, repo_size_mb


class TestRemoveExecBits:
    def test_exec_bits_removed_from_files(self, tmp_path):
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

    def test_directory_execute_bit_preserved(self, tmp_path):
        # Directories must keep the execute (traverse) bit so their contents
        # remain accessible. Without it, stat() on files inside fails with EACCES.
        subdir = tmp_path / "subdir"
        subdir.mkdir(mode=0o755)
        (subdir / "file.py").write_text("x = 1")

        _remove_exec_bits(tmp_path)

        dir_mode = subdir.stat().st_mode
        assert dir_mode & stat.S_IXUSR, "directory must keep owner execute (traverse) bit"

    def test_nested_files_accessible_after_strip(self, tmp_path):
        # End-to-end: files in subdirectories must still be readable after strip.
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True, mode=0o755)
        secret = subdir / "secret.txt"
        secret.write_text("hunter2")
        secret.chmod(0o755)

        _remove_exec_bits(tmp_path)

        assert secret.read_text() == "hunter2"  # would raise EACCES before the fix


class TestRepoSizeMb:
    def test_returns_float(self, git_repo):
        size = repo_size_mb(git_repo)
        assert isinstance(size, float)
        assert size >= 0.0

    def test_nonexistent_path_returns_zero(self, tmp_path):
        size = repo_size_mb(tmp_path / "nonexistent")
        assert size == 0.0
