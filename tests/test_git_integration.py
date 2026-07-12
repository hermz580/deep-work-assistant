from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from deep_work_assistant.git_integration import GitRepoWatcher, GitStatus, format_git_status


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit."""
    repo = tmp_path / 'test-repo'
    repo.mkdir(parents=True)
    subprocess.run(['git', 'init'], cwd=str(repo), capture_output=True, timeout=10)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=str(repo), capture_output=True, timeout=10)
    subprocess.run(['git', 'config', 'user.name', 'Tester'], cwd=str(repo), capture_output=True, timeout=10)
    # Initial commit
    (repo / 'README.md').write_text('# Test')
    subprocess.run(['git', 'add', '-A'], cwd=str(repo), capture_output=True, timeout=10)
    subprocess.run(['git', 'commit', '-m', 'initial commit'], cwd=str(repo), capture_output=True, timeout=10)
    return repo


class TestGitRepoWatcher:
    def test_finds_git_root(self, git_repo):
        """find_git_root should locate the .git directory."""
        root = GitRepoWatcher.find_git_root(git_repo)
        assert root is not None
        assert root == git_repo

    def test_returns_none_for_non_repo(self, tmp_path):
        """find_git_root should return None for non-git directories."""
        root = GitRepoWatcher.find_git_root(tmp_path / 'nonexistent')
        assert root is None

    def test_clean_repo_status(self, git_repo):
        """A clean repo should show no dirty files."""
        watcher = GitRepoWatcher(git_repo)
        status = watcher.check_status()
        assert status.is_clean
        assert status.dirty_file_count == 0

    def test_dirty_repo_detects_changes(self, git_repo):
        """Modified files should be detected."""
        (git_repo / 'README.md').write_text('# Modified')
        watcher = GitRepoWatcher(git_repo)
        status = watcher.check_status()
        assert not status.is_clean
        assert status.dirty_file_count >= 1
        assert status.has_uncommitted

    def test_untracked_files_detected(self, git_repo):
        """New untracked files should be detected."""
        (git_repo / 'new_file.py').write_text('print("hello")')
        watcher = GitRepoWatcher(git_repo)
        status = watcher.check_status()
        assert status.has_untracked
        assert status.dirty_file_count >= 1

    def test_branch_name(self, git_repo):
        """Branch name should be reported correctly."""
        watcher = GitRepoWatcher(git_repo)
        status = watcher.check_status()
        assert status.branch == 'master' or status.branch == 'main'

    def test_last_commit_info(self, git_repo):
        """Last commit message and author should be available."""
        watcher = GitRepoWatcher(git_repo)
        status = watcher.check_status()
        assert 'initial' in status.last_commit_message
        assert status.last_commit_author == 'Tester'

    def test_suggest_message_for_python_changes(self, git_repo):
        """Changed .py files should suggest a 'feat:' message."""
        (git_repo / 'app.py').write_text('def main(): pass')
        (git_repo / 'README.md').write_text('# Modified')
        watcher = GitRepoWatcher(git_repo)
        # Make git see the changes
        subprocess.run(['git', 'add', '-A'], cwd=str(git_repo), capture_output=True, timeout=10)
        msg = watcher.suggest_commit_message()
        assert 'feat' in msg or 'update' in msg

    def test_auto_commit_works(self, git_repo):
        """auto_commit should stage and commit changes."""
        (git_repo / 'app.py').write_text('def main(): pass')
        watcher = GitRepoWatcher(git_repo)
        result = watcher.auto_commit(message='test: add app.py')
        assert result is True
        # Repo should now be clean
        status = watcher.check_status()
        assert status.is_clean

    def test_auto_commit_generates_message(self, git_repo):
        """auto_commit with no message should auto-generate one."""
        (git_repo / 'feature.py').write_text('x = 1')
        watcher = GitRepoWatcher(git_repo)
        result = watcher.auto_commit()
        assert result is True
        status = watcher.check_status()
        assert status.is_clean


class TestGitStatus:
    def test_status_stale_detection(self, git_repo):
        """is_commit_stale should be True when changes exist and threshold is met."""
        (git_repo / 'file.txt').write_text('change')
        watcher = GitRepoWatcher(git_repo, stale_threshold_minutes=1)
        status = watcher.check_status()
        # With threshold=1, minutes_since_last_commit=0, so NOT stale
        assert status.is_commit_stale is False
        assert status.dirty_file_count >= 1

    def test_format_git_status(self):
        """format_git_status should handle empty status."""
        status = GitStatus()
        output = format_git_status(status)
        assert 'no git' in output.lower() or '(no git' in output.lower()
