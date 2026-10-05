"""Read the shared Windows cursor without moving it or capturing another frame."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from functools import lru_cache


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT),
    ]


SYSTEM_CURSORS = {
    32512: "default", 32513: "text", 32514: "wait", 32515: "crosshair",
    32516: "default", 32642: "nwse-resize", 32643: "nesw-resize",
    32644: "ew-resize", 32645: "ns-resize", 32646: "move",
    32648: "not-allowed", 32649: "pointer", 32650: "progress", 32651: "help",
    32671: "pin", 32672: "person",
}


@lru_cache(maxsize=1)
def _api():
    if os.name != "nt":
        return None, {}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = wintypes.BOOL
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadCursorW.restype = wintypes.HANDLE
    handles = {}
    for resource, style in SYSTEM_CURSORS.items():
        handle = user32.LoadCursorW(None, ctypes.c_void_p(resource))
        if handle:
            handles[handle] = style if style not in {"pin", "person"} else "default"
    # LoadCursor returns shared handles; these must not be destroyed.
    return user32, handles


def cursor_style() -> str:
    try:
        user32, handles = _api()
        if user32 is None:
            return "default"
        info = CURSORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetCursorInfo(ctypes.byref(info)):
            return "default"
        if not info.flags & 1:  # CURSOR_SHOWING
            return "none"
        return handles.get(info.hCursor, "default")
    except (OSError, AttributeError):
        return "default"
