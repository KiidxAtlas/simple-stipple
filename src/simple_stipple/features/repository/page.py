"""Repository synchronization workflow UI."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
from datetime import datetime
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.features.base import BasePage
from simple_stipple.platform.git import (
    GIT_NOT_FOUND_MESSAGE,
    NO_CONSOLE_WINDOW,
    GitRunner,
    find_git,
)
from simple_stipple.platform.settings import save_settings
from simple_stipple.ui.components.feedback import show_error
from simple_stipple.ui.components.focus import blocked_signals
from simple_stipple.ui.components.layout import (
    content_splitter,
    sidebar_panel,
    surface_frame,
)
from simple_stipple.ui.components.workflow import set_status_label
from simple_stipple.ui.dialogs.files import reveal_label
from simple_stipple.ui.style import STATUS_ERR, STATUS_NEUTRAL, STATUS_OK, STATUS_WARN

# Local git probes (config, status) run on the GUI thread; the first git
# launch on a fresh account can be slow (macOS xcrun shim, Windows AV scans),
# so keep this generous enough to not misreport a working install.
_GIT_PROBE_TIMEOUT_S = 15
_GIT_STEP_TIMEOUT_S = 30

# (output fragments, plain-language cause) checked in order against a failed
# git step's output; the first match names the problem in the step status.
_GIT_FAILURE_CAUSES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("cancelled",), "cancelled"),
    (("timed out",), f"timed out after {_GIT_STEP_TIMEOUT_S} s — check the network connection"),
    (
        (
            "authentication failed",
            "permission denied (publickey",
            "could not read username",
            "invalid username or password",
            "access denied",
            "the requested url returned error: 403",
        ),
        "authentication failed — check your Git credentials",
    ),
    (
        ("no configured push destination", "does not appear to be a git repository"),
        "no remote configured — add one with git remote add origin URL",
    ),
    (
        ("no upstream", "no tracking information"),
        "no upstream branch — set one with git push -u origin BRANCH",
    ),
    (
        ("conflict", "unmerged", "would be overwritten"),
        "conflicting changes — resolve them, then commit",
    ),
    (
        ("cannot lock ref",),
        "branch ref changed concurrently — fetch the latest branch, integrate it, then retry",
    ),
    (
        ("[rejected]", "non-fast-forward", "fetch first"),
        "the remote has newer commits — pull first",
    ),
    (
        (
            "could not resolve host",
            "unable to access",
            "connection refused",
            "connection timed out",
            "network is unreachable",
            "could not read from remote repository",
        ),
        "network error — the remote could not be reached",
    ),
)


def git_failure_summary(results: list[tuple[list[str], bool, str]]) -> str:
    """Name why a git run failed, quoting the key line of its output."""
    failed = next((result for result in results if not result[1]), None)
    if failed is None:
        return "unknown error"
    args, _ok, output = failed
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    lowered = [line.lower() for line in lines]
    match = next(
        (
            (label, index)
            for needles, label in _GIT_FAILURE_CAUSES
            for index, line in enumerate(lowered)
            if any(needle in line for needle in needles)
        ),
        None,
    )
    if match is not None and match[0] == "cancelled":
        return "cancelled"
    if match is not None:
        key_line = lines[match[1]]
    else:
        key_line = next(
            (line for line in lines if line.lower().startswith(("fatal:", "error:", "!"))),
            lines[-1] if lines else f"git {args[0] if args else ''} failed",
        )
    key_line = key_line.rstrip(".")
    if len(key_line) > 160:
        key_line = key_line[:159] + "…"
    return key_line if match is None else f"{match[0]} ({key_line})"


_GIT_CREDENTIAL_URL = re.compile(r"(?i)(https?://)[^\s/@]+@")


def _redact_git_text(text: str) -> str:
    """Avoid echoing embedded HTTP credentials into the Git output panel."""
    return _GIT_CREDENTIAL_URL.sub(r"\1[redacted]@", text)


def parse_git_command(command: str) -> list[str]:
    """Parse a Git command line into argv; no shell or pipelines are executed."""
    try:
        args = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"Invalid quoting in Git command: {exc}") from exc
    if args and Path(args[0]).name.casefold() in {"git", "git.exe"}:
        args = args[1:]
    if not args:
        raise ValueError("Enter Git arguments, e.g. status --short --branch.")
    return args


def branch_switch_commands(
    target: str, local_branches: set[str], remote_branches: set[str]
) -> list[list[str]]:
    """Plan a safe checkout, tracking an explicitly selected remote branch."""
    target = target.strip()
    if not target or target.startswith("-"):
        raise ValueError("Enter a valid local, remote, or new branch name.")
    if target in local_branches:
        return [["switch", target]]
    if target in remote_branches:
        remote_branch = target.partition("/")[2]
        if not remote_branch:
            raise ValueError("Choose a remote branch such as origin/main.")
        if remote_branch in local_branches:
            return [
                ["switch", remote_branch],
                ["branch", "--set-upstream-to", target, remote_branch],
            ]
        return [["switch", "--track", "-c", remote_branch, target]]

    matching_remote = sorted(
        branch for branch in remote_branches if branch.partition("/")[2] == target
    )
    if len(matching_remote) > 1:
        choices = ", ".join(matching_remote)
        raise ValueError(f"Several remotes have branch {target}: choose one ({choices}).")
    if matching_remote:
        return [["switch", "--track", "-c", target, matching_remote[0]]]
    return [["check-ref-format", "--branch", target], ["switch", "--create", target]]


class RepoPage(BasePage):
    _git_op_done = Signal(object)
    autoSyncToggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent, settings)
        self._git_busy = False
        self._shutting_down = False
        self._git_cancel = threading.Event()
        self._git_process: subprocess.Popen[str] | None = None
        self._git_thread: threading.Thread | None = None
        self._git_op_done.connect(self._on_git_op_done)
        self._workflow_repo_key: str | None = None
        self._branch_repo_key: str | None = None
        self._local_branches: set[str] = set()
        self._remote_branches: set[str] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)
        # ── Left sidebar ──────────────────────────────────────────────────────
        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(12, 12, 12, 12)
        left.setSpacing(8)

        # Repository path
        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit(self._settings.get("repo_dir", ""))
        self._dir_edit.setPlaceholderText("Select a git repository folder")
        self._dir_edit.textChanged.connect(self._emit_state_changed)
        # Debounced: each keystroke otherwise stat'd the filesystem twice
        # and flipped four buttons' enabled state, visibly flickering while
        # typing a path.
        self._refresh_repo_state_timer = QTimer(self)
        self._refresh_repo_state_timer.setSingleShot(True)
        self._refresh_repo_state_timer.setInterval(250)
        self._refresh_repo_state_timer.timeout.connect(self._refresh_repo_state)
        self._dir_edit.textChanged.connect(self._refresh_repo_state_timer.start)
        dir_row.addWidget(self._dir_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_repo_dir)
        dir_row.addWidget(browse_btn)
        left.addLayout(dir_row)

        intro = QLabel("Keep a shared pattern or workspace library in sync with its Git remote.")
        intro.setProperty("role", "hint")
        intro.setWordWrap(True)
        left.addWidget(intro)

        self._repo_status = QLabel("")
        self._repo_status.setWordWrap(True)
        left.addWidget(self._repo_status)

        self._auto_sync_check = QCheckBox("Auto sync this repository")
        self._auto_sync_check.setToolTip(
            "Commit, pull, and push automatically when files change here or on the remote"
        )
        self._auto_sync_check.toggled.connect(self.autoSyncToggled)
        left.addWidget(self._auto_sync_check)
        self._auto_sync_status = QLabel()
        self._auto_sync_status.setProperty("role", "hint")
        self._auto_sync_status.setWordWrap(True)
        left.addWidget(self._auto_sync_status)
        self.set_auto_sync_status("Off")
        branch_card = surface_frame("panel")
        branch_layout = QVBoxLayout(branch_card)
        branch_layout.setContentsMargins(8, 8, 8, 8)
        branch_layout.setSpacing(6)
        branch_title = QLabel("BRANCH")
        branch_title.setProperty("role", "eyebrow")
        branch_layout.addWidget(branch_title)
        self._current_branch_label = QLabel("Choose a repository")
        self._current_branch_label.setWordWrap(True)
        branch_layout.addWidget(self._current_branch_label)
        branch_row = QHBoxLayout()
        self._branch_combo = QComboBox()
        self._branch_combo.setEditable(True)
        self._branch_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._branch_combo.setPlaceholderText("Select or type a branch")
        self._branch_combo.setToolTip(
            "Choose a local branch, a remote such as origin/main, or type a new name"
        )
        branch_row.addWidget(self._branch_combo, stretch=1)
        self._switch_branch_btn = QPushButton("Switch / create")
        self._switch_branch_btn.setToolTip(
            "Switch to the selected branch, track a remote branch, or create a branch here"
        )
        self._switch_branch_btn.clicked.connect(self._switch_branch)
        branch_row.addWidget(self._switch_branch_btn)
        branch_layout.addLayout(branch_row)
        branch_hint = QLabel(
            "Choose origin/main to create or attach a tracking branch. A new name starts at the current commit."
        )
        branch_hint.setProperty("role", "hint")
        branch_hint.setWordWrap(True)
        branch_layout.addWidget(branch_hint)
        self._branch_action_status = QLabel("")
        self._branch_action_status.setWordWrap(True)
        branch_layout.addWidget(self._branch_action_status)
        left.addWidget(branch_card)

        workflow_title = QLabel("Repository workflow")
        workflow_title.setProperty("role", "section-label")
        left.addWidget(workflow_title)
        cards_layout = QVBoxLayout()
        cards_layout.setSpacing(8)

        # Pull card
        pull_card = surface_frame("panel")
        pull_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        pull_card_layout = QVBoxLayout(pull_card)
        pull_card_layout.setContentsMargins(8, 8, 8, 8)
        pull_card_layout.setSpacing(8)
        pull_card_lbl = QLabel("1  PULL FROM REMOTE")
        pull_card_lbl.setProperty("role", "eyebrow")
        pull_card_layout.addWidget(pull_card_lbl)
        self._pull_btn = QPushButton("Pull")
        pull_hint = QLabel("Start here to bring remote changes into this workspace.")
        pull_hint.setProperty("role", "hint")
        pull_hint.setWordWrap(True)
        pull_card_layout.addWidget(pull_hint)
        self._pull_btn.setMinimumHeight(34)
        self._pull_btn.setToolTip("Pull latest changes from remote")
        self._pull_btn.clicked.connect(self._git_pull)
        pull_card_layout.addWidget(self._pull_btn)
        self._pull_status = QLabel("")
        self._pull_status.setWordWrap(True)
        pull_card_layout.addWidget(self._pull_status)
        cards_layout.addWidget(pull_card)

        # Commit card
        commit_card = surface_frame("panel")
        commit_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        commit_card_layout = QVBoxLayout(commit_card)
        commit_card_layout.setContentsMargins(8, 8, 8, 8)
        commit_card_layout.setSpacing(8)
        commit_card_lbl = QLabel("2  REVIEW AND COMMIT")
        commit_card_lbl.setProperty("role", "eyebrow")
        commit_card_layout.addWidget(commit_card_lbl)
        commit_hint = QLabel("Review your changes, enter a commit message, then commit.")
        commit_hint.setProperty("role", "hint")
        commit_hint.setWordWrap(True)
        commit_card_layout.addWidget(commit_hint)
        self._commit_msg = QLineEdit("Update project files")
        self._commit_msg.setPlaceholderText("Commit message…")
        self._commit_msg.textChanged.connect(self._emit_state_changed)
        commit_card_layout.addWidget(self._commit_msg)
        self._commit_btn = QPushButton("Commit")
        self._commit_btn.setMinimumHeight(34)
        self._commit_btn.setToolTip("Stage all changes and commit with the message above")
        self._commit_btn.clicked.connect(self._git_commit)
        commit_card_layout.addWidget(self._commit_btn)
        self._commit_status = QLabel("")
        self._commit_status.setWordWrap(True)
        commit_card_layout.addWidget(self._commit_status)
        cards_layout.addWidget(commit_card)

        # Push card
        push_card = surface_frame("panel")
        push_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        push_card_layout = QVBoxLayout(push_card)
        push_card_layout.setContentsMargins(8, 8, 8, 8)
        push_card_layout.setSpacing(8)
        push_card_lbl = QLabel("3  PUSH TO REMOTE")
        push_card_lbl.setProperty("role", "eyebrow")
        push_card_layout.addWidget(push_card_lbl)
        push_hint = QLabel("Push after committing; the branch needs a configured upstream remote.")
        push_hint.setProperty("role", "hint")
        push_hint.setWordWrap(True)
        push_card_layout.addWidget(push_hint)
        self._push_btn = QPushButton("Push")
        self._push_btn.setMinimumHeight(34)
        self._push_btn.setProperty("role", "primary")
        self._push_btn.setToolTip("Push committed changes to remote")
        self._push_btn.clicked.connect(self._git_push)
        push_card_layout.addWidget(self._push_btn)
        self._push_status = QLabel("")
        self._push_status.setWordWrap(True)
        push_card_layout.addWidget(self._push_status)
        cards_layout.addWidget(push_card)

        left.addLayout(cards_layout)

        danger_card = surface_frame("panel")
        danger_layout = QVBoxLayout(danger_card)
        danger_layout.setContentsMargins(8, 8, 8, 8)
        danger_layout.setSpacing(6)
        danger_label = QLabel("DESTRUCTIVE ACTIONS")
        danger_label.setProperty("role", "section-label")
        danger_layout.addWidget(danger_label)
        danger_hint = QLabel(
            "Reset discards local changes and replaces them with the remote state."
        )
        danger_hint.setProperty("role", "hint")
        danger_hint.setWordWrap(True)
        danger_layout.addWidget(danger_hint)
        self._force_pull_btn = QPushButton("Reset to Remote…")
        self._force_pull_btn.setMinimumHeight(34)
        self._force_pull_btn.setProperty("role", "danger")
        self._force_pull_btn.setToolTip(
            "Discard local tracked and untracked changes and match the remote tracking branch"
        )
        self._force_pull_btn.clicked.connect(self._git_force_pull)
        danger_layout.addWidget(self._force_pull_btn)
        left.addWidget(danger_card)

        # Secondary actions
        secondary = QHBoxLayout()
        secondary.setSpacing(4)
        self._status_btn = QPushButton("Status")
        self._status_btn.setToolTip("Show current repository status")
        self._status_btn.clicked.connect(self._git_status)
        secondary.addWidget(self._status_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setToolTip("Cancel the pull/commit/push in progress")
        self._cancel_btn.setProperty("role", "danger")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_git_op)
        secondary.addWidget(self._cancel_btn)
        self._open_btn = QPushButton(reveal_label())
        self._open_btn.setToolTip("Open the repository folder")
        self._open_btn.clicked.connect(self._open_repo_dir)
        secondary.addWidget(self._open_btn)
        secondary.addStretch()
        left.addLayout(secondary)
        left.addStretch()

        self._left_panel = sidebar_panel(left_w, min_width=340, max_width=430)

        # ── Right: git log ────────────────────────────────────────────────────
        right_w = surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(12, 12, 12, 12)
        right.setSpacing(8)

        log_header = QHBoxLayout()
        log_lbl = QLabel("GIT OUTPUT")
        log_lbl.setProperty("role", "eyebrow")
        log_header.addWidget(log_lbl)
        log_header.addStretch()
        _clear_btn = QPushButton("Clear")
        _clear_btn.setMinimumSize(24, 32)
        _clear_btn.setToolTip("Clear Git output log")
        _clear_btn.setAccessibleName("Clear Git output log")
        log_header.addWidget(_clear_btn)
        right.addLayout(log_header)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.document().setMaximumBlockCount(2000)
        self._log.setPlaceholderText("Git command output appears here.")
        _clear_btn.clicked.connect(self._log.clear)
        right.addWidget(self._log, stretch=1)
        command_row = QHBoxLayout()
        self._git_command_edit = QLineEdit()
        self._git_command_edit.setMinimumHeight(34)
        self._git_command_edit.setPlaceholderText(
            "git rev-parse --abbrev-ref --symbolic-full-name @{u}"
        )
        self._git_command_edit.setAccessibleName("Git command")
        self._git_command_edit.setToolTip(
            "Enter a Git command or its arguments; it runs in this repository without a shell"
        )
        self._git_command_edit.returnPressed.connect(self._run_git_command)
        command_row.addWidget(self._git_command_edit, stretch=1)
        self._run_command_btn = QPushButton("Run")
        self._run_command_btn.setMinimumHeight(34)
        self._run_command_btn.clicked.connect(self._run_git_command)
        command_row.addWidget(self._run_command_btn)
        right.addLayout(command_row)
        command_hint = QLabel("Git arguments only — shell commands and pipelines are not executed.")
        command_hint.setProperty("role", "hint")
        command_hint.setWordWrap(True)
        right.addWidget(command_hint)
        self._command_status = QLabel("")
        self._command_status.setWordWrap(True)
        right.addWidget(self._command_status)

        self._splitter = content_splitter(self._left_panel, right_w, sizes=(380, 720))
        self._splitter.setCollapsible(0, True)
        self._splitter.set_responsive_secondary(0, "Settings")
        self._splitter.add_drawer_toggle_to(log_header)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        root.addWidget(self._splitter, stretch=1)
        self._refresh_repo_state()

    def _browse_repo_dir(self) -> None:
        start = self._dir_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select repository directory", start)
        if not path:
            return
        self._dir_edit.setText(path)
        self._settings["repo_dir"] = path
        save_settings(self._settings)
        self._emit_state_changed()

    def _repo_problem(self) -> str | None:
        """Why the selected folder cannot be used, or None when it is a git checkout."""
        text = self._dir_edit.text().strip()
        if not text:
            return "Choose a repository folder to enable git actions."
        path = Path(text).expanduser()
        try:
            if not path.exists():
                return f"Folder not found: {path}. Check the path or choose Browse…"
            if not path.is_dir():
                return f"{path.name} is a file, not a folder. Choose the folder that holds it."
            if not os.access(path, os.R_OK | os.X_OK):
                return f"No permission to open {path}. Check the folder's access rights."
            if (path / ".git").exists():
                return None
            root = next((parent for parent in path.parents if (parent / ".git").exists()), None)
        except PermissionError:
            return f"No permission to open {path}. Check the folder's access rights."
        if root is not None:
            return (
                f"{path.name} is inside a Git repository but is not its root. "
                f"Choose {root} instead."
            )
        return (
            f"{path.name} is not a Git repository (it has no .git folder). "
            "Choose the repository's root folder."
        )

    def _repo_dir(self, *, show_dialogs: bool = True) -> Path | None:
        problem = self._repo_problem()
        if problem is None:
            return Path(self._dir_edit.text().strip()).expanduser()
        if show_dialogs:
            QMessageBox.warning(self, "Repository", problem)
        return None

    def _refresh_repo_state(self) -> None:
        repo = self._repo_dir(show_dialogs=False)
        ready = repo is not None
        repo_key = str(repo.resolve()) if repo is not None else None
        if repo_key != self._workflow_repo_key:
            self._workflow_repo_key = repo_key
        if repo is not None:
            message = f"Ready — {repo.name} is a valid git repository."
            color = STATUS_OK
        else:
            message = self._repo_problem() or ""
            color = STATUS_NEUTRAL if not self._dir_edit.text().strip() else STATUS_ERR
        if repo_key != self._branch_repo_key:
            self._branch_repo_key = repo_key
            self._refresh_branch_choices(repo, preserve_selection=False)
        self._set_step_status(self._repo_status, message, color)
        self._open_btn.setEnabled(ready)
        # Git-action buttons stay disabled while a background pull/commit/
        # push is in flight, even if the repo path itself is valid.
        git_enabled = ready and not self._git_busy
        for button in (
            self._status_btn,
            self._pull_btn,
            self._force_pull_btn,
            self._commit_btn,
            self._push_btn,
            self._switch_branch_btn,
            self._run_command_btn,
        ):
            button.setEnabled(git_enabled)
        self._branch_combo.setEnabled(git_enabled)
        self._git_command_edit.setEnabled(git_enabled)
        self._cancel_btn.setEnabled(self._git_busy)

    def _refresh_branch_choices(
        self, repo: Path | None, *, preserve_selection: bool = True
    ) -> None:
        if repo is None:
            self._local_branches.clear()
            self._remote_branches.clear()
            with blocked_signals(self._branch_combo):
                self._branch_combo.clear()
                self._branch_combo.setEditText("")
            self._set_step_status(self._current_branch_label, "Choose a repository", STATUS_NEUTRAL)
            return

        git = find_git()
        if git is None:
            self._set_step_status(self._current_branch_label, GIT_NOT_FOUND_MESSAGE, STATUS_ERR)
            return

        runner = GitRunner(git, repo, timeout_s=_GIT_PROBE_TIMEOUT_S)
        refs = runner.run(
            "for-each-ref",
            "--format=%(refname:short) %(refname)",
            "refs/heads",
            "refs/remotes",
        )
        if not refs.ok:
            detail = refs.output or "Git could not list this repository's branches."
            self._set_step_status(
                self._current_branch_label, f"Could not list branches: {detail}", STATUS_WARN
            )
            return

        local_branches: set[str] = set()
        remote_branches: set[str] = set()
        for line in refs.output.splitlines():
            short_name, separator, full_name = line.rpartition(" ")
            if not separator:
                continue
            if full_name.startswith("refs/heads/"):
                local_branches.add(short_name)
            elif full_name.startswith("refs/remotes/") and not short_name.endswith("/HEAD"):
                remote_branches.add(short_name)
        self._local_branches = local_branches
        self._remote_branches = remote_branches

        current_step = runner.run("symbolic-ref", "--short", "-q", "HEAD")
        current_branch = current_step.output.strip() if current_step.ok else ""
        previous_selection = self._branch_combo.currentText()
        with blocked_signals(self._branch_combo):
            self._branch_combo.clear()
            self._branch_combo.addItems(sorted(local_branches | remote_branches, key=str.casefold))
            self._branch_combo.setEditText(
                current_branch or (previous_selection if preserve_selection else "")
            )

        if current_branch:
            self._set_step_status(
                self._current_branch_label, f"Current branch: {current_branch}", STATUS_OK
            )
        else:
            self._set_step_status(
                self._current_branch_label,
                "Detached HEAD — select a branch to attach this checkout.",
                STATUS_WARN,
            )

    @staticmethod
    def _set_step_status(label: QLabel, text: str, color: str) -> None:
        # Git output can contain <placeholders> that Qt would parse as markup.
        label.setTextFormat(Qt.TextFormat.PlainText)
        set_status_label(label, text, color)

    def _set_step_failure(
        self, label: QLabel, step: str, results: list[tuple[list[str], bool, str]]
    ) -> None:
        summary = git_failure_summary(results)
        tone = STATUS_WARN if summary == "cancelled" else STATUS_ERR
        self._set_step_status(label, f"{step} failed: {summary}. Details in Git output.", tone)

    def _append_log_line(self, text: str) -> None:
        lower = text.lower()
        if text.startswith("$ "):
            color = "#79c0ff"
        elif "error" in lower or "fatal" in lower:
            color = STATUS_ERR
        else:
            color = "#c9d1d9"
        self._log.append(
            f'<span style="color:{color}; '
            "font-family: Menlo, Consolas, &quot;DejaVu Sans Mono&quot;, monospace; "
            f'font-size: 11px;">'
            f"{escape(text)}</span>"
        )

    def _open_repo_dir(self) -> None:
        repo = self._repo_dir(show_dialogs=False)
        if repo is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(repo)))

    def _run_git_async(
        self, commands: list[list[str]], on_done, *, show_dialogs: bool = True
    ) -> bool:
        """Run one or more git subprocesses on a background thread.

        Pull/commit/push touch the network; running them via subprocess.run()
        directly on the GUI thread (as this used to) froze the entire app
        indefinitely on a stalled connection, with no way to cancel. Each
        step gets a 30s timeout, and a failed step aborts the remaining ones
        (e.g. "git add" failing skips "git commit") — same short-circuit as
        the old synchronous version.
        """
        repo = self._repo_dir(show_dialogs=show_dialogs)
        if repo is None:
            return False
        git = find_git()
        if git is None:
            if show_dialogs:
                QMessageBox.warning(self, "Repository", GIT_NOT_FOUND_MESSAGE)
            return False
        if self._git_busy:
            return False
        self._git_busy = True
        self._git_cancel.clear()
        self._refresh_repo_state()

        def work() -> None:
            git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            results: list[tuple[list[str], bool, str]] = []
            for args in commands:
                if self._git_cancel.is_set():
                    results.append((args, False, "Cancelled"))
                    break
                try:
                    proc = subprocess.Popen(
                        [git, *args],
                        cwd=str(repo),
                        env=git_env,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        encoding="utf-8",
                        errors="replace",
                        shell=False,
                        creationflags=NO_CONSOLE_WINDOW,
                    )
                    self._git_process = proc
                    stdout, stderr = proc.communicate(timeout=_GIT_STEP_TIMEOUT_S)
                    out = (stdout or "") + ("\n" + stderr if stderr else "")
                    ok = proc.returncode == 0 and not self._git_cancel.is_set()
                    if self._git_cancel.is_set():
                        out += "\nCancelled"
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    out = (stdout or "") + ("\n" + stderr if stderr else "") + "\nTimed out"
                    ok = False
                except OSError as exc:
                    out, ok = str(exc), False
                finally:
                    self._git_process = None
                results.append((args, ok, out.strip()))
                if not ok:
                    break
            if not self._shutting_down:
                self._git_op_done.emit((results, on_done))

        self._git_thread = threading.Thread(target=work, daemon=True)
        self._git_thread.start()
        return True

    def _on_git_op_done(self, payload: tuple) -> None:
        if self._shutting_down:
            return
        results, on_done = payload
        self._append_git_results(results)
        self._emit_state_changed()
        self._git_busy = False
        self._refresh_repo_state()
        branch_commands = {"switch", "checkout", "branch", "fetch", "pull", "reset", "remote"}
        changed_refs = any(args and args[0] in branch_commands for args, _ok, _out in results)
        if changed_refs:
            repo = self._repo_dir(show_dialogs=False)
            prefer_current = any(
                args and args[0] in {"switch", "checkout", "branch", "reset"}
                for args, _ok, _out in results
            )
            self._refresh_branch_choices(repo, preserve_selection=not prefer_current)
        on_done(results)

    def _append_git_results(self, results: list[tuple[list[str], bool, str]]) -> None:
        for args, _ok, out in results:
            command = _redact_git_text(f"$ git {' '.join(args)}")
            self._append_log_line(command)
            for line in out.splitlines() if out else ["(done)"]:
                self._append_log_line(_redact_git_text(line))
            self._append_log_line("")

    def append_auto_sync_log(self, results: list[tuple[list[str], bool, str]]) -> None:
        """Show a background auto-sync run in the Git output, like a manual one."""
        if self._shutting_down or not results:
            return
        self._append_log_line(f"── Auto sync {datetime.now():%H:%M:%S} ──")
        self._append_git_results(results)

    def _switch_branch(self) -> None:
        target = self._branch_combo.currentText().strip()
        try:
            commands = branch_switch_commands(target, self._local_branches, self._remote_branches)
        except ValueError as exc:
            self._set_step_status(self._branch_action_status, str(exc), STATUS_WARN)
            return

        def done(results: list[tuple[list[str], bool, str]]) -> None:
            if results and all(result[1] for result in results):
                self._set_step_status(
                    self._branch_action_status, f"Using branch {target}.", STATUS_OK
                )
            else:
                self._set_step_failure(self._branch_action_status, "Branch switch", results)

        self._run_git_async(commands, done)

    def _run_git_command(self) -> None:
        try:
            args = parse_git_command(self._git_command_edit.text())
        except ValueError as exc:
            self._set_step_status(self._command_status, str(exc), STATUS_WARN)
            return

        def done(results: list[tuple[list[str], bool, str]]) -> None:
            if results and all(result[1] for result in results):
                self._set_step_status(self._command_status, "Git command completed.", STATUS_OK)
            else:
                self._set_step_failure(self._command_status, "Git command", results)

        self._run_git_async([args], done)

    def _git_status(self) -> None:
        self._run_git_async([["status", "--short", "--branch"]], lambda results: None)

    def _git_pull(self) -> None:
        def done(results: list[tuple[list[str], bool, str]]) -> None:
            ok = results[-1][1] if results else False
            if not ok:
                self._set_step_failure(self._pull_status, "Pull", results)
            elif "already up to date" in results[-1][2].lower():
                self._set_step_status(self._pull_status, "Up to date", STATUS_OK)
            else:
                self._set_step_status(self._pull_status, "Pulled remote changes", STATUS_OK)

        self._run_git_async([["pull"]], done)

    def _git_force_pull(self) -> None:
        """Reset the selected checkout to its remote tracking branch.

        Fetch first, then reset the current branch to ``@{u}``. Cleaning
        untracked files is intentional: the confirmation makes the data-loss
        boundary explicit and ensures the folder genuinely matches HEAD.
        """
        repo = self._repo_dir(show_dialogs=True)
        if repo is None:
            return
        answer = QMessageBox.warning(
            self,
            "Reset to Remote — discard local changes?",
            "This will fetch the remote, reset the current branch to its upstream HEAD, "
            "and permanently delete local tracked and untracked changes.\n\n"
            f"Repository: {repo}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def done(results: list[tuple[list[str], bool, str]]) -> None:
            ok = results[-1][1] if results else False
            if ok:
                self._set_step_status(self._pull_status, "Reset to remote HEAD", STATUS_OK)
            else:
                self._set_step_failure(self._pull_status, "Reset to remote", results)

        self._run_git_async(
            [
                ["fetch", "origin", "--prune"],
                ["reset", "--hard", "@{u}"],
                ["clean", "-fd"],
            ],
            done,
        )

    def _git_commit(self) -> None:
        msg = self._commit_msg.text().strip() or "Update project files"
        repo = self._repo_dir(show_dialogs=True)
        if repo is None:
            return
        git = find_git()
        if git is None:
            QMessageBox.warning(self, "Commit", GIT_NOT_FOUND_MESSAGE)
            return
        try:
            identity = {
                key: subprocess.run(
                    [git, "config", "--get", key],
                    cwd=str(repo),
                    text=True,
                    capture_output=True,
                    timeout=_GIT_PROBE_TIMEOUT_S,
                    check=False,
                    creationflags=NO_CONSOLE_WINDOW,
                ).stdout.strip()
                for key in ("user.name", "user.email")
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            show_error(
                self, "Commit setup failed", exc, message="Could not read the Git author identity."
            )
            return
        setup_commands: list[list[str]] = []
        if not identity["user.name"]:
            name, ok = QInputDialog.getText(self, "Git author name", "Name for this repository:")
            if not ok or not name.strip():
                return
            setup_commands.append(["config", "user.name", name.strip()])
        if not identity["user.email"]:
            email, ok = QInputDialog.getText(self, "Git author email", "Email for this repository:")
            if not ok or not email.strip() or "@" not in email:
                QMessageBox.warning(
                    self, "Commit", "Enter a valid email address to create a commit."
                )
                return
            setup_commands.append(["config", "user.email", email.strip()])
        try:
            status = subprocess.run(
                [git, "status", "--short"],
                cwd=str(repo),
                text=True,
                capture_output=True,
                timeout=_GIT_PROBE_TIMEOUT_S,
                check=False,
                creationflags=NO_CONSOLE_WINDOW,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            show_error(self, "Commit setup failed", exc, message="Could not inspect changed files.")
            return
        if not status:
            QMessageBox.information(self, "Commit", "Nothing to commit.")
            return
        preview = status if len(status) <= 4000 else status[:4000] + "\n…"
        answer = QMessageBox.question(
            self,
            "Review Files to Commit",
            f"The following files will be staged and committed:\n\n{preview}\n\nMessage: {msg}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def done(results: list[tuple[list[str], bool, str]]) -> None:
            _, ok, out = results[-1]
            if len(results) < len(setup_commands) + 2:
                # Author setup or "git add" failed before the commit ran.
                self._set_step_failure(self._commit_status, "Commit", results)
            elif not ok and "nothing to commit" in out.lower():
                self._set_step_status(self._commit_status, "Nothing to commit", STATUS_NEUTRAL)
                QMessageBox.information(self, "Commit", "Nothing to commit.")
            elif ok:
                self._set_step_status(self._commit_status, "Committed", STATUS_OK)
            else:
                self._set_step_failure(self._commit_status, "Commit", results)

        self._run_git_async([*setup_commands, ["add", "-A"], ["commit", "-m", msg]], done)

    def _git_push(self) -> None:
        def done(results: list[tuple[list[str], bool, str]]) -> None:
            ok = results[-1][1] if results else False
            if ok:
                self._set_step_status(self._push_status, "Pushed", STATUS_OK)
            else:
                self._set_step_failure(self._push_status, "Push", results)

        self._run_git_async([["push"]], done)

    def get_workspace_state(self) -> dict:
        return {
            "repo_dir": self._dir_edit.text(),
            "commit_msg": self._commit_msg.text(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        if not isinstance(state, dict):
            state = {}
        self._dir_edit.setText(str(state.get("repo_dir", "")))
        self._commit_msg.setText(str(state.get("commit_msg", "Update project files")))
        self._refresh_repo_state()

    def sync_from_settings(self) -> None:
        """Refresh the repo dir field from the current global settings dict."""
        self._dir_edit.setText(self._settings.get("repo_dir", ""))
        self._refresh_repo_state()

    def clear_workspace_state(self) -> None:
        self._dir_edit.setText(self._settings.get("repo_dir", ""))
        self._commit_msg.setText("Update project files")
        self._refresh_repo_state()

    def get_preset_state(self) -> dict[str, dict]:
        return {}

    def apply_preset_state(self, state: dict | None) -> None:
        pass

    def auto_fetch(self) -> bool:
        """Silently fetch from remote repository (for auto-fetch on startup).

        Returns True if successful, False otherwise.
        Does not show message boxes — only logs to the git output panel.
        """
        return self._run_git_async(
            [["fetch", "--all", "--prune"]],
            lambda _results: None,
            show_dialogs=False,
        )

    def current_repo(self) -> Path | None:
        """The repository chosen on this page, when it is a valid git checkout."""
        return self._repo_dir(show_dialogs=False)

    def is_git_busy(self) -> bool:
        """Whether a pull/commit/push started from this page is still running."""
        return self._git_busy

    def set_auto_sync_enabled(self, enabled: bool) -> None:
        """Mirror the Auto sync setting without re-emitting ``autoSyncToggled``."""
        with blocked_signals(self._auto_sync_check):
            self._auto_sync_check.setChecked(enabled)
        if not enabled:
            self.set_auto_sync_status("Off")
        elif self._auto_sync_state == "Off":
            self.set_auto_sync_status("On — waiting for edits")

    def set_auto_sync_status(self, text: str) -> None:
        """Show the auto-sync state: Off, On — waiting…, Syncing…, or the last result."""
        self._auto_sync_state = text
        self._auto_sync_status.setText(f"Auto sync: {text}")

    def _cancel_git_op(self) -> None:
        if not self._git_busy:
            return
        self._git_cancel.set()
        process = self._git_process
        if process is not None and process.poll() is None:
            # Terminate the in-flight subprocess immediately — the cancel
            # flag alone is only checked between steps, so without this the
            # user waits out the full 30s per-command timeout regardless.
            try:
                process.terminate()
            except OSError:
                pass
        self._set_step_status(self._repo_status, "Cancelling…", STATUS_WARN)
        self._cancel_btn.setEnabled(False)

    def _terminate_git_process(self) -> None:
        self._git_cancel.set()
        process = self._git_process
        if process is not None and process.poll() is None:
            process.terminate()

    def shutdown(self) -> None:
        self._shutdown_thread(self._git_thread, self._terminate_git_process)
