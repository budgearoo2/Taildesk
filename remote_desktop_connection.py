"""TailDesk Windows host launcher. See README.md for setup and release steps."""

from __future__ import annotations

import atexit
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import Tk, messagebox, simpledialog

from taildesk.config import ConfigStore
from taildesk.display import DisplayController
from taildesk.server import create_app, find_tailscale_ipv4
from taildesk.startup import set_startup
from taildesk.tray import create_tray
from taildesk.tailscale_serve import configure_https

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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.name != "nt":
        raise SystemExit("TailDesk currently runs on Windows 11.")

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

    host = settings.get("bind_host", "auto")
    if host == "auto":
        host = find_tailscale_ipv4() or "127.0.0.1"
    port = int(settings.get("port", 8765))

    if settings.get("tailscale_https", True):
        serve_url, serve_error, managed_port = configure_https(True, port, settings.get("tailscale_https_port"))
        app.config["TAILDESK_SERVE_URL"] = serve_url
        app.config["TAILDESK_SERVE_ERROR"] = serve_error
        if managed_port != settings.get("tailscale_https_port"):
            store.update({"tailscale_https_port": managed_port})
        if serve_error:
            LOG.warning("Private HTTPS clipboard endpoint was not configured: %s", serve_error)
        elif serve_url:
            LOG.info("Private HTTPS URL: %s", serve_url)

    # Waitress is a production WSGI server, unlike Flask's development server.
    from waitress import serve

    server_thread = threading.Thread(
        target=lambda: serve(app, host=host, port=port, threads=8, clear_untrusted_proxy_headers=True),
        name="taildesk-http",
        daemon=True,
    )
    server_thread.start()
    if host != "127.0.0.1":
        threading.Thread(
            target=lambda: serve(app, host="127.0.0.1", port=port, threads=4, clear_untrusted_proxy_headers=True),
            name="taildesk-loopback-http",
            daemon=True,
        ).start()

    local_url = app.config.get("TAILDESK_SERVE_URL") or f"http://{host}:{port}/"
    LOG.info("TailDesk is listening on %s:%s", host, port)
    if host == "127.0.0.1":
        LOG.warning("Tailscale was not detected. Remote access is disabled; connect Tailscale or set the bind address in settings.")
    elif "--minimized" not in sys.argv:
        LOG.info("Connect from another Tailnet device at http://%s:%s", host, port)

    tray = create_tray(
        on_open=lambda: webbrowser.open(app.config.get("TAILDESK_SERVE_URL") or f"http://{host}:{port}/"),
        connected=lambda: bool(connection_state.connected),
        on_quit=connection_state.disconnect,
    )
    if "--minimized" not in sys.argv:
        webbrowser.open(local_url)
    tray.run()
    connection_state.disconnect()
    display.restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
