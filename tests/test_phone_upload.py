"""Exercise the real LAN receiver, QR decoding, desktop review and Trace handoff."""
from __future__ import annotations

import http.client
import io
import socket
import time
from urllib.parse import urlsplit

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from simple_stipple.features.trace import TracePage
from simple_stipple.features.trace import phone_upload as ui
from simple_stipple.platform import phone_upload as receiver


def picture(*, orientation=None):
    image = Image.new("RGB", (80, 50), "white")
    ImageDraw.Draw(image).rectangle((20, 10, 60, 40), fill="black")
    output = io.BytesIO()
    if orientation:
        exif = image.getexif()
        exif[270] = "Private camera description"
        exif[274] = orientation
        image.save(output, "JPEG", exif=exif)
    else:
        image.save(output, "PNG")
    return output.getvalue()


def request(session, method="GET", *, path=None, data=None, headers=None):
    address = urlsplit(session.url("127.0.0.1"))
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=3)
    try:
        connection.request(method, path or address.path, body=data, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


@pytest.fixture
def session():
    received = []
    service = receiver.PhoneUploadSession(["127.0.0.1"], received.append, bind="127.0.0.1")
    yield service, received
    service.close()


def test_session_serves_only_private_page_and_validates_image(session):
    service, received = session
    status, page, headers = request(service)
    assert status == 200 and b'type="file"' in page
    assert headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in headers
    assert request(service, path="/not-the-token")[0] == 404
    assert request(service, path="/../../etc/passwd")[0] == 404
    assert request(service, "POST", path=f"/{service.token}/upload", data=b"not a picture")[0] == 400
    assert not received
    assert request(service, "POST", path=f"/{service.token}/upload", data=picture())[0] == 200
    with Image.open(io.BytesIO(received[0])) as image:
        assert image.size == (80, 50)
        assert image.format == "PNG"
    assert request(service, "POST", path=f"/{service.token}/upload", data=picture())[0] == 409
    assert len(received) == 1


@pytest.mark.parametrize("headers, expected", [
    ({"Origin": "https://hostile.example"}, 403),
    ({"Host": "hostile.example"}, 404),
    ({"Content-Length": str(receiver.MAX_UPLOAD_BYTES + 1)}, 413),
    ({"Content-Length": "nonsense"}, 400),
    ({"Transfer-Encoding": "chunked"}, 413),
])
def test_rejects_cross_origin_rebinding_and_bad_lengths(session, headers, expected):
    service, received = session
    assert request(service, "POST", path=f"/{service.token}/upload", data=b"x",
                   headers=headers)[0] == expected
    assert not received


def test_expiry_and_shutdown_revoke_session(session):
    service, received = session
    service.deadline = time.monotonic() - 1
    assert request(service)[0] == 410
    assert request(service, "POST", path=f"/{service.token}/upload", data=picture())[0] == 410
    assert not received
    service.close()
    service.close()
    assert not service._thread.is_alive()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", service.port), timeout=.5)


def test_normalize_orients_mobile_photo_and_strips_exif():
    data = receiver.normalize_image(picture(orientation=6))
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (50, 80)
        assert not image.getexif()


def test_pixel_limit_is_checked_before_loading(monkeypatch):
    monkeypatch.setattr(receiver, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="megapixels"):
        receiver.normalize_image(picture())


@pytest.fixture
def phone_ui(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "local_addresses", lambda: [("Loopback test network", "127.0.0.1")])
    monkeypatch.setattr(ui, "user_data_dir", lambda: tmp_path)
    return tmp_path


def test_qr_code_decodes_to_the_private_link(qapp):
    url = "http://192.168.1.42:59812/a-valid-private-upload-token-123456789012345"
    image = ui.qr_pixmap(url).toImage().convertToFormat(ui.QImage.Format.Format_Grayscale8)
    pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(pixels[:, :image.width()])
    assert decoded == url


def test_review_and_cancel_leave_no_file(qtbot, phone_ui):
    dialog = ui.PhoneUploadDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    service = dialog._session
    assert service is not None
    assert request(service, "POST", path=f"/{service.token}/upload", data=picture())[0] == 200
    qtbot.waitUntil(dialog._use.isEnabled)
    assert not service._thread.is_alive()
    assert dialog.image_path is None
    dialog.reject()
    assert not list(phone_ui.rglob("*.png"))


def test_dialog_handles_missing_network(qtbot, monkeypatch):
    monkeypatch.setattr(ui, "local_addresses", lambda: [])
    dialog = ui.PhoneUploadDialog()
    qtbot.addWidget(dialog)
    assert not dialog._use.isEnabled()
    assert dialog._session is None
    assert dialog.image_path is None
    dialog.reject()


def test_trace_button_receives_reviews_persists_and_loads_image(qtbot, phone_ui):
    page = TracePage(settings={})
    qtbot.addWidget(page)
    page.show()
    uploads = []

    def send_from_phone():
        dialog = next(widget for widget in QApplication.topLevelWidgets()
                      if isinstance(widget, ui.PhoneUploadDialog) and widget.isVisible())
        service = dialog._session
        assert service is not None
        # The genuine modal dialog receives a real HTTP upload on its worker thread.
        dialog._received.connect(lambda _: QTimer.singleShot(0, dialog._use.click))
        uploads.append(request(service, "POST", path=f"/{service.token}/upload", data=picture())[0])

    QTimer.singleShot(100, send_from_phone)
    try:
        page._phone_upload_btn.click()
        assert uploads == [200]
        assert page._img_path and page._img_path.startswith(str(phone_ui))
        assert page._img_edit.text() == page._img_path
        assert (page._img_w_px, page._img_h_px) == (80, 50)
        with Image.open(page._img_path) as stored:
            assert stored.size == (80, 50)
        qtbot.waitUntil(lambda: page._last_display_img is not None and not page._running, timeout=20000)
        assert page._canvas.poly_count > 0
    finally:
        page.shutdown()
