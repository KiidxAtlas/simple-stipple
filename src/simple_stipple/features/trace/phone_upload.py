"""QR pairing and desktop review for Trace's short-lived phone uploads."""
from __future__ import annotations

import uuid

import cv2
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from simple_stipple.platform.phone_upload import SESSION_SECONDS, PhoneUploadSession
from simple_stipple.platform.settings import user_data_dir


def local_addresses() -> list[tuple[str, str]]:
    addresses = []
    seen = set()
    flags = QNetworkInterface.InterfaceFlag
    for interface in QNetworkInterface.allInterfaces():
        if not interface.flags() & flags.IsUp or interface.flags() & flags.IsLoopBack:
            continue
        for entry in interface.addressEntries():
            address = entry.ip()
            if address.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                continue
            host = address.toString()
            if address.isLoopback() or host.startswith("169.254.") or host in seen:
                continue
            seen.add(host)
            addresses.append((f"{interface.humanReadableName()} — {host}", host))
    return addresses


def qr_pixmap(url: str) -> QPixmap:
    """Use OpenCV's existing QR encoder; keep its black/white pixels crisp."""
    encoder_factory = vars(cv2).get("QRCodeEncoder_create")
    if encoder_factory is None:
        raise ValueError("The installed OpenCV build has no QR encoder")
    pixels = encoder_factory().encode(url)
    height, width = pixels.shape
    image = QImage(pixels.data, width, height, pixels.strides[0],
                   QImage.Format.Format_Grayscale8).copy()
    return QPixmap.fromImage(image).scaled(280, 280, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.FastTransformation)


class PhoneUploadDialog(QDialog):
    _received = Signal(bytes)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Upload from phone")
        self.setMinimumWidth(420)
        self.image_path: str | None = None
        self._image: bytes | None = None
        self._session: PhoneUploadSession | None = None
        layout = QVBoxLayout(self)
        intro = QLabel("Connect your phone to the same Wi-Fi, then scan this code with its camera.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self._networks = QComboBox()
        for label, host in local_addresses():
            self._networks.addItem(label, host)
        self._networks.setAccessibleName("Computer network address")
        self._networks.setToolTip("If your phone cannot connect, choose your Wi-Fi network address.")
        layout.addWidget(self._networks)
        self._qr = QLabel()
        self._qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr.setAccessibleName("Scan this QR code to send a picture")
        layout.addWidget(self._qr)
        self._link = QLineEdit()
        self._link.setReadOnly(True)
        self._link.setAccessibleName("Private upload link")
        layout.addWidget(self._link)
        copy = QPushButton("Copy link")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self._link.text()))
        layout.addWidget(copy)
        self._status = QLabel("Waiting for a picture…")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        privacy = QLabel("Trusted networks only: this local connection is not encrypted. "
                         "Anyone with the link can send one picture. No cloud service is used. "
                         "Close this window to stop sharing. Link expires after 10 minutes. "
                         "Allow local network access through your firewall if asked.")
        privacy.setWordWrap(True)
        privacy.setProperty("role", "hint")
        layout.addWidget(privacy)
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._use = self._buttons.addButton("Use image", QDialogButtonBox.ButtonRole.AcceptRole)
        self._use.setEnabled(False)
        self._buttons.rejected.connect(self.reject)
        self._use.clicked.connect(self._use_image)
        layout.addWidget(self._buttons)
        self._received.connect(self._review_image)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._expire)
        try:
            hosts = [self._networks.itemData(i) for i in range(self._networks.count())]
            self._session = PhoneUploadSession(hosts, self._received.emit)
            self.finished.connect(self._session.close)
            self.destroyed.connect(self._session.close)
            self._timer.start(SESSION_SECONDS * 1000)
            self._networks.currentIndexChanged.connect(self._show_link)
            self._show_link()
        except (OSError, ValueError, AttributeError, cv2.error) as exc:
            if self._session:
                self._session.close()
            self._status.setText(f"Could not start phone upload: {exc}. "
                                 "Connect the computer to Wi-Fi, then reopen this window.")
            copy.setEnabled(False)

    def _show_link(self) -> None:
        if self._session is None:
            return
        url = self._session.url(self._networks.currentData())
        self._link.setText(url)
        self._qr.setPixmap(qr_pixmap(url))

    def _review_image(self, image: bytes) -> None:
        if not self.isVisible():
            return
        self._image = image
        pixmap = QPixmap()
        pixmap.loadFromData(image)
        self._qr.setPixmap(pixmap.scaled(300, 280, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        self._networks.setEnabled(False)
        self._use.setEnabled(True)
        self._status.setText("Picture received. Choose Use image to replace the current Trace source, "
                             "or Cancel to keep it unchanged.")
        if self._session:
            self._session.close()
        self._timer.stop()

    def _expire(self) -> None:
        if self._session:
            self._session.close()
        self._qr.clear()
        self._link.clear()
        self._status.setText("This link expired. Close this window and open Upload from phone again.")

    def _use_image(self) -> None:
        if self._image is None:
            return
        path = None
        created = False
        try:
            path = user_data_dir() / "phone_uploads" / f"{uuid.uuid4().hex}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                created = True
                stream.write(self._image)
        except OSError as exc:
            if created and path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    self._status.setText(f"Could not save or clean up {path}: {exc}; {cleanup_error}")
                    return
            self._status.setText(f"Could not save the picture: {exc}. Try again or cancel.")
            return
        self.image_path = str(path)
        self.accept()

    def done(self, result: int) -> None:
        self._timer.stop()
        if self._session:
            self._session.close()
        super().done(result)
