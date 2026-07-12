"""Git repository awareness — commit reminders, status checks, and auto-commit.

Integrates with the Deep Work Assistant's live loop and CLI to detect when
a user has uncommitted changes lingering too long and optionally auto-commit.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Constants ─────────────────────────────────────────────────────────────────

STALE_THRESHOLD_MINUTES = 30  # Default: remind after 30 min of uncommitted work


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class GitStatus:
    """Snapshot of a git repository's health at a point in time."""

    repo_path: str = ''
    branch: str = ''
    has_uncommitted: bool = False
    has_untracked: bool = False
    dirty_file_count: int = 0
    minutes_since_last_commit: int = 0
    last_commit_message: str = ''
    last_commit_author: str = ''
    is_commit_stale: bool = False
    checked_at: float = 0.0

    @property
    def is_clean(self) -> bool:
        return not self.has_uncommitted and not self.has_untracked

    def to_dict(self) -> dict[str, Any]:
        return {
            'repo_path': self.repo_path,
            'branch': self.branch,
            'has_uncommitted': self.has_uncommitted,
            'has_untracked': self.has_untracked,
            'dirty_file_count': self.dirty_file_count,
            'minutes_since_last_commit': self.minutes_since_last_commit,
            'last_commit_message': self.last_commit_message,
            'last_commit_author': self.last_commit_author,
            'is_commit_stale': self.is_commit_stale,
            'is_clean': self.is_clean,
            'checked_at': self.checked_at,
        }


# ── GitRepoWatcher ────────────────────────────────────────────────────────────

