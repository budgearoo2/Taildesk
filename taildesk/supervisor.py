"""Keep the TailDesk host worker running after crashes or hangs.

The supervisor imports no capture, encoding, or web-server packages, so a native
fault in the worker cannot take it down. It restarts the worker, restores the
desktop the worker may have left changed, and stops only when the user quits.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

LOG = logging.getLogger("TailDesk.supervisor")
WORKER_FLAG = "--worker"
RESTARTED_FLAG = "--restarted"
EXIT_QUIT = 0
EXIT_NO_PASSWORD = 3
STOP_CODES = {EXIT_QUIT, EXIT_NO_PASSWORD}
HEALTH_INTERVAL = 5
HEALTH_TIMEOUT = 5
# Roughly a minute of unanswered checks before a hung worker is replaced.
HEALTH_FAILURES = 6
RESTART_DELAYS = (2, 5, 15, 30, 60)
STABLE_SECONDS = 300
MUTEX_NAME = "Local\\TailDesk.Host"
_mutex = None


def worker_command(argv: list[str], *, restarted: bool) -> list[str]:
    """Launch this same program in worker mode, frozen or from source."""
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / "remote_desktop_connection.py")]
    command.append(WORKER_FLAG)
    # Restarts happen unattended: never reopen the browser or show dialogs.
    if restarted or "--minimized" in argv:
        command.append("--minimized")
    if restarted:
        command.append(RESTARTED_FLAG)
    return command


def acquire_single_instance() -> bool:
    """Return False when another TailDesk host is already running for this user."""
    global _mutex
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _mutex = handle  # Held until this process exits.
    return True


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(url: str) -> dict | None:
    """Return the health payload, or None if the worker did not answer in time."""
    try:
        with _opener.open(urllib.request.Request(url), timeout=HEALTH_TIMEOUT) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read(4096))
            return data if isinstance(data, dict) and data.get("ok") else None
    except (OSError, ValueError):
        return None


def restore_desktop() -> None:
    """Undo display and input changes a crashed or killed worker could not restore."""
    if os.name != "nt":
        return
    user32 = ctypes.windll.user32
    try:
        # TailDesk only applies CDS_FULLSCREEN modes, so the registry still holds
        # each monitor's native mode; a NULL mode reapplies it on every display.
        user32.ChangeDisplaySettingsExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        user32.ChangeDisplaySettingsExW.restype = ctypes.c_long
        result = user32.ChangeDisplaySettingsExW(None, None, None, 0, None)
        if result != 0:
            LOG.warning("Display restoration after a worker failure returned code %s", result)
    except Exception:
        LOG.exception("Could not restore the display mode after a worker failure")
    try:
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint32, ctypes.c_size_t]
        user32.mouse_event.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32, ctypes.c_size_t]
        for button, up_flag in ((0x01, 0x0004), (0x02, 0x0010), (0x04, 0x0040)):
            if user32.GetAsyncKeyState(button) & 0x8000:
                user32.mouse_event(up_flag, 0, 0, 0, 0)
        for key in range(0x08, 0xFF):
            if user32.GetAsyncKeyState(key) & 0x8000:
                user32.keybd_event(key, 0, 0x0002, 0)  # KEYEVENTF_KEYUP
    except Exception:
        LOG.exception("Could not release held input after a worker failure")


def describe_exit(code: int | None) -> str:
    if code is None:
        return "stopped responding"
    return f"exited with code {code} (0x{code & 0xFFFFFFFF:08X})"


class Supervisor:
    def __init__(self, argv: list[str], port: int, *, popen=subprocess.Popen, probe=probe,
                 restore=restore_desktop, clock=time.monotonic, sleep=time.sleep) -> None:
        self.argv = argv
        self.port = port
        self.popen = popen
        self.probe = probe
        self.restore = restore
        self.clock = clock
        self.sleep = sleep

    def run(self) -> int:
        restarted = False
        quick_failures = 0
        while True:
            started = self.clock()
            process = self.popen(worker_command(self.argv, restarted=restarted), close_fds=True)
            code = self._watch(process)
            if code in STOP_CODES:
                return code
            LOG.error("TailDesk worker %s; restarting it", describe_exit(code))
            self.restore()
            quick_failures = 0 if self.clock() - started >= STABLE_SECONDS else quick_failures + 1
            # quick_failures is at least 1 here, so the first restart waits RESTART_DELAYS[0].
            self.sleep(RESTART_DELAYS[min(quick_failures, len(RESTART_DELAYS)) - 1])
            restarted = True

    def _watch(self, process) -> int | None:
        """Wait for the worker to exit; kill it and return None if it hangs."""
        local_url = f"http://127.0.0.1:{self.port}/api/health"
        # Checks only count once a listener has answered: first-run password
        # setup and slow package imports must not look like a hang.
        local_ready = remote_ready = False
        misses = 0
        while True:
            try:
                return process.wait(timeout=HEALTH_INTERVAL)
            except subprocess.TimeoutExpired:
                pass
            status = self.probe(local_url)
            healthy = status is not None
            local_ready = local_ready or healthy
            bind = status.get("bind") if status else None
            if healthy and bind:
                remote = self.probe(f"http://{bind}:{self.port}/api/health") is not None
                remote_ready = remote_ready or remote
                healthy = remote or not remote_ready
            if healthy or not local_ready:
                misses = 0
                continue
            misses += 1
            if misses >= HEALTH_FAILURES:
                LOG.error("TailDesk worker stopped answering health checks; ending process %s", process.pid)
                process.kill()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    LOG.error("TailDesk worker %s did not exit after being ended", process.pid)
                return None
