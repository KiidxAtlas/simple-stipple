"""Repository synchronization workflow UI."""

from __future__ import annotations

import subprocess
import threading
from html import escape
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
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
from simple_stipple.platform.config import save_settings
from simple_stipple.ui.components.layout import (
    content_splitter,
    sidebar_panel,
    surface_frame,
)
from simple_stipple.ui.components.workflow import set_status_label
from simple_stipple.ui.files import reveal_label
from simple_stipple.ui.style.theme import STATUS_ERR, STATUS_NEUTRAL, STATUS_OK


class RepoPage(BasePage):
    _git_op_done = Signal(object)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._git_busy = False
        self._shutting_down = False
        self._git_cancel = threading.Event()
        self._git_process: subprocess.Popen[str] | None = None
        self._git_thread: threading.Thread | None = None
        self._git_op_done.connect(self._on_git_op_done)

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

        self._repo_status = QLabel("")
        self._repo_status.setWordWrap(True)
        left.addWidget(self._repo_status)

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

        self._splitter = content_splitter(self._left_panel, right_w, sizes=(380, 720))
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

    def _repo_dir(self, *, show_dialogs: bool = True) -> Path | None:
        text = self._dir_edit.text().strip()
        if not text:
            if show_dialogs:
                QMessageBox.information(self, "Repository", "Select a repository directory first.")
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
            color = STATUS_NEUTRAL
        elif repo is not None:
            message = f"Ready — {repo.name} is a valid git repository."
            color = STATUS_OK
        else:
            message = "Selected folder is missing or is not a git repository."
            color = STATUS_ERR
        self._set_step_status(self._repo_status, message, color)
        self._open_btn.setEnabled(ready)
        # Git-action buttons stay disabled while a background pull/commit/
        # push is in flight, even if the repo path itself is valid.
        git_enabled = ready and not self._git_busy
        for button in (
            self._status_btn,
            self._pull_btn,
            self._commit_btn,
            self._push_btn,
        ):
            button.setEnabled(git_enabled)
        self._cancel_btn.setEnabled(self._git_busy)

    @staticmethod
    def _set_step_status(label: QLabel, text: str, color: str) -> None:
        set_status_label(label, text, color)

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
        if self._git_busy:
            return False
        self._git_busy = True
        self._git_cancel.clear()
        self._refresh_repo_state()

        def work() -> None:
            results: list[tuple[list[str], bool, str]] = []
            for args in commands:
                if self._git_cancel.is_set():
                    results.append((args, False, "Cancelled"))
                    break
                try:
                    proc = subprocess.Popen(
                        ["git", *args],
                        cwd=str(repo),
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self._git_process = proc
                    stdout, stderr = proc.communicate(timeout=30)
                    out = (stdout or "") + ("\n" + stderr if stderr else "")
                    ok = proc.returncode == 0 and not self._git_cancel.is_set()
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
        for args, _ok, out in results:
            self._append_log_line(f"$ git {' '.join(args)}")
            for line in out.splitlines() if out else ["(done)"]:
                self._append_log_line(line)
            self._append_log_line("")
        self._emit_state_changed()
        self._git_busy = False
        self._refresh_repo_state()
        on_done(results)

    def _git_status(self) -> None:
        self._run_git_async([["status", "--short", "--branch"]], lambda results: None)

    def _git_pull(self) -> None:
        def done(results: list[tuple[list[str], bool, str]]) -> None:
            ok = results[-1][1] if results else False
            if ok:
                self._set_step_status(self._pull_status, "Up to date", STATUS_OK)
            else:
                self._set_step_status(self._pull_status, "Pull failed — check log", STATUS_ERR)

        self._run_git_async([["pull"]], done)

    def _git_commit(self) -> None:
        msg = self._commit_msg.text().strip() or "Update project files"
        repo = self._repo_dir(show_dialogs=True)
        if repo is None:
            return
        try:
            status = subprocess.run(
                ["git", "status", "--short"],
                cwd=str(repo),
                text=True,
                capture_output=True,
                timeout=3,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            QMessageBox.warning(self, "Commit", f"Could not inspect changed files:\n{exc}")
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
            if len(results) < 2:
                return  # "git add" itself failed; already logged
            _, ok, out = results[-1]
            if not ok and "nothing to commit" in out.lower():
                self._set_step_status(self._commit_status, "Nothing to commit", STATUS_NEUTRAL)
                QMessageBox.information(self, "Commit", "Nothing to commit.")
            elif ok:
                self._set_step_status(self._commit_status, "Committed", STATUS_OK)
            else:
                self._set_step_status(self._commit_status, "Commit failed — check log", STATUS_ERR)

        self._run_git_async([["add", "-A"], ["commit", "-m", msg]], done)

    def _git_push(self) -> None:
        def done(results: list[tuple[list[str], bool, str]]) -> None:
            ok = results[-1][1] if results else False
            if ok:
                self._set_step_status(self._push_status, "Pushed", STATUS_OK)
            else:
                self._set_step_status(self._push_status, "Push failed — check log", STATUS_ERR)

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
        self._set_step_status(self._repo_status, "Cancelling…", STATUS_ERR)
        self._cancel_btn.setEnabled(False)

    def shutdown(self) -> None:
        self._shutting_down = True
        self._git_cancel.set()
        self.blockSignals(True)
        process = self._git_process
        if process is not None and process.poll() is None:
            process.terminate()
        thread = self._git_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
