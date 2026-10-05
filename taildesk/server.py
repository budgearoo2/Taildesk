from __future__ import annotations

import io
import ipaddress
import ctypes
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import mss
import pyautogui
import pyperclip
from flask import Flask, abort, jsonify, make_response, render_template, request, send_file, session
from PIL import Image, ImageChops

from taildesk.audio import AudioRouter
from taildesk.config import ConfigStore, DEFAULT_TRANSFER_DIR
from taildesk.display import DisplayController
from taildesk.startup import set_startup
from taildesk.tailscale_serve import configure_https
from taildesk.video import effective_jpeg_quality, encode_delta_frame, validate_frame_rate
from taildesk import __version__

LOG = logging.getLogger("TailDesk.server")
MAX_UPLOAD = 256 * 1024 * 1024
CONNECTION_TIMEOUT = 12
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


def _pointer_position(data: dict[str, Any]) -> tuple[int, int]:
    """Map normalized browser coordinates to the current host input surface."""
    try:
        x_ratio = float(data.get("x", 0))
        y_ratio = float(data.get("y", 0))
    except (TypeError, ValueError):
        abort(400)
    if not (0 <= x_ratio <= 1 and 0 <= y_ratio <= 1):
        abort(400)
    screen_width, screen_height = pyautogui.size()
    return (
        round(x_ratio * max(0, screen_width - 1)),
        round(y_ratio * max(0, screen_height - 1)),
    )


class ConnectionState:
    def __init__(self, display: DisplayController):
        self.display = display
        self.audio = AudioRouter()
        self.audio.driver_installed_callback = self._audio_driver_installed
        self.lock = threading.RLock()
        self.last_seen: float | None = None
        self.connected = False
        self.owner: str | None = None
        self.held_keys: set[str] = set()
        self.held_buttons: set[str] = set()
        self.previous_frame: Image.Image | None = None
        self._stop = threading.Event()
        threading.Thread(target=self._watchdog, daemon=True, name="taildesk-connection-watchdog").start()

    def _audio_driver_installed(self) -> None:
        with self.lock:
            connected = self.connected
        if connected:
            self.audio.connect()

    def heartbeat(self, owner: str, width: int, height: int) -> bool:
        with self.lock:
            if self.connected and self.owner != owner:
                return False
            new_connection = self.owner != owner
            if self.owner != owner:
                self.previous_frame = None
            self.connected = True
            self.owner = owner
            self.last_seen = time.monotonic()
        self.display.resize(width, height)
        if new_connection:
            with self.lock:
                if self.connected and self.owner == owner:
                    self.audio.connect()
        return True

    def can_control(self, owner: str | None) -> bool:
        with self.lock:
            return self.connected and bool(owner) and self.owner == owner

    def disconnect(self, owner: str | None = None) -> None:
        with self.lock:
            if owner is not None and self.owner != owner:
                return
            keys = tuple(self.held_keys)
            buttons = tuple(self.held_buttons)
            self.held_keys.clear()
            self.held_buttons.clear()
            self.previous_frame = None
            self.connected = False
            self.last_seen = None
            self.owner = None
        self.audio.disconnect()
        for key in keys:
            try:
                pyautogui.keyUp(key)
            except Exception:
                LOG.exception("Could not release a held input key")
        for button in buttons:
            try:
                pyautogui.mouseUp(button=button)
            except Exception:
                LOG.exception("Could not release a held mouse button")
        self.display.restore()

    def key_down(self, key: str, repeat: bool = False) -> None:
        with self.lock:
            if key not in self.held_keys:
                pyautogui.keyDown(key)
                self.held_keys.add(key)
            elif repeat:
                # Browser key-repeat events must reach Windows while held.
                pyautogui.keyDown(key)

    def key_up(self, key: str) -> None:
        with self.lock:
            if key in self.held_keys:
                pyautogui.keyUp(key)
                self.held_keys.discard(key)

    def mouse_down(self, button: str, x: int, y: int) -> None:
        with self.lock:
            pyautogui.moveTo(x, y)
            if button not in self.held_buttons:
                pyautogui.mouseDown(button=button)
                self.held_buttons.add(button)

    def mouse_up(self, button: str, x: int, y: int) -> None:
        with self.lock:
            pyautogui.moveTo(x, y)
            if button in self.held_buttons:
                pyautogui.mouseUp(button=button)
                self.held_buttons.discard(button)

    def _watchdog(self) -> None:
        while not self._stop.wait(1):
            with self.lock:
                expired = self.connected and self.last_seen is not None and time.monotonic() - self.last_seen > CONNECTION_TIMEOUT
            if expired:
                LOG.info("Remote browser timed out; restoring the original display mode")
                self.disconnect()


