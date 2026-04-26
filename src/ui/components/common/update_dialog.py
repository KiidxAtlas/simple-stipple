"""Update check dialog."""

from __future__ import annotations

import logging
import subprocess
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.ui.components.common.factories import _section_label, _sep, _surface_frame
from src.updates import (
    UpdateInfo,
    check_for_updates,
    download_update,
    get_current_version,
    get_releases_page_url,
)

_LOG = logging.getLogger(__name__)


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

    def __init__(self, url: str, dest_path: Path, system: str, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._dest_path = dest_path
        self._system = system

    def run(self) -> None:
        try:
            success = download_update(self._url, self._dest_path)
            self.downloadComplete.emit(success, str(self._dest_path), self._system)
        except (OSError, RuntimeError, ValueError) as exc:
            _LOG.error("Update download thread error: %s", exc)
            self.downloadComplete.emit(False, str(self._dest_path), self._system)


class UpdateDialog(QDialog):
    """Dialog for viewing and installing updates."""

    def __init__(
        self, parent: QWidget | None = None, update_info: UpdateInfo | None = None
    ):
        super().__init__(parent)
        self.setWindowTitle("Check for Updates")
        self.resize(600, 500)
        self.setMinimumSize(500, 400)
        self.setModal(True)

        self._update_info = update_info
        self._check_thread: UpdateCheckThread | None = None
        self._download_thread: UpdateDownloadThread | None = None
        self._download_progress: QMessageBox | None = None
        self._close_btn: QPushButton | None = None
        self._download_btn: QPushButton | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Check for Updates")
        title.setStyleSheet("color: #f0f6fc; font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        current_version = get_current_version()
        subtitle = QLabel(f"You are currently running version {current_version}")
        subtitle.setStyleSheet("color: #8b949e; font-size: 12px;")
        layout.addWidget(subtitle)

        _sep(layout)

        # Build content based on state
        if update_info is None:
            self._build_checking_ui(layout)
        elif update_info.is_newer:
            self._build_update_available_ui(layout, update_info)
        else:
            self._build_up_to_date_ui(layout, update_info)

        _sep(layout)

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
        status.setStyleSheet("color: #8b949e; font-size: 13px;")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(status, stretch=1)

        # Start background check if not already provided
        if self._update_info is None:
            self._start_background_check(layout)

    def _start_background_check(self, layout: QVBoxLayout) -> None:
        """Start background update check."""
        self._check_thread = UpdateCheckThread()
        self._check_thread.checkComplete.connect(
            lambda info: self._on_check_complete(info, layout)
        )
        self._check_thread.start()

    def _on_check_complete(self, info: UpdateInfo | None, layout: QVBoxLayout) -> None:
        """Handle update check completion."""
        # Clear current layout
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

        if info is None:
            error_label = QLabel(
                "Failed to check for updates. Please check your internet connection."
            )
            error_label.setStyleSheet("color: #f85149; font-size: 13px;")
            error_label.setWordWrap(True)
            layout.addWidget(error_label, stretch=1)
        elif info.is_newer:
            self._build_update_available_ui(layout, info)
        else:
            self._build_up_to_date_ui(layout, info)

    def _build_up_to_date_ui(self, layout: QVBoxLayout, info: UpdateInfo) -> None:
        """Show up-to-date message."""
        card = _surface_frame("panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        _section_label(card_layout, "✓ You're Up to Date")

        msg = QLabel(f"Version {info.version} is the latest available.")
        msg.setStyleSheet("color: #8b949e; font-size: 13px;")
        msg.setWordWrap(True)
        card_layout.addWidget(msg)

        layout.addWidget(card, stretch=1)

    def _build_update_available_ui(self, layout: QVBoxLayout, info: UpdateInfo) -> None:
        """Show update available message with download option."""
        card = _surface_frame("panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        _section_label(card_layout, f"↓ Update Available: v{info.version}")

        msg = QLabel("A new version is available for download.")
        msg.setStyleSheet("color: #8b949e; font-size: 13px;")
        msg.setWordWrap(True)
        card_layout.addWidget(msg)

        # Release notes
        if info.release_notes:
            notes_label = QLabel("Release Notes:")
            notes_label.setStyleSheet(
                "color: #e6edf3; font-size: 12px; font-weight: 600;"
            )
            card_layout.addWidget(notes_label)

            notes = QTextEdit()
            notes.setReadOnly(True)
            notes.setText(info.release_notes)
            notes.setMaximumHeight(150)
            notes.setStyleSheet(
                """
                QTextEdit {
                    background: #0f141b;
                    border: 1px solid #2b3440;
                    border-radius: 4px;
                    color: #c9d1d9;
                    font-size: 11px;
                }
            """
            )
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
        action_row.addWidget(self._download_btn)

        card_layout.addLayout(action_row)
        layout.addWidget(card, stretch=1)

    def _open_release_page(self, info: UpdateInfo) -> None:
        """Open the GitHub release page in browser."""
        _ = info
        webbrowser.open(get_releases_page_url())

    def _download_and_install(self, info: UpdateInfo) -> None:
        """Download and attempt to install the update."""
        reply = QMessageBox.question(
            self,
            "Download Update",
            f"Download and install version {info.version}?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        # Determine file extension based on platform
        import platform

        system = platform.system()
        if system == "Darwin":
            filename = f"SimpleStipple-{info.version}.dmg"
        elif system == "Windows":
            filename = f"SimpleStipple-{info.version}.exe"
        else:
            filename = f"SimpleStipple-{info.version}.tar.gz"

        download_path = Path.home() / "Downloads" / filename
        self._set_download_busy(True)

        # Show non-blocking progress message and run download in background.
        if self._download_progress is None:
            self._download_progress = QMessageBox(self)
        self._download_progress.setWindowTitle("Downloading")
        self._download_progress.setText(f"Downloading update to:\n{download_path}")
        self._download_progress.setStandardButtons(QMessageBox.StandardButton.NoButton)
        self._download_progress.setModal(True)
        self._download_progress.show()

        self._download_thread = UpdateDownloadThread(
            info.url,
            download_path,
            system,
            parent=self,
        )
        self._download_thread.downloadComplete.connect(self._on_download_complete)
        self._download_thread.start()

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

        QMessageBox.information(
            self,
            "Download Complete",
            f"Update downloaded to:\n{download_path}\n\nPlease close the app and install the new version.",
        )

        # Try to open the Downloads folder
        try:
            if system == "Darwin":
                subprocess.run(["open", str(Path.home() / "Downloads")])
            elif system == "Windows":
                subprocess.run(["explorer", str(Path.home() / "Downloads")])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            _LOG.warning("Could not open Downloads folder: %s", exc)

    def _set_download_busy(self, busy: bool) -> None:
        """Keep update dialog actions coherent while a download is active."""
        if self._download_btn is not None:
            self._download_btn.setEnabled(not busy)
            self._download_btn.setText("Downloading…" if busy else "Download & Install")
        if self._close_btn is not None:
            self._close_btn.setEnabled(not busy)
