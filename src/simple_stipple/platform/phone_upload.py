"""Short-lived, token-protected LAN image receiver. No cloud or arbitrary file serving."""
from __future__ import annotations

import hmac
import io
import ipaddress
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
SESSION_SECONDS = 600

_PAGE = b'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Send to Simple Stipple</title><style>
body{margin:0;background:#eef2f5;color:#172b3a;font:18px/1.5 system-ui,sans-serif}
main{max-width:30rem;margin:8vh auto;padding:24px}h1{font-size:2rem;line-height:1.15}
p{max-width:34ch}input,button{font:inherit;max-width:100%;box-sizing:border-box}
input{display:block;margin:28px 0}button{background:#245e86;color:white;border:0;
padding:14px 22px;border-radius:6px;cursor:pointer}button:disabled{opacity:.55}
:focus-visible{outline:3px solid #bc581e;outline-offset:4px}small{display:block;margin-top:28px}
</style><main><h1>Send a picture to Trace</h1>
<p>Choose a photo or take a new one. Review it on your computer before tracing.</p>
<form id="form"><label for="image">Picture</label>
<input id="image" type="file" accept="image/*" required>
<button id="send">Send picture</button></form>
<p id="status" role="status" aria-live="polite"></p>
<small>Same Wi-Fi network only. Up to 20 MB and 24 megapixels. This private link expires
when you close the computer's upload window, or after 10 minutes.</small></main>
<script>document.getElementById('form').addEventListener('submit',async(event)=>{
event.preventDefault();const button=document.getElementById('send');
const status=document.getElementById('status');const file=document.getElementById('image').files[0];
if(!file)return;if(file.size>20971520){status.textContent='Choose a picture smaller than 20 MB.';return;}
button.disabled=true;status.textContent='Sending picture...';
try{const response=await fetch(location.pathname.replace(/\\/$/,'')+'/upload',
{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
const text=await response.text();status.textContent=text;
if(!response.ok)button.disabled=false;
}catch(error){status.textContent='Could not reach the computer. Check Wi-Fi and reopen the QR window.';
button.disabled=false;}});</script></html>'''


def normalize_image(data: bytes) -> bytes:
    """Validate actual image content, orient phone photos, and strip source metadata."""
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Choose an image between 1 byte and 20 MB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("Choose a picture no larger than 24 megapixels.")
            if source.format not in {"PNG", "JPEG", "WEBP", "BMP", "TIFF", "GIF"}:
                raise ValueError("Use a PNG, JPEG, WebP, BMP, TIFF or GIF image.")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.info.clear()
            output = io.BytesIO()
            image.save(output, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("This is not a readable image. Try a JPEG or PNG export.") from exc
    result = output.getvalue()
    if len(result) > MAX_UPLOAD_BYTES:
        raise ValueError("The decoded picture is too large. Resize it and try again.")
    return result


class _UploadServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, bind: str, session: PhoneUploadSession) -> None:
        self.session = session
        self._slots = threading.BoundedSemaphore(4)
        super().__init__((bind, 0), _UploadHandler)

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class _UploadHandler(BaseHTTPRequestHandler):
    server: _UploadServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args) -> None:
        # The default logger exposes the private session token in URLs.
        return

    def do_GET(self) -> None:
        if self.authorized(""):
            self.reply(200, _PAGE, "text/html; charset=utf-8")

    def authorized(self, suffix: str) -> bool:
        session = self.server.session
        if session._closed or time.monotonic() >= session.deadline:
            self.reply(410, b"This upload link expired. Open a new QR window on the computer.")
            return False
        host = self.headers.get("Host", "")
        valid_hosts = {f"{host}:{session.port}" for host in session.hosts}
        if host not in valid_hosts or not hmac.compare_digest(
            self.path.encode(), f"/{session.token}{suffix}".encode()
        ):
            self.reply(404, b"Upload session not found.")
            return False
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{host}":
            self.reply(403, b"Use the page opened by the QR code.")
            return False
        return True

    def do_POST(self) -> None:
        if not self.authorized("/upload"):
            return
        session = self.server.session
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.reply(400, b"A valid Content-Length is required.")
            return
        if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_UPLOAD_BYTES:
            self.reply(413, b"Choose a picture smaller than 20 MB.")
            return
        with session._lock:
            if session._pending:
                self.reply(409, b"A picture is already waiting for review on the computer.")
                return
            session._pending = True
        try:
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("The upload was interrupted. Try again.")
            image = normalize_image(data)
        except (ValueError, TimeoutError, OSError) as exc:
            with session._lock:
                session._pending = False
            self.reply(400, str(exc).encode("utf-8"))
            return
        with session._lock:
            if session._closed or time.monotonic() >= session.deadline:
                self.reply(410, b"This upload session has closed.")
                return
            session.received(image)
        self.reply(200, b"Picture sent. Choose Use image on your computer to load it into Trace.")

    def reply(self, code: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                         "script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # A phone navigating away must not crash an otherwise valid session.
            return


class PhoneUploadSession:
    """Own one bounded listener and one upload; callbacks run on a request thread."""

    def __init__(self, hosts: Sequence[str], received: Callable[[bytes], None],
                 *, bind: str = "0.0.0.0", lifetime: float = SESSION_SECONDS) -> None:
        self.hosts = tuple(str(ipaddress.IPv4Address(host)) for host in hosts)
        if not self.hosts:
            raise ValueError("No local IPv4 address is available.")
        self.token = secrets.token_urlsafe(32)
        self.deadline = time.monotonic() + lifetime
        self.received = received
        self._lock = threading.RLock()
        self._pending = False
        self._closed = False
        self._server = _UploadServer(bind, self)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": .1}, daemon=True)
        self._thread.start()

    def url(self, host: str) -> str:
        if host not in self.hosts:
            raise ValueError("Choose one of this computer's network addresses.")
        return f"http://{host}:{self.port}/{self.token}"

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