def find_tailscale_ipv4() -> str | None:
    candidates = [shutil.which("tailscale")]
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates.extend(
        str(path)
        for path in (
            program_files / "Tailscale IPN" / "tailscale.exe",
            program_files / "Tailscale" / "tailscale.exe",
            Path(__file__).resolve().parents[1] / "tailscale.exe",
        )
    )
    for executable in candidates:
        if not executable or not Path(executable).exists():
            continue
        try:
            result = subprocess.run([executable, "ip", "-4"], capture_output=True, text=True, timeout=4, check=False)
            address = result.stdout.strip().splitlines()[0]
            if re.fullmatch(r"100\.(?:\d{1,3}\.){2}\d{1,3}", address):
                return address
        except (OSError, subprocess.SubprocessError, IndexError):
            continue
    return None


def _transfer_root(store: ConfigStore) -> Path:
    path = Path(store.snapshot().get("transfer_folder") or DEFAULT_TRANSFER_DIR).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _safe_file(store: ConfigStore, name: str) -> Path:
    base = Path(unquote(name).replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._() -]", "_", base).strip(" .")[:180]
    if not base or base in {".", ".."}:
        raise ValueError("Invalid file name")
    root = _transfer_root(store)
    path = (root / base).resolve()
    if path.parent != root:
        raise ValueError("Invalid file path")
    return path


