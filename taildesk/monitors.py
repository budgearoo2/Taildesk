"""Enumerate active Windows monitors with stable device IDs and desktop bounds."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def list_monitors() -> list[dict]:
    if os.name != "nt":
        return []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFOEXW)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), callback_type, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    monitors = []

    @callback_type
    def collect(handle, _dc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            rect = info.rcMonitor
            if rect.right > rect.left and rect.bottom > rect.top:
                monitors.append({
                    "id": info.szDevice, "left": rect.left, "top": rect.top,
                    "width": rect.right - rect.left, "height": rect.bottom - rect.top,
                    "primary": bool(info.dwFlags & 1),
                })
        return True

    if not user32.EnumDisplayMonitors(None, None, collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    monitors.sort(key=lambda monitor: (not monitor["primary"], monitor["id"]))
    for index, monitor in enumerate(monitors, 1):
        monitor["label"] = f"Screen {index}" + (" (primary)" if monitor["primary"] else "")
    return monitors
