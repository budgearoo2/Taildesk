"""TailDesk Windows host launcher. See README.md for setup and release steps."""

from __future__ import annotations

import atexit
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import Tk, messagebox, simpledialog

from taildesk.config import APP_DIR, ConfigStore
from taildesk.display import DisplayController
from taildesk.server import create_app, find_tailscale_ipv4
from taildesk.startup import set_startup
from taildesk.network import HostListeners
from taildesk.tray import create_tray
from taildesk.tailscale_serve import configure_https
from taildesk.updater import check_startup_update, configure_updates

APP_NAME = "TailDesk"
LOG = logging.getLogger(APP_NAME)


def make_password(store: ConfigStore) -> bool:
    root = Tk()
    root.withdraw()
    try:
        while True:
            value = simpledialog.askstring(
                APP_NAME,
                "Create an admin password (14 or more characters):",
                show="*",
                parent=root,
            )
            if value is None:
                return False
            if len(value) < 14:
                messagebox.showerror(APP_NAME, "Use at least 14 characters.", parent=root)
                continue
            again = simpledialog.askstring(APP_NAME, "Confirm the password:", show="*", parent=root)
            if value != again:
                messagebox.showerror(APP_NAME, "The passwords did not match.", parent=root)
                continue
            store.set_password(value)
            messagebox.showinfo(
                APP_NAME,
                "Password saved as a salted hash. TailDesk cannot recover it if you forget it.",
                parent=root,
            )
            return True
    finally:
        root.destroy()


def main() -> int:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[RotatingFileHandler(APP_DIR / "taildesk.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")],
    )
    if os.name != "nt":
        raise SystemExit("TailDesk currently runs on Windows 11.")

    if check_startup_update():
        return 0

    store = ConfigStore()
    if not store.has_password() and not make_password(store):
        return 1

    settings = store.snapshot()
    if settings.get("startup", True):
        set_startup(True)

    display = DisplayController()
    atexit.register(display.restore)
    app = create_app(store=store, display=display)
    connection_state = app.config["TAILDESK_STATE"]
    atexit.register(connection_state.disconnect)

    host = settings.get("bind_host", "auto")
    port = int(settings.get("port", 8765))
    listeners = HostListeners(app, host, port, find_tailscale_ipv4)
    try:
        listeners.start()
    except (OSError, ValueError):
        connection_state.disconnect()
        listeners.close()
        raise
    atexit.register(listeners.close)

    def setup_https() -> None:
        if not settings.get("tailscale_https", True):
            return
        serve_url, serve_error, managed_port = configure_https(True, port, settings.get("tailscale_https_port"))
        app.config["TAILDESK_SERVE_URL"] = serve_url
        app.config["TAILDESK_SERVE_ERROR"] = serve_error
        if managed_port != settings.get("tailscale_https_port"):
            store.update({"tailscale_https_port": managed_port})
        if serve_error:
            LOG.warning("Private HTTPS clipboard endpoint was not configured: %s", serve_error)
        elif serve_url:
            LOG.info("Private HTTPS URL: %s", serve_url)

    # Optional HTTPS setup must not delay the tray or local recovery settings.
    threading.Thread(target=setup_https, daemon=True, name="taildesk-https-setup").start()

    local_url = f"http://127.0.0.1:{port}/"
    LOG.info("Host settings ready at %s", local_url)

    tray = create_tray(
        on_open=lambda: webbrowser.open(local_url),
        connected=lambda: bool(connection_state.connected),
        on_quit=connection_state.disconnect,
        on_install_audio=connection_state.audio.open_driver_setup,
        on_configure_updates=configure_updates,
    )
    if "--minimized" not in sys.argv:
        webbrowser.open(local_url)
    try:
        tray.run()
    finally:
        connection_state.disconnect()
        display.restore()
        listeners.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        LOG.exception("TailDesk could not start")
        root = Tk()
        root.withdraw()
        messagebox.showerror(
            APP_NAME,
            f"TailDesk could not start:\n{exc}\n\nIf it is already running, use its tray icon. "
            f"Details are saved in {APP_DIR / 'taildesk.log'}.", parent=root,
        )
        root.destroy()
        raise SystemExit(1)