def create_app(store: ConfigStore, display: DisplayController) -> Flask:
    app = Flask(__name__, template_folder="web", static_folder="web", static_url_path="/static")
    config = store.snapshot()
    app.secret_key = config["secret_key"]
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=False,  # HTTP is limited to the encrypted Tailnet interface.
        MAX_CONTENT_LENGTH=MAX_UPLOAD,
        TAILDESK_STATE=ConnectionState(display),
        TAILDESK_ATTEMPTS=defaultdict(deque),
        TAILDESK_SERVE_URL=None,
        TAILDESK_SERVE_ERROR=None,
    )
    state: ConnectionState = app.config["TAILDESK_STATE"]

    def loopback_client() -> bool:
        # Tailscale Serve is a local reverse proxy, so deny its forwarded Tailnet requests
        # before considering the loopback socket used by the host's own browser.
        if request.headers.get("Tailscale-User-Login") or request.headers.get("Tailscale-User-Name"):
            return False
        try:
            if not ipaddress.ip_address(request.remote_addr or "").is_loopback:
                return False
        except ValueError:
            return False
        hostname = request.host.partition(":")[0].strip("[]").casefold()
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            return False
        fetch_site = request.headers.get("Sec-Fetch-Site")
        return fetch_site in {None, "same-origin", "none"}

    @app.before_request
    def require_auth():
        if loopback_client():
            if not session.get("authenticated"):
                session.clear()
                session["authenticated"] = True
                session["client_id"] = os.urandom(24).hex()
            return None
        if request.path in {"/", "/api/login", "/static/style.css", "/static/app.js"}:
            return None
        if not session.get("authenticated"):
            return jsonify(error="Sign in"), 401
        return None

    @app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    @app.post("/api/login")
    def login():
        address = request.remote_addr or "unknown"
        now = time.monotonic()
        attempts = app.config["TAILDESK_ATTEMPTS"][address]
        while attempts and now - attempts[0] > 60:
            attempts.popleft()
        if len(attempts) >= 8:
            return jsonify(error="Too many attempts; wait a minute and try again."), 429
        password = str((request.get_json(silent=True) or {}).get("password", ""))
        if not store.verify_password(password):
            attempts.append(now)
            time.sleep(0.35)
            return jsonify(error="Incorrect password"), 401
        session.clear()
        session["authenticated"] = True
        session["client_id"] = os.urandom(24).hex()
        return jsonify(ok=True)

    @app.post("/api/logout")
    def logout():
        owner = session.get("client_id")
        session.clear()
        state.disconnect(owner)
        return jsonify(ok=True)

    @app.get("/api/state")
    def get_state():
        settings = store.snapshot()
        return jsonify(
            connected=state.connected,
            version=__version__,
            bind=find_tailscale_ipv4() if settings.get("bind_host") == "auto" else settings.get("bind_host"),
            port=settings.get("port", 8765),
            settings=store.public(),
            local_access=loopback_client(),
            https_url=app.config.get("TAILDESK_SERVE_URL"),
            https_error=app.config.get("TAILDESK_SERVE_ERROR"),
            audio=state.audio.status(),
        )

    @app.post("/api/heartbeat")
    def heartbeat():
        data = request.get_json(silent=True) or {}
        width = max(640, min(3840, int(data.get("width", 0))))
        height = max(360, min(2160, int(data.get("height", 0))))
        if not state.heartbeat(str(session.get("client_id", "")), width, height):
            return jsonify(error="Another browser is currently controlling this PC."), 409
        return jsonify(ok=True)

    @app.post("/api/disconnect")
    def disconnect():
        state.disconnect(session.get("client_id"))
        return jsonify(ok=True)

    @app.get("/api/screen")
    def screen():
        if not state.can_control(session.get("client_id")):
            return jsonify(error="This browser does not own the active desktop session."), 409
        try:
            # The browser may lower quality to keep a slow connection responsive,
            # but it cannot exceed the quality limit configured by the host.
            quality = effective_jpeg_quality(
                store.snapshot().get("jpeg_quality", 65), request.args.get("q")
            )
            with mss.mss() as capture:
                frame = capture.grab(capture.monitors[1])
                image = Image.frombytes("RGB", frame.size, frame.rgb)
            tile_size = 128
            with state.lock:
                if not state.connected or state.owner != session.get("client_id"):
                    return jsonify(error="This browser does not own the active desktop session."), 409
                previous = state.previous_frame
                full = request.args.get("full") == "1" or previous is None or previous.size != image.size
                if not full:
                    changed: list[tuple[int, int, Image.Image]] = []
                    difference = ImageChops.difference(image, previous)
                    for y in range(0, image.height, tile_size):
                        for x in range(0, image.width, tile_size):
                            box = (x, y, min(x + tile_size, image.width), min(y + tile_size, image.height))
                            if difference.crop(box).getbbox():
                                changed.append((x, y, image.crop(box)))
                    total_tiles = ((image.width + tile_size - 1) // tile_size) * ((image.height + tile_size - 1) // tile_size)
                    full = len(changed) > total_tiles * 0.55
                state.previous_frame = image.copy()
            if full:
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                response = make_response(output.getvalue())
                response.headers["Content-Type"] = "image/jpeg"
            elif not changed:
                response = make_response("", 204)
            else:
                # Binary delta frame format avoids JSON/base64's 33% payload
                # expansion. Header: magic, screen width, screen height, tile count.
                response = make_response(encode_delta_frame(image.width, image.height, changed, quality))
                response.headers["Content-Type"] = "application/vnd.taildesk.tiles"
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception:
            LOG.exception("Screen capture failed")
            return jsonify(error="Screen capture failed"), 500

    @app.get("/api/audio")
    def audio_chunk():
        if not state.can_control(session.get("client_id")):
            return jsonify(error="This browser does not own the active desktop session."), 409
        chunk = state.audio.next_chunk()
        if chunk is None:
            return make_response("", 204)
        response = make_response(chunk)
        response.headers["Content-Type"] = "application/octet-stream"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/audio/install")
    def audio_install():
        try:
            message = state.audio.open_driver_setup()
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
            LOG.warning("Could not start the audio driver installation: %s", exc)
            return jsonify(error=str(exc), audio=state.audio.status()), 400
        return jsonify(ok=True, message=message, audio=state.audio.status())

    @app.post("/api/input")
    def input_event():
        if not state.can_control(session.get("client_id")):
            return jsonify(error="This browser does not own the active desktop session."), 409
        data = request.get_json(silent=True) or {}
        kind = data.get("kind")
        if kind == "move":
            x, y = _pointer_position(data)
            pyautogui.moveTo(x, y)
        elif kind == "click":
            button = data.get("button", "left")
            if button not in {"left", "right", "middle"}:
                abort(400)
            if "x" in data and "y" in data:
                x, y = _pointer_position(data)
                pyautogui.moveTo(x, y)
            pyautogui.click(button=button)
        elif kind in {"mouse_down", "mouse_up"}:
            button = data.get("button", "left")
            if button not in {"left", "right", "middle"}:
                abort(400)
            x, y = _pointer_position(data)
            (state.mouse_down if kind == "mouse_down" else state.mouse_up)(button, x, y)
        elif kind in {"down", "up"}:
            key = str(data.get("key", ""))
            if key not in pyautogui.KEYBOARD_KEYS:
                abort(400)
            if kind == "down":
                state.key_down(key, bool(data.get("repeat", False)))
            else:
                state.key_up(key)
        elif kind == "scroll":
            try:
                wheel_delta = max(-2400, min(2400, round(-float(data.get("delta", 0)) * 3)))
            except (TypeError, ValueError):
                abort(400)
            if wheel_delta:
                mouse_event = ctypes.windll.user32.mouse_event
                mouse_event.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32, ctypes.c_size_t]
                mouse_event.restype = None
                mouse_event(0x0800, 0, 0, ctypes.c_uint32(wheel_delta).value, 0)
        else:
            abort(400)
        return jsonify(ok=True)

    @app.route("/api/clipboard", methods=["GET", "POST"])
    def clipboard():
        if not state.can_control(session.get("client_id")):
            return jsonify(error="This browser does not own the active desktop session."), 409
        if not store.snapshot().get("clipboard_enabled", True):
            return jsonify(error="Clipboard sync is disabled"), 403
        try:
            if request.method == "GET":
                return jsonify(text=pyperclip.paste())
            text = str((request.get_json(silent=True) or {}).get("text", ""))[:200_000]
            pyperclip.copy(text)
            return jsonify(ok=True)
        except Exception:
            LOG.exception("Clipboard access failed")
            return jsonify(error="Clipboard access failed"), 500

    @app.get("/api/files")
    def list_files():
        entries = []
        for path in _transfer_root(store).iterdir():
            if path.is_file():
                try:
                    stat = path.stat()
                    entries.append({"name": path.name, "size": stat.st_size, "modified": int(stat.st_mtime)})
                except OSError:
                    continue
        return jsonify(files=entries)

    @app.post("/api/upload")
    def upload():
        if request.content_length is None or request.content_length > MAX_UPLOAD:
            return jsonify(error="Uploads are limited to 256 MB."), 413
        try:
            path = _safe_file(store, request.headers.get("X-File-Name", "upload.bin"))
            data = request.get_data(cache=False)
            if not data:
                return jsonify(error="The selected file is empty."), 400
            path.write_bytes(data)
            return jsonify(ok=True, name=path.name, size=len(data))
        except (OSError, ValueError) as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/api/download/<path:name>")
    def download(name: str):
        try:
            path = _safe_file(store, name)
        except ValueError:
            abort(404)
        if not path.is_file():
            abort(404)
        return send_file(path, as_attachment=True, download_name=path.name, mimetype=mimetypes.guess_type(path.name)[0])

    @app.get("/api/settings")
    def settings_get():
        return jsonify(store.public())

    @app.post("/api/settings")
    def settings_post():
        data: dict[str, Any] = request.get_json(silent=True) or {}
        updates: dict[str, Any] = {}
        if "bind_host" in data:
            host = str(data["bind_host"]).strip()
            if host not in {"auto", "127.0.0.1"}:
                try:
                    parsed_host = ipaddress.IPv4Address(host)
                    if parsed_host.is_unspecified or parsed_host.is_multicast:
                        raise ipaddress.AddressValueError("not a usable interface address")
                except ipaddress.AddressValueError:
                    return jsonify(error="Use auto, localhost, or a specific IPv4 interface address."), 400
            updates["bind_host"] = host
        if "port" in data:
            port = int(data["port"])
            if not 1024 <= port <= 65535:
                return jsonify(error="Port must be from 1024 to 65535."), 400
            updates["port"] = port
        if "fps" in data:
            try:
                fps = validate_frame_rate(data["fps"])
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
            updates["fps"] = fps
        if "jpeg_quality" in data:
            quality = int(data["jpeg_quality"])
            if not 25 <= quality <= 90:
                return jsonify(error="JPEG quality must be from 25 to 90."), 400
            updates["jpeg_quality"] = quality
        if "transfer_folder" in data:
            folder = Path(str(data["transfer_folder"])).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            updates["transfer_folder"] = str(folder.resolve())
        for flag in ("clipboard_enabled", "tailscale_https"):
            if flag in data:
                updates[flag] = bool(data[flag])
        if "startup" in data:
            updates["startup"] = bool(data["startup"])
            set_startup(updates["startup"])
        if "tailscale_https" in updates:
            managed_port = store.snapshot().get("tailscale_https_port")
            https_url, https_error, new_managed_port = configure_https(
                updates["tailscale_https"], int(store.snapshot().get("port", 8765)), managed_port
            )
            app.config["TAILDESK_SERVE_URL"] = https_url
            app.config["TAILDESK_SERVE_ERROR"] = https_error
            updates["tailscale_https_port"] = new_managed_port
        store.update(updates)
        needs_restart = "bind_host" in updates or "port" in updates
        return jsonify(ok=True, restart_required=needs_restart, settings=store.public(), https_url=app.config["TAILDESK_SERVE_URL"], https_error=app.config["TAILDESK_SERVE_ERROR"])

    @app.after_request
    def harden(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