class GitRepoWatcher:
    """Monitors a git repository for uncommitted changes and can auto-commit.

    Auto-detects the nearest git repo from a given path (or the current working
    directory). Provides status checks, stale-change detection, commit message
    suggestions, and automated committing.
    """

    def __init__(
        self,
        repo_path: str | Path | None = None,
        stale_threshold_minutes: int = STALE_THRESHOLD_MINUTES,
    ) -> None:
        self._stale_threshold = max(1, stale_threshold_minutes)
        self._last_status: GitStatus | None = None

        resolved = self.find_git_root(repo_path)
        self._repo_path: Path | None = resolved

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def has_repo(self) -> bool:
        """True when a git repository was found."""
        return self._repo_path is not None

    @property
    def repo_path(self) -> Path | None:
        return self._repo_path

    def check_status(self) -> GitStatus:
        """Run git commands and return a full GitStatus snapshot.

        Returns an empty status (all defaults) when no repo is found or when
        git commands fail.
        """
        if not self._repo_path:
            status = GitStatus(checked_at=time.time())
            self._last_status = status
            return status

        branch = self._run_git('branch', '--show-current') or ''
        porcelain = self._run_git('status', '--porcelain') or ''

        dirty_lines = [l for l in porcelain.splitlines() if l.strip()]
        has_uncommitted = any(
            l.startswith(' M') or l.startswith('MM') or l.startswith('A ') or l.startswith('M ') or l.startswith(' D') or l.startswith('D ')
            for l in dirty_lines
        )
        has_untracked = any(l.startswith('??') for l in dirty_lines)
        dirty_file_count = len(dirty_lines)

        # Last commit info
        last_commit_raw = self._run_git(
            'log', '-1', '--format=%ct|%s|%an', '--'
        )
        minutes_since_last_commit = 0
        last_commit_message = ''
        last_commit_author = ''

        if last_commit_raw:
            parts = last_commit_raw.strip().split('|', 2)
            if len(parts) == 3:
                try:
                    commit_ts = int(parts[0])
                    minutes_since_last_commit = int((time.time() - commit_ts) / 60)
                except (ValueError, TypeError):
                    pass
                last_commit_message = parts[1]
                last_commit_author = parts[2]

        is_stale = (
            dirty_file_count > 0
            and minutes_since_last_commit >= self._stale_threshold
        )

        status = GitStatus(
            repo_path=str(self._repo_path),
            branch=branch,
            has_uncommitted=has_uncommitted,
            has_untracked=has_untracked,
            dirty_file_count=dirty_file_count,
            minutes_since_last_commit=minutes_since_last_commit,
            last_commit_message=last_commit_message,
            last_commit_author=last_commit_author,
            is_commit_stale=is_stale,
            checked_at=time.time(),
        )
        self._last_status = status
        return status

    def suggest_commit_message(self) -> str:
        """Generate a sensible commit message from the current working tree.

        Scans changed file extensions and paths to infer the type of work.
        Falls back to a generic message when nothing meaningful can be inferred.
        """
        if not self._repo_path:
            return 'chore: update'

        porcelain = self._run_git('status', '--porcelain') or ''
        lines = [l.strip() for l in porcelain.splitlines() if l.strip()]

        if not lines:
            return 'chore: update'

        changed_files: list[str] = []
        for line in lines:
            filepath = line[3:] if len(line) > 3 else line  # strip status chars
            changed_files.append(filepath)

        # Detect primary change type from file names
        is_feature = any(
            f.endswith(('.py', '.js', '.ts', '.rs', '.go', '.java'))
            for f in changed_files
        )
        is_docs = any(
            f.endswith(('.md', '.rst', '.txt', '.adoc'))
            for f in changed_files
        )
        is_config = any(
            f.endswith(('.yaml', '.yml', '.json', '.toml', '.ini', '.cfg'))
            for f in changed_files
        )
        is_test = any(
            'test' in f.lower() for f in changed_files
        )

        # Build message prefix
        if is_test and len(changed_files) <= 3:
            return f'test: update tests ({", ".join(changed_files)})'
        if is_feature and len(changed_files) <= 3:
            return f'feat: update {", ".join(changed_files)}'
        if is_docs and len(changed_files) <= 3:
            return f'docs: update {", ".join(changed_files)}'
        if is_config and len(changed_files) <= 3:
            return f'chore: update config ({", ".join(changed_files)})'

        # Count by type for larger changesets
        parts: list[str] = []
        if is_feature:
            py_count = sum(1 for f in changed_files if f.endswith('.py'))
            parts.append(f'{py_count} source file(s)')
        if is_test:
            test_count = sum(1 for f in changed_files if 'test' in f.lower())
            parts.append(f'{test_count} test file(s)')
        if is_docs:
            parts.append('docs')
        if is_config:
            parts.append('config')
        if not parts:
            parts.append(f'{len(changed_files)} file(s)')

        return f'update: {", ".join(parts)}'

    def auto_commit(
        self,
        message: str | None = None,
        add_all: bool = True,
    ) -> bool:
        """Stage all changes and commit.

        Parameters
        ----------
        message : str | None
            Commit message.  Auto-generated when None.
        add_all : bool
            Whether to run ``git add -A`` before committing (default True).

        Returns
        -------
        bool
            True when the commit succeeded.
        """
        if not self._repo_path:
            return False

        if add_all:
            result = self._run_git_raw('add', '-A')
            if result is None or result.returncode != 0:
                return False

        msg = message or self.suggest_commit_message()
        result = self._run_git_raw('commit', '-m', msg)
        if result is None or result.returncode != 0:
            return False

        # Refresh cached status
        self.check_status()
        return True

    def last_status(self) -> GitStatus | None:
        """Return the most recently computed status, or None."""
        return self._last_status

    # ── Static helpers ──────────────────────────────────────────────────────

    @staticmethod
    def find_git_root(path: str | Path | None = None) -> Path | None:
        """Walk up from *path* (or CWD) looking for a ``.git`` directory.

        Returns the repository root (the directory containing ``.git``) or
        None when no git repo is found.
        """
        start = Path(path or Path.cwd()).resolve()
        for parent in [start, *start.parents]:
            if (parent / '.git').exists():
                return parent
        return None

    # ── Internal git helpers ────────────────────────────────────────────────

    def _run_git(self, *args: str) -> str | None:
        """Run a git command in the repo and return stdout, or None on failure."""
        result = self._run_git_raw(*args)
        if result is None:
            return None
        out = result.stdout.strip()
        return out if out else ''

    def _run_git_raw(self, *args: str) -> subprocess.CompletedProcess | None:
        """Run a git command and return the full CompletedProcess, or None."""
        if not self._repo_path:
            return None
        try:
            return subprocess.run(
                ['git', *args],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self._repo_path),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None


# ── Display helpers ───────────────────────────────────────────────────────────

def format_git_status(status: GitStatus) -> str:
    """Pretty-print a git status snapshot for the terminal."""
    lines: list[str] = []

    if not status.repo_path:
        return '  (no git repository detected)'

    lines.append(f'  📂 Repo : {status.repo_path}')
    lines.append(f'  🌿 Branch : {status.branch or "(detached)"}')

    if status.is_clean:
        lines.append('  ✅ Working tree is clean')
        lines.append(f'  📝 Last commit: {status.last_commit_message[:60]}')
        lines.append(f'     {status.minutes_since_last_commit}m ago by {status.last_commit_author}')
        return '\n'.join(lines)

    lines.append(f'  📝 {status.dirty_file_count} dirty file(s) — last commit {status.minutes_since_last_commit}m ago')

    if status.has_uncommitted:
        lines.append('  ⚠️  Modified files not staged')
    if status.has_untracked:
        lines.append('  🆕 Untracked files present')

    if status.is_commit_stale:
        lines.append(f'  ⏰ Changes have been sitting for {status.minutes_since_last_commit}m!')
        lines.append('  💡 Run: deep-work-assistant git commit')

    lines.append(f'  📝 Last commit: {status.last_commit_message[:60]}')
    return '\n'.join(lines)