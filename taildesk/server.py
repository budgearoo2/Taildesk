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
from taildesk.cursor import cursor_style
from taildesk.desktop_access import SECURE_DESKTOP_MESSAGE, desktop_notice, notice_image, secure_desktop_active
from taildesk.display import DisplayController
from taildesk.startup import set_startup
from taildesk.supervisor import SUPERVISOR_PID_ENV
from taildesk import updater
from taildesk.network import validate_bind_address
from taildesk.monitors import list_monitors
from taildesk.tailscale_serve import configure_https
from taildesk.video import effective_jpeg_quality, encode_delta_frame, validate_frame_rate
from taildesk import __version__

LOG = logging.getLogger("TailDesk.server")
MAX_UPLOAD = 256 * 1024 * 1024
CONNECTION_TIMEOUT = 12
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


def _pointer_position(data: dict[str, Any], monitor: dict) -> tuple[int, int]:
    """Map normalized browser coordinates to the current host input surface."""
    try:
        x_ratio = float(data.get("x", 0))
        y_ratio = float(data.get("y", 0))
    except (TypeError, ValueError):
        abort(400)
    if not (0 <= x_ratio <= 1 and 0 <= y_ratio <= 1):
        abort(400)
    return (
        monitor["left"] + round(x_ratio * max(0, monitor["width"] - 1)),
        monitor["top"] + round(y_ratio * max(0, monitor["height"] - 1)),
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
        self.frame_lock = threading.Lock()
        self.monitor_id: str | None = None
        self.monitor_revision = 0
        self.realtime = None
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
            self.current_monitor()
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
            self._disconnect(owner)

    def _disconnect(self, owner: str | None = None) -> None:
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
            if self.realtime:
                self.realtime.close()
        try:
            self.audio.disconnect()
        except Exception:
            LOG.exception("Could not restore audio; continuing to restore input and display")
        self.release_inputs(keys, buttons)
        self.display.restore()

    def release_inputs(self, keys=None, buttons=None) -> None:
        with self.lock:
            if keys is None:
                keys, buttons = tuple(self.held_keys), tuple(self.held_buttons)
                self.held_keys.clear()
                self.held_buttons.clear()
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

    def current_monitor(self) -> dict:
        monitors = list_monitors()
        if not monitors:
            raise ValueError("No active screens were detected on the host.")
        with self.lock:
            monitor = next((item for item in monitors if item["id"] == self.monitor_id), monitors[0])
            if self.monitor_id != monitor["id"]:
                self.release_inputs()
                self.display.select_monitor(monitor["id"])
                self.monitor_id = monitor["id"]
                self.monitor_revision += 1
                self.previous_frame = None
                # Restoring the previous display can move another monitor's bounds.
                monitor = next((item for item in list_monitors() if item["id"] == self.monitor_id), monitor)
            return monitor

    def select_monitor(self, owner: str, monitor_id: str) -> None:
        with self.lock:
            if not self.can_control(owner):
                raise PermissionError("This browser does not own the active desktop session.")
            if monitor_id not in {item["id"] for item in list_monitors()}:
                raise ValueError("That screen is no longer available. Choose a detected screen.")
            if monitor_id != self.monitor_id:
                self.release_inputs()
                self.display.select_monitor(monitor_id)
                self.monitor_id = monitor_id
                self.monitor_revision += 1
                self.previous_frame = None

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
            result = subprocess.run(
                [executable, "ip", "-4"], capture_output=True, text=True, timeout=4,
                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                continue
            address = result.stdout.strip().splitlines()[0]
            if ipaddress.IPv4Address(address) in ipaddress.IPv4Network("100.64.0.0/10"):
                return address
        except (OSError, subprocess.SubprocessError, IndexError, ValueError):
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
        TAILDESK_EXIT_FOR_UPDATE=None,
    )
    state: ConnectionState = app.config["TAILDESK_STATE"]

    def loopback_client() -> bool:
        # Tailscale Serve is a local reverse proxy, so deny its forwarded Tailnet requests
        # before considering the loopback socket used by the host's own browser.
        if any(request.headers.get(name) for name in (
            "Tailscale-User-Login", "Tailscale-User-Name", "Tailscale-Headers-Info",
            "Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
        )):
            return False
        try:
            if not ipaddress.ip_address(request.remote_addr or "").is_loopback:
                return False
        except ValueError:
            return False
        hostname = request.host.split("]", 1)[0].lstrip("[") if request.host.startswith("[") else request.host.partition(":")[0]
        hostname = hostname.casefold()
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            return False
        fetch_site = request.headers.get("Sec-Fetch-Site")
        return fetch_site in {None, "same-origin", "none"}

    def host_browser() -> bool:
        if loopback_client():
            return True
        # A direct connection to this PC's own Tailnet address is still the host.
        # This is only a control restriction, never a password bypass.
        address = request.remote_addr or ""
        local_addresses = app.config.get("TAILDESK_LOCAL_ADDRESSES", set())
        return address not in {"127.0.0.1", "::1"} and address in local_addresses

    @app.before_request
    def require_auth():
        if host_browser() and request.path in {
            "/api/heartbeat", "/api/screen", "/api/input", "/api/audio", "/api/clipboard",
            "/api/monitor", "/api/realtime", "/api/realtime/stop",
        }:
            return jsonify(error="This is the host PC. Open the connection address on your laptop to control it."), 403
        if loopback_client():
            if not session.get("authenticated"):
                session.clear()
                session["authenticated"] = True
                session["client_id"] = os.urandom(24).hex()
            return None
        # All viewer dependencies must load before the remote browser signs in.
        # Templates and other files in the web folder are not public assets.
        if request.path in {"/", "/api/login", "/api/health"} or (
            request.endpoint == "static" and request.view_args.get("filename") in {
                "style.css", "app.js", "screen_protocol.js", "adaptive_stream.js",
                "input_queue.js", "stream_stats.js", "cursor_sync.js", "realtime.js",
            }
        ):
            return None
        if not session.get("authenticated"):
            return jsonify(error="Sign in"), 401
        return None

    @app.get("/api/health")
    def health():
        # The supervisor polls this to detect a hung host. It must stay cheap and
        # never wait on controller locks; only the local supervisor learns the bind.
        return jsonify(ok=True, bind=app.config.get("TAILDESK_BIND") if loopback_client() else None)

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
        bind = app.config.get("TAILDESK_BIND")
        port = app.config.get("TAILDESK_PORT", settings.get("port", 8765))
        return jsonify(
            connected=state.connected,
            version=__version__,
            bind=bind,
            port=port,
            remote_url=f"http://{bind}:{port}/" if bind else None,
            listener_error=app.config.get("TAILDESK_LISTENER_ERROR"),
            restart_required=(settings.get("bind_host") != app.config.get("TAILDESK_START_BIND", settings.get("bind_host")) or settings.get("port") != port),
            settings=store.public(),
            local_access=loopback_client(),
            host_browser=host_browser(),
            https_url=app.config.get("TAILDESK_SERVE_URL"),
            https_error=app.config.get("TAILDESK_SERVE_ERROR"),
            audio=state.audio.status(),
            monitors=list_monitors(),
            selected_monitor=state.monitor_id,
            realtime=True,
        )

    @app.post("/api/heartbeat")
    def heartbeat():
        data = request.get_json(silent=True) or {}
        width = max(640, min(3840, int(data.get("width", 0))))
        height = max(360, min(2160, int(data.get("height", 0))))
        try:
            if not state.heartbeat(str(session.get("client_id", "")), width, height):
                return jsonify(error="Another browser is currently controlling this PC."), 409
        except ValueError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(ok=True, monitors=list_monitors(), selected_monitor=state.monitor_id, notice=desktop_notice())

    @app.post("/api/monitor")
    def select_monitor():
        try:
            state.select_monitor(str(session.get("client_id", "")), str((request.get_json(silent=True) or {}).get("monitor", "")))
        except PermissionError as exc:
            return jsonify(error=str(exc)), 409
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(ok=True, monitors=list_monitors(), selected_monitor=state.monitor_id)

    @app.post("/api/disconnect")
    def disconnect():
        state.disconnect(session.get("client_id"))
        return jsonify(ok=True)

    @app.get("/api/screen")
    def screen():
        with state.frame_lock:
            return screen_frame()

    def screen_frame():
        if not state.can_control(session.get("client_id")):
            return jsonify(error="This browser does not own the active desktop session."), 409
        try:
            # The browser may lower quality to keep a slow connection responsive,
            # but it cannot exceed the quality limit configured by the host.
            quality = effective_jpeg_quality(
                store.snapshot().get("jpeg_quality", 65), request.args.get("q")
            )
            with state.lock:
                monitor = state.current_monitor()
                revision = state.monitor_revision
                previous = state.previous_frame
                if request.args.get("monitor") and request.args["monitor"] != monitor["id"]:
                    return make_response("", 204)
            if secure_desktop_active():
                # Windows refuses capture of UAC's secure desktop; explain instead of failing,
                # and send a full frame once the normal desktop returns.
                output = io.BytesIO()
                notice_image(monitor["width"], monitor["height"], SECURE_DESKTOP_MESSAGE).save(output, format="JPEG", quality=quality)
                with state.lock:
                    if revision == state.monitor_revision:
                        state.previous_frame = None
                response = make_response(output.getvalue())
                response.headers["Content-Type"] = "image/jpeg"
                response.headers["X-TailDesk-Monitor"] = monitor["id"]
                response.headers["Cache-Control"] = "no-store"
                return response
            with mss.mss() as capture:
                frame = capture.grab({key: monitor[key] for key in ("left", "top", "width", "height")})
                image = Image.frombytes("RGB", frame.size, frame.bgra, "raw", "BGRX")
            tile_size = 128
            # Image comparison/encoding must not hold the input/controller lock.
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
            if full:
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=False)
                response = make_response(output.getvalue())
                response.headers["Content-Type"] = "image/jpeg"
            elif not changed:
                response = make_response("", 204)
            else:
                # Binary delta frame format avoids JSON/base64's 33% payload
                # expansion. Header: magic, screen width, screen height, tile count.
                response = make_response(encode_delta_frame(image.width, image.height, changed, quality))
                response.headers["Content-Type"] = "application/vnd.taildesk.tiles"
            with state.lock:
                if not state.can_control(session.get("client_id")) or revision != state.monitor_revision:
                    return make_response("", 204)
                state.previous_frame = image
            response.headers["X-TailDesk-Monitor"] = monitor["id"]
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

    @app.post("/api/realtime")
    def realtime_offer():
        owner = session.get("client_id")
        with state.lock:
            if not state.can_control(owner):
                return jsonify(error="This browser does not own the active desktop session."), 409
            data = request.get_json(silent=True) or {}
            sdp = data.get("sdp")
            if not isinstance(sdp, str) or len(sdp) > 64000 or data.get("type") != "offer":
                return jsonify(error="Invalid media offer"), 400
            if state.realtime is None:
                from taildesk.realtime import RealtimeSession
                state.realtime = RealtimeSession(state)
        def realtime_input(data, monitor):
            with app.test_request_context():
                apply_input(data, monitor)
        try:
            answer = state.realtime.offer(owner, sdp, validate_frame_rate(store.snapshot().get("fps", 60)), realtime_input)
            return jsonify(answer)
        except Exception:
            LOG.exception("Realtime negotiation failed")
            return jsonify(error="Realtime video unavailable; using compatibility mode."), 503

    @app.post("/api/realtime/stop")
    def realtime_stop():
        if not state.can_control(session.get("client_id")):
            return jsonify(error="Desktop session ended"), 409
        if state.realtime:
            state.realtime.close()
        state.release_inputs()
        return jsonify(ok=True)

    update_lock = threading.Lock()

    @app.post("/api/update")
    def update_install():
        """Check GitHub and, when newer, install the verified release and restart."""
        exit_for_update = app.config.get("TAILDESK_EXIT_FOR_UPDATE")
        if exit_for_update is None:
            return jsonify(error="Updates can only be installed by the running TailDesk app."), 503
        if not update_lock.acquire(blocking=False):
            return jsonify(error="An update is already in progress."), 409
        try:
            # Setup must wait for the supervisor, which holds the installed program files.
            version = updater.install_latest(int(os.environ.get(SUPERVISOR_PID_ENV) or os.getpid()))
        except updater.UpdateError as exc:
            update_lock.release()
            LOG.warning("Update failed: %s", exc)
            return jsonify(error=str(exc)), 503
        if version is None:
            update_lock.release()
            return jsonify(ok=True, updating=False, current=__version__)
        LOG.info("Installing TailDesk %s; closing so setup can replace the program files", version)
        # Let this response reach the browser before the host closes.
        threading.Timer(1.0, exit_for_update).start()
        return jsonify(ok=True, updating=True, current=__version__, version=version)

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
        with state.lock:
            if not state.can_control(session.get("client_id")):
                return jsonify(error="This browser does not own the active desktop session."), 409
            data = request.get_json(silent=True) or {}
            try:
                monitor = state.current_monitor()
            except ValueError as exc:
                return jsonify(error=str(exc)), 409
            if data.get("monitor") and data["monitor"] != monitor["id"]:
                return jsonify(error="The selected screen changed; input was discarded."), 409
            return apply_input(data, monitor)

    def apply_input(data, monitor):
        kind = data.get("kind")
        if kind == "move":
            x, y = _pointer_position(data, monitor)
            pyautogui.moveTo(x, y)
        elif kind == "click":
            button = data.get("button", "left")
            if button not in {"left", "right", "middle"}:
                abort(400)
            if "x" in data and "y" in data:
                x, y = _pointer_position(data, monitor)
                pyautogui.moveTo(x, y)
            pyautogui.click(button=button)
        elif kind in {"mouse_down", "mouse_up"}:
            button = data.get("button", "left")
            if button not in {"left", "right", "middle"}:
                abort(400)
            x, y = _pointer_position(data, monitor)
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
            try:
                validate_bind_address(host)
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
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
        if request.path in {"/api/screen", "/api/input"} and response.status_code in {200, 204} and state.can_control(session.get("client_id")):
            response.headers["X-TailDesk-Cursor"] = cursor_style()
            response.headers["X-TailDesk-Cursor-Stamp"] = str(time.monotonic_ns() // 1000)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
