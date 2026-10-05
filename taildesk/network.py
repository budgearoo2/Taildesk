"""Listener lifecycle and local interface validation."""
from __future__ import annotations

import ipaddress
import logging
import socket
import threading

from waitress import create_server
from waitress import wasyncore
from waitress.task import ThreadedTaskDispatcher

LOG = logging.getLogger("TailDesk.network")


def validate_bind_address(host: str) -> None:
    if host in {"auto", "127.0.0.1"}:
        return
    try:
        address = ipaddress.IPv4Address(host)
        if address.is_unspecified or address.is_multicast or address.is_loopback:
            raise ValueError
    except ValueError:
        raise ValueError("Use auto, 127.0.0.1, or an IPv4 address assigned to this PC.") from None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
    except OSError:
        raise ValueError("That IP address is not available on this PC. Choose auto to use its Tailscale address.") from None


class HostListeners:
    """Keep local settings available even when the remote listener cannot start."""

    def __init__(self, app, host: str, port: int, discover) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.discover = discover
        self.servers = []
        self.stop = threading.Event()
        self.worker = None
        self.lock = threading.RLock()
        app.config.update(
            TAILDESK_BIND=None,
            TAILDESK_PORT=port,
            TAILDESK_START_BIND=host,
            TAILDESK_LISTENER_ERROR=None,
            TAILDESK_LOCAL_ADDRESSES={"127.0.0.1", "::1"},
        )

    def _listen(self, host: str, threads: int) -> None:
        # Bind synchronously so failures are reported before claiming readiness.
        # Separate socket maps prevent two server threads from sharing an event loop.
        with self.lock:
            if self.stop.is_set():
                raise OSError("TailDesk is shutting down")
            socket_map = {}
            dispatcher = ThreadedTaskDispatcher()
            try:
                server = create_server(
                    self.app, host=host, port=self.port, threads=threads,
                    clear_untrusted_proxy_headers=True, map=socket_map, _dispatcher=dispatcher,
                )
                dispatcher.set_thread_count(threads)
            except Exception:
                dispatcher.shutdown()
                wasyncore.close_all(socket_map)
                raise
            self.servers.append((server, socket_map))
            threading.Thread(target=server.run, daemon=True, name=f"taildesk-http-{host}").start()

    def start(self) -> None:
        self._listen("127.0.0.1", 4)
        if self.host == "127.0.0.1":
            self.app.config["TAILDESK_LISTENER_ERROR"] = "Remote access is disabled. Set the bind address to auto and restart TailDesk."
            return
        if not self._start_remote() and self.host == "auto":
            self.worker = threading.Thread(target=self._retry, daemon=True, name="taildesk-wait-for-tailscale")
            self.worker.start()

    def _start_remote(self) -> bool:
        host = self.discover() if self.host == "auto" else self.host
        if not host:
            self.app.config["TAILDESK_LISTENER_ERROR"] = "Waiting for Tailscale. Connect Tailscale on this PC; TailDesk will retry automatically."
            return False
        try:
            validate_bind_address(host)
            self._listen(host, 8)
        except (OSError, ValueError) as exc:
            self.app.config["TAILDESK_LISTENER_ERROR"] = f"Remote access could not start: {exc} Check Settings and restart TailDesk."
            LOG.warning("Remote listener failed on %s:%s: %s", host, self.port, exc)
            return False
        self.app.config["TAILDESK_LOCAL_ADDRESSES"].add(host)
        self.app.config["TAILDESK_BIND"] = host
        self.app.config["TAILDESK_LISTENER_ERROR"] = None
        LOG.info("Remote access ready at http://%s:%s/", host, self.port)
        return True

    def _retry(self) -> None:
        while not self.stop.wait(5):
            if self._start_remote():
                return

    def close(self) -> None:
        self.stop.set()
        with self.lock:
            for server, socket_map in self.servers:
                server.task_dispatcher.shutdown()
                wasyncore.close_all(socket_map)
            self.servers.clear()
