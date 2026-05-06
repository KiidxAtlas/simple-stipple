"""Repository page — basic Git pull/commit/push workflow."""

from __future__ import annotations

import subprocess
from html import escape
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.settings import save_settings
from src.ui.components.common.factories import (
    _content_splitter,
    _sidebar_panel,
    _surface_frame,
)


class RepoPage(QWidget):
    stateChanged = Signal()

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}

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
        self._dir_edit.textChanged.connect(self._refresh_repo_state)
        dir_row.addWidget(self._dir_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_repo_dir)
        dir_row.addWidget(browse_btn)
        left.addLayout(dir_row)

        self._repo_status = QLabel("")
        self._repo_status.setWordWrap(True)
        left.addWidget(self._repo_status)

        # Workflow cards: Pull | Commit | Push
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)

        # Pull card
        pull_card = _surface_frame("panel")
        pull_card_layout = QVBoxLayout(pull_card)
        pull_card_layout.setContentsMargins(8, 8, 8, 8)
        pull_card_layout.setSpacing(6)
        pull_card_lbl = QLabel("PULL")
        pull_card_lbl.setProperty("role", "eyebrow")
        pull_card_layout.addWidget(pull_card_lbl)
        self._pull_btn = QPushButton("Pull")
        self._pull_btn.setMinimumHeight(34)
        self._pull_btn.setToolTip("Pull latest changes from remote")
        self._pull_btn.clicked.connect(self._git_pull)
        pull_card_layout.addWidget(self._pull_btn)
        self._pull_status = QLabel("")
        self._pull_status.setWordWrap(True)
        pull_card_layout.addWidget(self._pull_status)
        cards_row.addWidget(pull_card, stretch=1)

        # Commit card
        commit_card = _surface_frame("panel")
        commit_card_layout = QVBoxLayout(commit_card)
        commit_card_layout.setContentsMargins(8, 8, 8, 8)
        commit_card_layout.setSpacing(6)
        commit_card_lbl = QLabel("COMMIT")
        commit_card_lbl.setProperty("role", "eyebrow")
        commit_card_layout.addWidget(commit_card_lbl)
        self._commit_msg = QLineEdit("Update project files")
        self._commit_msg.setPlaceholderText("Commit message…")
        self._commit_msg.textChanged.connect(self._emit_state_changed)
        commit_card_layout.addWidget(self._commit_msg)
        self._commit_btn = QPushButton("Commit")
        self._commit_btn.setMinimumHeight(34)
        self._commit_btn.setToolTip(
            "Stage all changes and commit with the message above"
        )
        self._commit_btn.clicked.connect(self._git_commit)
        commit_card_layout.addWidget(self._commit_btn)
        self._commit_status = QLabel("")
        self._commit_status.setWordWrap(True)
        commit_card_layout.addWidget(self._commit_status)
        cards_row.addWidget(commit_card, stretch=1)

        # Push card
        push_card = _surface_frame("panel")
        push_card_layout = QVBoxLayout(push_card)
        push_card_layout.setContentsMargins(8, 8, 8, 8)
        push_card_layout.setSpacing(6)
        push_card_lbl = QLabel("PUSH")
        push_card_lbl.setProperty("role", "eyebrow")
        push_card_layout.addWidget(push_card_lbl)
        self._push_btn = QPushButton("Push")
        self._push_btn.setMinimumHeight(34)
        self._push_btn.setProperty("role", "primary")
        self._push_btn.setToolTip("Push committed changes to remote")
        self._push_btn.clicked.connect(self._git_push)
        push_card_layout.addWidget(self._push_btn)
        self._push_status = QLabel("")
        self._push_status.setWordWrap(True)
        push_card_layout.addWidget(self._push_status)
        cards_row.addWidget(push_card, stretch=1)

        left.addLayout(cards_row)

        # Secondary actions
        secondary = QHBoxLayout()
        secondary.setSpacing(4)
        self._status_btn = QPushButton("Status")
        self._status_btn.setToolTip("Show current repository status")
        self._status_btn.clicked.connect(self._git_status)
        secondary.addWidget(self._status_btn)
        self._open_btn = QPushButton("Open Folder")
        self._open_btn.setToolTip("Open the repository folder in Finder")
        self._open_btn.clicked.connect(self._open_repo_dir)
        secondary.addWidget(self._open_btn)
        secondary.addStretch()
        left.addLayout(secondary)
        left.addStretch()

        self._left_panel = _sidebar_panel(left_w, min_width=300, max_width=380)

        # ── Right: git log ────────────────────────────────────────────────────
        right_w = _surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(12, 12, 12, 12)
        right.setSpacing(6)

        log_header = QHBoxLayout()
        log_lbl = QLabel("GIT OUTPUT")
        log_lbl.setProperty("role", "eyebrow")
        log_header.addWidget(log_lbl)
        log_header.addStretch()
        _clear_btn = QPushButton("Clear")
        _clear_btn.setFixedHeight(20)
        log_header.addWidget(_clear_btn)
        right.addLayout(log_header)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Git command output appears here.")
        _clear_btn.clicked.connect(self._log.clear)
        right.addWidget(self._log, stretch=1)

        self._splitter = _content_splitter(self._left_panel, right_w, sizes=(320, 720))
        root.addWidget(self._splitter, stretch=1)
        self._refresh_repo_state()

    def _emit_state_changed(self) -> None:
        self.stateChanged.emit()

    def _browse_repo_dir(self) -> None:
        start = self._dir_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "Select repository directory", start
        )
        if not path:
            return
        self._dir_edit.setText(path)
        self._settings["repo_dir"] = path
        save_settings(self._settings)
        self._emit_state_changed()

    def _repo_dir(self, *, show_dialogs: bool = True) -> Path | None:
        text = self._dir_edit.text().strip()
        if not text:
            if show_dialogs:
                QMessageBox.information(
                    self, "Repository", "Select a repository directory first."
                )
            return None
        p = Path(text)
        if not p.exists() or not p.is_dir():
            if show_dialogs:
                QMessageBox.warning(self, "Repository", "Directory does not exist.")
            return None
        if not (p / ".git").exists():
            if show_dialogs:
                QMessageBox.warning(
                    self, "Repository", "Selected directory is not a git repository."
                )
            return None
        return p

    def _refresh_repo_state(self) -> None:
        repo = self._repo_dir(show_dialogs=False)
        ready = repo is not None
        if not self._dir_edit.text().strip():
            message = "Choose a repository folder to enable git actions."
            color = "#8b949e"
        elif ready:
            message = f"Ready — {repo.name} is a valid git repository."
            color = "#3fb950"
        else:
            message = "Selected folder is missing or is not a git repository."
            color = "#f85149"
        self._set_step_status(self._repo_status, message, color)
        for button in (
            self._status_btn,
            self._pull_btn,
            self._commit_btn,
            self._push_btn,
            self._open_btn,
        ):
            button.setEnabled(ready)

    @staticmethod
    def _set_step_status(label: QLabel, text: str, color: str) -> None:
        if not text:
            label.setVisible(False)
            return
        label.setVisible(True)
        label.setText(text)
        if color == "#3fb950":
            role = "status-ok"
        elif color == "#f85149":
            role = "status-err"
        else:
            role = "status-neutral"
        label.setProperty("role", role)
        label.style().unpolish(label)
        label.style().polish(label)

    def _append_log_line(self, text: str) -> None:
        lower = text.lower()
        if text.startswith("$ "):
            color = "#79c0ff"
        elif "error" in lower or "fatal" in lower:
            color = "#f85149"
        else:
            color = "#c9d1d9"
        self._log.append(
            f'<span style="color:{color}; font-family: Menlo, Courier; font-size: 11px;">'
            f"{escape(text)}</span>"
        )

    def _open_repo_dir(self) -> None:
        repo = self._repo_dir(show_dialogs=False)
        if repo is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(repo)))

    def _run_git(self, args: list[str]) -> tuple[bool, str]:
        repo = self._repo_dir()
        if repo is None:
            return False, ""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(repo),
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            QMessageBox.critical(self, "Git", str(exc))
            return False, ""

        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        out = out.strip()
        self._append_log_line(f"$ git {' '.join(args)}")
        for line in out.splitlines() if out else ["(done)"]:
            self._append_log_line(line)
        self._append_log_line("")
        self._emit_state_changed()
        return proc.returncode == 0, out

    def _git_status(self) -> None:
        self._run_git(["status", "--short", "--branch"])

    def _git_pull(self) -> None:
        ok, _ = self._run_git(["pull"])
        if ok:
            self._set_step_status(self._pull_status, "Up to date", "#3fb950")
        else:
            self._set_step_status(
                self._pull_status, "Pull failed — check log", "#f85149"
            )

    def _git_commit(self) -> None:
        ok, _ = self._run_git(["add", "-A"])
        if not ok:
            return
        msg = self._commit_msg.text().strip() or "Update project files"
        ok, out = self._run_git(["commit", "-m", msg])
        if not ok and "nothing to commit" in out.lower():
            self._set_step_status(self._commit_status, "Nothing to commit", "#8b949e")
            QMessageBox.information(self, "Commit", "Nothing to commit.")
        elif ok:
            self._set_step_status(self._commit_status, "Committed", "#3fb950")
        else:
            self._set_step_status(
                self._commit_status, "Commit failed — check log", "#f85149"
            )

    def _git_push(self) -> None:
        ok, _ = self._run_git(["push"])
        if ok:
            self._set_step_status(self._push_status, "Pushed", "#3fb950")
        else:
            self._set_step_status(
                self._push_status, "Push failed — check log", "#f85149"
            )

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

    def apply_preset_state(self, presets: dict[str, dict]) -> None:
        _ = presets

    def auto_fetch(self) -> bool:
        """Silently fetch from remote repository (for auto-fetch on startup).

        Returns True if successful, False otherwise.
        Does not show message boxes — only logs to the git output panel.
        """
        repo = self._repo_dir()
        if repo is None:
            return False
        try:
            proc = subprocess.run(
                ["git", "fetch", "--all", "--prune"],
                cwd=str(repo),
                text=True,
                capture_output=True,
                check=False,
                timeout=15,  # 15-second timeout for network operations
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        out = out.strip()
        self._append_log_line("$ git fetch --all --prune (auto)")
        for line in out.splitlines() if out else ["(done)"]:
            self._append_log_line(line)
        self._append_log_line("")
        self._emit_state_changed()
        return proc.returncode == 0
