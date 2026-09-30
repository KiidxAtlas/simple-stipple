from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea

from simple_stipple.features.repository.page import (
    RepoPage,
    branch_switch_commands,
    git_failure_summary,
    parse_git_command,
)
from simple_stipple.platform.git import find_git


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            "remote: Invalid username or password.\n"
            "fatal: Authentication failed for 'https://example.com/repo.git/'",
            "authentication failed",
        ),
        (
            "There is no tracking information for the current branch.\n"
            "Please specify which branch you want to merge with.",
            "no upstream branch",
        ),
        ("CONFLICT (content): Merge conflict in part.dxf", "conflicting changes"),
        (
            " ! [rejected]        main -> main (fetch first)\n"
            "error: failed to push some refs to 'origin'",
            "the remote has newer commits",
        ),
        (
            " ! [remote rejected] main -> main (cannot lock ref refs/heads/main: is at b5 but expected 83)",
            "branch ref changed concurrently",
        ),
        (
            "fatal: unable to access 'https://example.com/': Could not resolve host: example.com",
            "network error",
        ),
        ("\nTimed out", "timed out"),
    ],
)
def test_failed_git_step_is_named_by_its_cause_and_key_line(output: str, expected: str) -> None:
    summary = git_failure_summary([(["fetch"], True, ""), (["push"], False, output)])

    assert summary.startswith(expected)
    assert "(" in summary  # the quoted git line follows the cause


def test_unrecognised_failure_quotes_the_fatal_line() -> None:
    output = "hint: something\nfatal: bad object HEAD.\nhint: more"

    assert git_failure_summary([(["pull"], False, output)]) == "fatal: bad object HEAD"


def test_command_parser_preserves_upstream_syntax_and_quoted_args() -> None:
    assert parse_git_command("git rev-parse --abbrev-ref --symbolic-full-name @{u}") == [
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
    ]
    assert parse_git_command('commit -m "shared library update"') == [
        "commit",
        "-m",
        "shared library update",
    ]
    with pytest.raises(ValueError, match="Invalid quoting"):
        parse_git_command('commit -m "unfinished')


def test_branch_switch_plan_tracks_remote_or_creates_branch() -> None:
    assert branch_switch_commands("main", {"main"}, {"origin/main"}) == [["switch", "main"]]
    assert branch_switch_commands("origin/main", {"main"}, {"origin/main"}) == [
        ["switch", "main"],
        ["branch", "--set-upstream-to", "origin/main", "main"],
    ]
    assert branch_switch_commands("feature/ui", set(), {"origin/feature/ui"}) == [
        ["switch", "--track", "-c", "feature/ui", "origin/feature/ui"]
    ]
    assert branch_switch_commands("feature/new", set(), set()) == [
        ["check-ref-format", "--branch", "feature/new"],
        ["switch", "--create", "feature/new"],
    ]


def test_ambiguous_remote_branch_requires_explicit_remote() -> None:
    with pytest.raises(ValueError, match="Several remotes"):
        branch_switch_commands("main", set(), {"origin/main", "upstream/main"})


def _run_git(git: str, cwd: Path, *args: str) -> str:
    return subprocess.run(
        [git, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout.strip()


@pytest.mark.skipif(find_git() is None, reason="git is not installed")
def test_repository_page_recovers_detached_head_and_runs_upstream_command(
    qtbot, tmp_path: Path
) -> None:
    git = find_git()
    assert git is not None
    remote = tmp_path / "remote.git"
    repo = tmp_path / "workspace"
    repo.mkdir()
    _run_git(git, tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    _run_git(git, repo, "init", "--initial-branch=main")
    _run_git(git, repo, "config", "user.name", "Repository Test")
    _run_git(git, repo, "config", "user.email", "repository@example.com")
    (repo / "README.md").write_text("branch test\n", encoding="utf-8")
    _run_git(git, repo, "add", "-A")
    _run_git(git, repo, "commit", "-m", "seed")
    _run_git(git, repo, "remote", "add", "origin", str(remote))
    _run_git(git, repo, "push", "--set-upstream", "origin", "main")
    _run_git(git, repo, "switch", "--detach", "HEAD")
    _run_git(git, repo, "branch", "-D", "main")

    page = RepoPage(settings={"repo_dir": str(repo)})
    qtbot.addWidget(page)
    page.show()
    page._splitter._set_drawer_open(True)
    assert page._current_branch_label.text().startswith("Detached HEAD")
    assert "origin/main" in [
        page._branch_combo.itemText(index) for index in range(page._branch_combo.count())
    ]

    assert page._switch_branch_btn.isEnabled()
    assert page._switch_branch_btn.isVisible()
    branch_scroll = page._left_panel.findChild(QScrollArea)
    assert branch_scroll is not None
    branch_scroll.ensureWidgetVisible(page._switch_branch_btn)
    qtbot.wait(25)
    page._branch_combo.setEditText("origin/main")
    qtbot.mouseClick(page._switch_branch_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(page._branch_action_status.text()), timeout=10_000)
    assert page._branch_action_status.text() == "Using branch origin/main.", page._log.toPlainText()
    assert not page._git_busy
    assert (
        _run_git(git, repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        == "origin/main"
    )

    page._git_command_edit.setText("git rev-parse --abbrev-ref --symbolic-full-name @{u}")
    qtbot.mouseClick(page._run_command_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page._git_busy, timeout=10_000)
    assert "origin/main" in page._log.toPlainText()
    assert page._command_status.text() == "Git command completed."
    page.shutdown()


def test_cancelled_run_is_reported_without_git_noise() -> None:
    assert git_failure_summary([(["pull"], False, "Receiving objects\nCancelled")]) == "cancelled"
