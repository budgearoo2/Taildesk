"""TailDesk Windows host launcher. See README.md for setup and release steps."""

from __future__ import annotations

import atexit
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
import time
import webbrowser
from tkinter import Tk, messagebox, simpledialog

from taildesk.config import APP_DIR, ConfigStore
from taildesk.supervisor import EXIT_NO_PASSWORD, EXIT_QUIT, EXIT_UPDATE, RESTARTED_FLAG, WORKER_FLAG, Supervisor, acquire_single_instance

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


def configure_logging(name: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    # Each process rotates its own file; Windows cannot rename a log another process holds open.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[RotatingFileHandler(APP_DIR / name, maxBytes=1_000_000, backupCount=2, encoding="utf-8")],
    )


def supervise() -> int:
    """Start the host worker and bring it back after crashes or hangs."""
    configure_logging("taildesk-supervisor.log")
    if os.name != "nt":
        raise SystemExit("TailDesk currently runs on Windows 11.")
    port = int(ConfigStore().snapshot().get("port", 8765))
    if not acquire_single_instance():
        # Start menu launches while TailDesk is running just open its settings.
        if "--minimized" not in sys.argv:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return 0

    from taildesk.updater import check_startup_update

    if check_startup_update():
        return 0
    LOG.info("TailDesk supervisor started")
    code = Supervisor(sys.argv, port).run()
    LOG.info("TailDesk supervisor stopped (worker exit code %s)", code)
    return code


def main() -> int:
    configure_logging("taildesk.log")
    if os.name != "nt":
        raise SystemExit("TailDesk currently runs on Windows 11.")
    # Native faults in capture or encoding libraries bypass Python logging.
    crash_path = APP_DIR / "taildesk-crash.log"
    oversized = crash_path.exists() and crash_path.stat().st_size > 1_000_000
    crash_log = crash_path.open("w" if oversized else "a", encoding="utf-8")
    crash_log.write(f"--- TailDesk worker {os.getpid()} started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    crash_log.flush()
    faulthandler.enable(file=crash_log, all_threads=True)

    from taildesk.display import DisplayController
    from taildesk.network import HostListeners
    from taildesk.server import create_app, find_tailscale_ipv4
    from taildesk.startup import set_startup
    from taildesk.tailscale_serve import configure_https
    from taildesk.tray import create_tray
    from taildesk.updater import configure_updates

    store = ConfigStore()
    if not store.has_password() and not make_password(store):
        return EXIT_NO_PASSWORD

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
    exit_code = EXIT_QUIT

    def exit_for_update() -> None:
        # Setup is already running and waiting; the supervisor exits on this code.
        nonlocal exit_code
        exit_code = EXIT_UPDATE
        tray.stop()

    app.config["TAILDESK_EXIT_FOR_UPDATE"] = exit_for_update
    if "--minimized" not in sys.argv:
        webbrowser.open(local_url)
    try:
        tray.run()
    finally:
        connection_state.disconnect()
        display.restore()
        listeners.close()
    return exit_code


if __name__ == "__main__":
    if WORKER_FLAG not in sys.argv:
        raise SystemExit(supervise())
    try:
        raise SystemExit(main())
    except Exception as exc:
        LOG.exception("TailDesk could not start")
        if RESTARTED_FLAG in sys.argv:
            # The supervisor retries with backoff; do not stack dialogs on an unattended host.
            raise SystemExit(1)
        root = Tk()
        root.withdraw()
        messagebox.showerror(
            APP_NAME,
            f"TailDesk could not start:\n{exc}\n\nIf it is already running, use its tray icon. "
            f"Details are saved in {APP_DIR / 'taildesk.log'}.", parent=root,
        )
        root.destroy()
        raise SystemExit(1)
