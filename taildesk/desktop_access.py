"""Detect host screens Windows does not let a normal user process capture or control."""
from __future__ import annotations

import ctypes
import functools
import os
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont

SECURE_DESKTOP_MESSAGE = (
    "The host is showing a Windows security prompt (UAC) or the lock screen. "
    "Windows does not let TailDesk see or control it. Approve or cancel it on the host; "
    "the picture returns automatically."
)
CAPTURE_PAUSED_MESSAGE = "The host screen could not be captured. TailDesk is retrying."
ELEVATED_WINDOW_MESSAGE = (
    "The active host window is running as administrator. Windows blocks TailDesk's "
    "mouse and keyboard input to it; switch to another window or use it on the host."
)
_TOKEN_QUERY = 0x0008
_TOKEN_INTEGRITY_LEVEL = 25
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5


@functools.lru_cache(maxsize=1)
def _api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    return user32, kernel32, advapi32


def secure_desktop_active() -> bool:
    """True while UAC's secure desktop or the lock screen owns input.

    A process in the user's session can open the input desktop only while it is
    the ordinary Default desktop; Winlogon's secure desktop refuses access.
    """
    if os.name != "nt":
        return False
    user32 = _api()[0]
    desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desktop:
        return True
    user32.CloseDesktop(desktop)
    return False


def _integrity_level(process) -> int | None:
    _, kernel32, advapi32 = _api()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, _TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not size.value or not advapi32.GetTokenInformation(token, _TOKEN_INTEGRITY_LEVEL, buffer, size, ctypes.byref(size)):
            return None
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_MANDATORY_LABEL.Label.Sid
        count = advapi32.GetSidSubAuthorityCount(sid)[0]
        return advapi32.GetSidSubAuthority(sid, count - 1)[0]
    finally:
        kernel32.CloseHandle(token)


def foreground_window_elevated() -> bool:
    """True when the focused window belongs to a process above TailDesk's integrity level."""
    if os.name != "nt":
        return False
    user32, kernel32, _ = _api()
    window = user32.GetForegroundWindow()
    if not window:
        return False
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
    if not process_id.value or process_id.value == os.getpid():
        return False
    process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
    if not process:
        return False
    try:
        theirs = _integrity_level(process)
        if theirs is None:
            # An elevated process's token denies queries from a standard-rights process.
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        ours = _integrity_level(kernel32.GetCurrentProcess())
        return ours is not None and theirs > ours
    finally:
        kernel32.CloseHandle(process)


def desktop_notice() -> str | None:
    if secure_desktop_active():
        return SECURE_DESKTOP_MESSAGE
    if foreground_window_elevated():
        return ELEVATED_WINDOW_MESSAGE
    return None


@functools.lru_cache(maxsize=4)
def notice_image(width: int, height: int, message: str) -> Image.Image:
    """A dark host-sized frame explaining why the real screen is not shown."""
    width, height = max(2, width), max(2, height)
    image = Image.new("RGB", (width, height), "#101820")
    draw = ImageDraw.Draw(image)
    size = max(14, min(width, height) // 28)
    try:
        font = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeui.ttf"), size)
    except OSError:
        font = ImageFont.load_default()
    lines, line = [], ""
    for word in message.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=font) > width * 0.8:
            lines.append(line)
            line = word
        else:
            line = candidate
    lines.append(line)
    spacing = int(size * 1.5)
    top = (height - spacing * len(lines)) // 2
    for index, text in enumerate(lines):
        left = (width - draw.textlength(text, font=font)) // 2
        draw.text((left, top + index * spacing), text, fill="#e8eef5", font=font)
    return image
