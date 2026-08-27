"""Update check dialog."""

from __future__ import annotations

import logging
import subprocess
import webbrowser
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.platform.updates import (
    UpdateInfo,
    can_self_update_windows,
    check_for_updates,
    download_update,
    get_current_version,
    get_releases_page_url,
    launch_windows_self_update,
    update_staging_path,
)
from simple_stipple.ui.components.layout import (
    section_label,
    sep,
    surface_frame,
)
from simple_stipple.ui.components.workflow import set_status_label
from simple_stipple.ui.style import (
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    STATUS_ERR,
)

_LOG = logging.getLogger(__name__)
_DETACHED_THREADS: set[QThread] = set()


class UpdateCheckThread(QThread):
    """Background thread for checking updates."""

    checkComplete = Signal(object)  # UpdateInfo | None

    def run(self) -> None:
        """Run the update check in background."""
        try:
            info = check_for_updates(timeout=10)
            self.checkComplete.emit(info)
        except (OSError, RuntimeError, ValueError) as exc:
            _LOG.error("Update check thread error: %s", exc)
            self.checkComplete.emit(None)


class UpdateDownloadThread(QThread):
    """Background thread for downloading update artifacts."""

    downloadComplete = Signal(bool, str, str)  # success, path, system
    downloadProgress = Signal(int, int)  # downloaded bytes, total bytes or 0

    def __init__(self, url: str, dest_path: Path, system: str, sha256: str, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._dest_path = dest_path
        self._system = system
        self._sha256 = sha256

    def run(self) -> None:
        try:
            success = download_update(
                self._url,
                self._dest_path,
                expected_sha256=self._sha256,
                progress_cb=lambda done, total: self.downloadProgress.emit(done, total or 0),
            )
            self.downloadComplete.emit(success, str(self._dest_path), self._system)
        except (OSError, RuntimeError, ValueError) as exc:
            _LOG.error("Update download thread error: %s", exc)
            self.downloadComplete.emit(False, str(self._dest_path), self._system)


class UpdateDialog(QDialog):
    """Dialog for viewing and installing updates."""

    def __init__(self, parent: QWidget | None = None, update_info: UpdateInfo | None = None):
        super().__init__(parent)
        self.setWindowTitle("Check for Updates")
        self.resize(600, 500)
        self.setMinimumSize(500, 400)
        self.setModal(True)

        self._update_info = update_info
        self._check_thread: UpdateCheckThread | None = None
        self._download_thread: UpdateDownloadThread | None = None
        self._download_progress: QProgressDialog | None = None
        self._close_btn: QPushButton | None = None
        self._download_btn: QPushButton | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
        layout.setSpacing(SPACE_MD)

        title = QLabel("Check for Updates")
        title.setProperty("role", "dialog-title")
        layout.addWidget(title)

        current_version = get_current_version()
        subtitle = QLabel(f"You are currently running version {current_version}")
        subtitle.setProperty("role", "dialog-subtitle")
        layout.addWidget(subtitle)

        sep(layout)

        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(SPACE_MD)
        layout.addWidget(content, stretch=1)

        # Build content based on state without replacing persistent dialog chrome.
        if update_info is None:
            self._build_checking_ui(self._content_layout)
        elif update_info.is_newer:
            self._build_update_available_ui(self._content_layout, update_info)
        else:
            self._build_up_to_date_ui(self._content_layout, update_info)

        sep(layout)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._close_btn = QPushButton("Close")
        self._close_btn.setMinimumWidth(90)
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _build_checking_ui(self, layout: QVBoxLayout) -> None:
        """Show checking state."""
        status = QLabel("Checking for updates…")
        status.setProperty("role", "status-neutral")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(status, stretch=1)

        # Start background check if not already provided
        if self._update_info is None:
            self._start_background_check(layout)

    def _start_background_check(self, layout: QVBoxLayout) -> None:
        """Start background update check."""
        self._check_thread = UpdateCheckThread()
        self._check_thread.checkComplete.connect(lambda info: self._on_check_complete(info, layout))
        self._check_thread.start()

    def _on_check_complete(self, info: UpdateInfo | None, layout: QVBoxLayout) -> None:
        """Handle update check completion."""
        self._update_info = info
        # Clear only the replaceable content region.
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                child_layout = item.layout()
                if child_layout is not None:
                    child_layout.deleteLater()

        if info is None:
            error_label = QLabel()
            error_label.setWordWrap(True)
            set_status_label(
                error_label,
                "Failed to check for updates. Please check your internet connection.",
                STATUS_ERR,
                hide_when_empty=False,
            )
            layout.addWidget(error_label, stretch=1)
        elif info.is_newer:
            self._build_update_available_ui(layout, info)
        else:
            self._build_up_to_date_ui(layout, info)

    def _build_up_to_date_ui(self, layout: QVBoxLayout, info: UpdateInfo) -> None:
        """Show up-to-date message."""
        card = surface_frame("panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        card_layout.setSpacing(SPACE_SM)

        section_label(card_layout, "✓ You're Up to Date")

        msg = QLabel(f"Version {info.version} is the latest available.")
        msg.setProperty("role", "dialog-message")
        msg.setWordWrap(True)
        card_layout.addWidget(msg)

        layout.addWidget(card, stretch=1)

    def _build_update_available_ui(self, layout: QVBoxLayout, info: UpdateInfo) -> None:
        """Show update available message with download option."""
        card = surface_frame("panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)
        card_layout.setSpacing(SPACE_SM)

        section_label(card_layout, f"↓ Update Available: v{info.version}")

        msg = QLabel("A new version is available for download.")
        msg.setProperty("role", "dialog-message")
        msg.setWordWrap(True)
        card_layout.addWidget(msg)

        # Release notes
        if info.release_notes:
            notes_label = QLabel("Release Notes:")
            notes_label.setProperty("role", "section-title")
            card_layout.addWidget(notes_label)

            notes = QTextEdit()
            notes.setReadOnly(True)
            notes.setText(info.release_notes)
            notes.setMaximumHeight(150)
            notes.setProperty("role", "release-notes")
            card_layout.addWidget(notes)

        card_layout.addStretch()

        # Action buttons
        action_row = QHBoxLayout()
        action_row.addStretch()

        browse_btn = QPushButton("Visit Release Page")
        browse_btn.setMinimumWidth(120)
        browse_btn.clicked.connect(lambda: self._open_release_page(info))
        action_row.addWidget(browse_btn)

        self._download_btn = QPushButton("Download & Install")
        self._download_btn.setMinimumWidth(130)
        self._download_btn.setProperty("role", "primary")
        self._download_btn.clicked.connect(lambda: self._download_and_install(info))
        if not info.sha256:
            self._download_btn.setEnabled(False)
            self._download_btn.setToolTip(
                "Automatic installation is disabled because this release has no SHA-256 digest."
            )
        action_row.addWidget(self._download_btn)

        card_layout.addLayout(action_row)
        layout.addWidget(card, stretch=1)

    def _open_release_page(self, info: UpdateInfo) -> None:
        """Open the GitHub release page in browser."""
        _ = info
        webbrowser.open(get_releases_page_url())

    def _download_and_install(self, info: UpdateInfo) -> None:
        """Download and attempt to install the update."""
        if not info.sha256:
            QMessageBox.warning(
                self,
                "Update Not Verified",
                "This release does not publish a SHA-256 digest. Open the release page "
                "and verify the download manually.",
            )
            return
        reply = QMessageBox.question(
            self,
            "Download Update",
            f"Download and install version {info.version}?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        import platform

        system = platform.system()
        download_path = update_staging_path(info.version, system)
        self._set_download_busy(True)

        # Show non-blocking progress and run download in the background.
        if self._download_progress is None:
            self._download_progress = QProgressDialog(self)
        self._download_progress.setWindowTitle("Downloading Update")
        self._download_progress.setLabelText(f"Downloading Simple Stipple {info.version}…")
        self._download_progress.setCancelButton(None)
        self._download_progress.setRange(0, 0)
        self._download_progress.setMinimumDuration(0)
        self._download_progress.setModal(True)
        self._download_progress.show()

        self._download_thread = UpdateDownloadThread(
            info.url,
            download_path,
            system,
            info.sha256,
            parent=self,
        )
        self._download_thread.downloadProgress.connect(self._on_download_progress)
        self._download_thread.downloadComplete.connect(self._on_download_complete)
        self._download_thread.start()

    def _on_download_progress(self, bytes_done: int, total: int) -> None:
        if self._download_progress is None:
            return
        if total <= 0:
            self._download_progress.setRange(0, 0)
            return
        self._download_progress.setRange(0, total)
        self._download_progress.setValue(min(bytes_done, total))
        percent = int(bytes_done * 100 / total)
        self._download_progress.setLabelText(f"Downloading update… {percent}%")

    def _on_download_complete(self, success: bool, path: str, system: str) -> None:
        """Handle completion of background update download."""
        if self._download_progress is not None:
            self._download_progress.hide()
            self._download_progress.deleteLater()
            self._download_progress = None
        self._set_download_busy(False)

        download_path = Path(path)

        if not success:
            QMessageBox.critical(
                self,
                "Download Failed",
                "Failed to download the update. Please try again later or visit the release page.",
            )
            return

        if system == "Windows" and can_self_update_windows():
            reply = QMessageBox.question(
                self,
                "Ready to Restart",
                "The verified update is ready. Restart Simple Stipple now to finish installing?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                if launch_windows_self_update(download_path):
                    self.accept()
                    QCoreApplication.quit()
                    return
                QMessageBox.critical(
                    self,
                    "Could Not Start Installer",
                    "The update was downloaded and verified, but the installer could not start. "
                    "Try again or install it manually from the staged file.",
                )

        QMessageBox.information(
            self,
            "Update Ready",
            f"The verified update was downloaded to:\n{download_path}\n\n"
            "Open it to finish installing this platform's update.",
        )

        # Platforms without an in-place updater still open the verified artifact.
        try:
            if system == "Darwin":
                subprocess.Popen(["open", str(download_path)])
            elif system == "Windows":
                subprocess.Popen(["explorer", f"/select,{download_path}"])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            _LOG.warning("Could not open the staged update: %s", exc)

    def _set_download_busy(self, busy: bool) -> None:
        """Keep update dialog actions coherent while a download is active."""
        if self._download_btn is not None:
            self._download_btn.setEnabled(not busy)
            self._download_btn.setText("Downloading…" if busy else "Download & Install")
        if self._close_btn is not None:
            self._close_btn.setEnabled(not busy)

    def done(self, result: int) -> None:
        # accept()/reject() (Close button, Esc) hide the dialog without a
        # QCloseEvent, so the thread detach below must run here too.
        self._detach_network_threads()
        super().done(result)

    def closeEvent(self, event) -> None:
        self._detach_network_threads()
        super().closeEvent(event)

    def _detach_network_threads(self) -> None:
        """Detach in-flight network threads before the dialog is destroyed.

        ``quit()`` cannot stop a QThread whose ``run`` method is blocked in a
        network request. Destroying a parented, still-running QThread is fatal
        in Qt, so suppress late UI delivery and retain it until completion.
        """
        for attr in ("_check_thread", "_download_thread"):
            thread = getattr(self, attr, None)
            if thread is None:
                continue
            try:
                if thread.isRunning():
                    thread.requestInterruption()
                    if isinstance(thread, UpdateCheckThread):
                        thread.checkComplete.disconnect()
                    else:
                        thread.downloadProgress.disconnect()
                        thread.downloadComplete.disconnect()
                    thread.setParent(None)
                    _DETACHED_THREADS.add(thread)
                    thread.finished.connect(lambda active=thread: _DETACHED_THREADS.discard(active))
            except RuntimeError:
                # Thread already deleted by Qt
                pass
            setattr(self, attr, None)
