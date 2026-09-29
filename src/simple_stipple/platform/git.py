"""Git executable discovery, subprocess plumbing, and repository sync."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

GIT_NOT_FOUND_MESSAGE = (
    "Git was not found. Install Git, or add it to PATH for this user account, "
    "then restart Simple Stipple."
)
# A windowed Windows app that launches console programs flashes a console
# window for every call unless told not to; background sync polls git often.
# The flag only exists on Windows; 0 is the no-op value everywhere else.
NO_CONSOLE_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_git() -> str | None:
    """Locate the git executable.

    Apps launched from Finder/Dock/Start menu do not inherit the shell PATH a
    terminal gets, so a git that works in the terminal can be invisible here.
    Fall back to the standard install locations before giving up.
    """
    found = shutil.which("git")
    if found:
        return found
    if os.name == "nt":
        candidates = [
            Path(base) / "Git" / "cmd" / "git.exe"
            for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"))
            if base
        ]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Programs" / "Git" / "cmd" / "git.exe")
    else:
        candidates = [
            Path(p) for p in ("/opt/homebrew/bin/git", "/usr/local/bin/git", "/usr/bin/git")
        ]
    return next((str(c) for c in candidates if c.is_file()), None)


@dataclass(frozen=True)
class GitStep:
    args: tuple[str, ...]
    ok: bool
    output: str


@dataclass
class GitRunner:
    """Run git non-interactively in one repository, recording every step.

    Background callers must never block on a credential prompt nobody can
    see, so terminal prompting is disabled; a missing credential fails fast.
    """

    git: str
    repo: Path
    timeout_s: float = 60.0
    on_process: Callable[[subprocess.Popen[str] | None], None] = lambda _proc: None
    steps: list[GitStep] = field(default_factory=list)

    def run(self, *args: str) -> GitStep:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            proc = subprocess.Popen(
                [self.git, *args],
                cwd=str(self.repo),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=NO_CONSOLE_WINDOW,
            )
        except OSError as exc:
            step = GitStep(args, False, str(exc))
            self.steps.append(step)
            return step
        self.on_process(proc)
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timed_out = True
        finally:
            self.on_process(None)
        output = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
        if timed_out:
            output = f"{output}\nTimed out".strip()
        step = GitStep(args, proc.returncode == 0 and not timed_out, output)
        self.steps.append(step)
        return step


SyncState = Literal["synced", "up_to_date", "local_changes", "no_upstream", "conflict", "error"]


@dataclass(frozen=True)
class SyncResult:
    state: SyncState
    detail: str = ""


def _commit_count(runner: GitRunner, revision_range: str) -> int | None:
    step = runner.run("rev-list", "--count", revision_range)
    if not step.ok:
        return None
    try:
        return int(step.output.splitlines()[0])
    except (IndexError, ValueError):
        return None


def sync_repository(runner: GitRunner, *, commit_message: str | None) -> SyncResult:
    """Bring a repository and its upstream to the same state.

    With ``commit_message``, uncommitted work (including new files) is
    committed first. Without it, uncommitted work means an edit is still in
    progress, so nothing is touched. Remote commits are integrated by rebasing
    local commits on top; a conflicting rebase is aborted so the repository is
    left exactly as it was, and reported as ``"conflict"``.
    """
    status = runner.run("status", "--porcelain")
    if not status.ok:
        return SyncResult("error", status.output)
    if status.output:
        if commit_message is None:
            return SyncResult("local_changes")
        for step in (runner.run("add", "-A"), runner.run("commit", "-m", commit_message)):
            if not step.ok:
                return SyncResult("error", step.output)

    upstream = runner.run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not upstream.ok:
        return SyncResult(
            "no_upstream",
            "The current branch has no remote branch to sync with. Push it once "
            "from the Repository page to set one up.",
        )
    fetch = runner.run("fetch", "--prune")
    if not fetch.ok:
        return SyncResult("error", fetch.output)
    behind = _commit_count(runner, "HEAD..@{u}")
    ahead = _commit_count(runner, "@{u}..HEAD")
    if behind is None or ahead is None:
        return SyncResult("error", runner.steps[-1].output)

    if behind:
        rebase = runner.run("rebase", "@{u}")
        if not rebase.ok:
            runner.run("rebase", "--abort")
            return SyncResult(
                "conflict",
                "Remote changes conflict with local commits. Nothing was changed; "
                "resolve it from the Repository page (Pull), then turn Auto sync back on.",
            )
    if ahead:
        push = runner.run("push")
        if not push.ok:
            return SyncResult("error", push.output)
    if behind or ahead:
        return SyncResult("synced", f"pulled {behind}, pushed {ahead}")
    return SyncResult("up_to_date")
