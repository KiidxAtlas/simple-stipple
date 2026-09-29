"""Repository auto sync against real git repositories (bare remote + two clones)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal

from simple_stipple.app import tasks
from simple_stipple.app.tasks import AutoSyncController
from simple_stipple.platform.git import GitRunner, find_git, sync_repository
from simple_stipple.platform.settings import _migrate_settings

GIT = find_git()
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")


def _git(repo: Path, *args: str) -> str:
    assert GIT is not None
    return subprocess.run(
        [GIT, *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _clone(remote: Path, target: Path) -> Path:
    _git(remote.parent, "clone", "-q", str(remote), str(target))
    for key, value in (
        ("user.name", "Sync Test"),
        ("user.email", "sync@example.com"),
        ("commit.gpgsign", "false"),
        ("pull.rebase", "false"),
    ):
        _git(target, "config", key, value)
    return target


@pytest.fixture
def machines(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A bare remote plus two clones standing in for two computers."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")
    seed = _clone(remote, tmp_path / "seed")
    (seed / "README.md").write_text("shared\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "push", "-q", "-u", "origin", "main")
    return remote, _clone(remote, tmp_path / "laptop"), _clone(remote, tmp_path / "desktop")


def _sync(repo: Path, *, commit: bool = True):
    assert GIT is not None
    return sync_repository(GitRunner(GIT, repo), commit_message="Auto-sync" if commit else None)


def test_new_local_file_reaches_the_other_machine(machines) -> None:
    _remote, laptop, desktop = machines
    (laptop / "pattern.dxf").write_text("0\nEOF\n", encoding="utf-8")

    assert _sync(laptop).state == "synced"
    assert _sync(desktop, commit=False).state == "synced"

    assert (desktop / "pattern.dxf").read_text(encoding="utf-8") == "0\nEOF\n"
    assert _sync(desktop, commit=False).state == "up_to_date"


def test_both_machines_editing_different_files_converge(machines) -> None:
    _remote, laptop, desktop = machines
    (laptop / "a.txt").write_text("from laptop\n", encoding="utf-8")
    (desktop / "b.txt").write_text("from desktop\n", encoding="utf-8")

    assert _sync(laptop).state == "synced"
    assert _sync(desktop).state == "synced"  # commits, rebases onto laptop's push, pushes
    assert _sync(laptop, commit=False).state == "synced"

    for machine in (laptop, desktop):
        assert (machine / "a.txt").exists() and (machine / "b.txt").exists()
    assert _git(laptop, "rev-parse", "HEAD") == _git(desktop, "rev-parse", "HEAD")


def test_pending_local_edits_are_left_alone_by_a_remote_check(machines) -> None:
    _remote, laptop, _desktop = machines
    (laptop / "draft.txt").write_text("half done\n", encoding="utf-8")
    head = _git(laptop, "rev-parse", "HEAD")

    assert _sync(laptop, commit=False).state == "local_changes"
    assert _git(laptop, "rev-parse", "HEAD") == head
    assert (laptop / "draft.txt").exists()


def test_conflicting_edits_abort_cleanly_without_losing_work(machines) -> None:
    _remote, laptop, desktop = machines
    (laptop / "README.md").write_text("laptop version\n", encoding="utf-8")
    (desktop / "README.md").write_text("desktop version\n", encoding="utf-8")
    assert _sync(laptop).state == "synced"

    result = _sync(desktop)

    assert result.state == "conflict"
    assert "Nothing was changed" in result.detail
    # The desktop's own commit is intact and no rebase is left half-applied.
    assert (desktop / "README.md").read_text(encoding="utf-8") == "desktop version\n"
    assert _git(desktop, "status", "--porcelain") == ""
    assert not (desktop / ".git" / "rebase-merge").exists()


def test_branch_without_upstream_is_reported_not_guessed(machines) -> None:
    _remote, laptop, _desktop = machines
    _git(laptop, "switch", "-q", "-c", "local-only")
    (laptop / "x.txt").write_text("x\n", encoding="utf-8")

    assert _sync(laptop).state == "no_upstream"


def test_legacy_auto_commit_setting_carries_over() -> None:
    assert _migrate_settings({"auto_commit_push": True})["auto_sync_repo"] is True
    assert "auto_commit_push" not in _migrate_settings({"auto_commit_push": False})


class _RepoPageStub(QObject):
    autoSyncToggled = Signal(bool)

    def __init__(self, repo: Path | None) -> None:
        super().__init__()
        self.repo = repo

    def current_repo(self) -> Path | None:
        return self.repo

    def is_git_busy(self) -> bool:
        return False


class _AppStub(QObject):
    def __init__(self, repo: Path | None) -> None:
        super().__init__()
        self._settings: dict = {}
        self._repo_page = _RepoPageStub(repo)


@pytest.fixture
def fast_controller(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tasks, "save_settings", lambda _settings: None)
    monkeypatch.setattr(AutoSyncController, "_POLL_INTERVAL_MS", 50)
    monkeypatch.setattr(AutoSyncController, "_QUIET_PERIOD_MS", 50)
    monkeypatch.setattr(AutoSyncController, "_REMOTE_INTERVAL_MS", 200)
    controllers: list[AutoSyncController] = []

    def make(repo: Path | None) -> AutoSyncController:
        controller = AutoSyncController(_AppStub(repo))  # type: ignore[arg-type]
        controllers.append(controller)
        return controller

    yield make
    for controller in controllers:
        controller.shutdown()


def test_auto_sync_pushes_local_edits_and_pulls_remote_ones(
    qtbot, machines, fast_controller
) -> None:
    remote, laptop, desktop = machines
    controller = fast_controller(laptop)
    statuses: list[str] = []
    runs: list[list[tuple[list[str], bool, str]]] = []
    controller.statusChanged.connect(statuses.append)
    controller.logged.connect(runs.append)
    controller.set_enabled(True)
    qtbot.waitUntil(lambda: any(s.startswith("Up to date") for s in statuses), timeout=10_000)
    assert runs == []  # a check that changed nothing stays out of the Git output

    (laptop / "new-tile.dxf").write_text("tile\n", encoding="utf-8")
    qtbot.waitUntil(
        lambda: "new-tile.dxf" in _git(remote, "ls-tree", "--name-only", "main"), timeout=10_000
    )
    qtbot.waitUntil(lambda: bool(runs), timeout=5_000)
    commands = [args[0] for args, _ok, _out in runs[0]]
    assert {"add", "commit", "push"} <= set(commands)
    assert all(ok for _args, ok, _out in runs[0])

    _git(desktop, "pull", "-q")
    (desktop / "from-desktop.txt").write_text("hi\n", encoding="utf-8")
    _git(desktop, "add", "-A")
    _git(desktop, "commit", "-q", "-m", "desktop edit")
    _git(desktop, "push", "-q")
    qtbot.waitUntil(lambda: (laptop / "from-desktop.txt").exists(), timeout=10_000)

    controller.set_enabled(False)
    assert not controller._poll_timer.isActive()
    assert statuses[-1] == "Auto sync is off."


def test_auto_sync_pauses_on_conflict_until_turned_back_on(
    qtbot, machines, fast_controller
) -> None:
    _remote, laptop, desktop = machines
    (desktop / "README.md").write_text("desktop version\n", encoding="utf-8")
    _git(desktop, "commit", "-q", "-am", "desktop edit")
    _git(desktop, "push", "-q")
    (laptop / "README.md").write_text("laptop version\n", encoding="utf-8")
    _git(laptop, "commit", "-q", "-am", "laptop edit")

    controller = fast_controller(laptop)
    statuses: list[str] = []
    runs: list[list[tuple[list[str], bool, str]]] = []
    controller.statusChanged.connect(statuses.append)
    controller.logged.connect(runs.append)
    controller.set_enabled(True)

    qtbot.waitUntil(lambda: controller._paused, timeout=10_000)
    assert not controller._poll_timer.isActive()
    assert "conflict" in statuses[-1]
    assert (laptop / "README.md").read_text(encoding="utf-8") == "laptop version\n"
    (conflict_run,) = runs
    assert ["rebase", "--abort"] in [args for args, _ok, _out in conflict_run]

    controller.set_enabled(False)
    controller.set_enabled(True)
    assert not controller._paused
    assert controller._poll_timer.isActive()
