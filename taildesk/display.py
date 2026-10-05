from __future__ import annotations

import ctypes
import logging
import os
import threading

LOG = logging.getLogger("TailDesk.display")


def _enable_process_dpi_awareness() -> None:
    """Keep screen capture and injected pointer coordinates in physical pixels."""
    if os.name != "nt":
        return
    try:
        set_context = ctypes.windll.user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_bool
        if set_context(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        set_awareness = ctypes.windll.shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        if set_awareness(2) == 0:  # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return
    except (AttributeError, OSError):
        pass
    LOG.warning("Could not set process DPI awareness; pointer scaling may be inaccurate on DPI-scaled displays")


_enable_process_dpi_awareness()


class POINTL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MODE_UNION(ctypes.Union):
    _fields_ = [("position", POINTL), ("orientation", ctypes.c_uint32), ("fixed_output", ctypes.c_uint32)]


class DEVMODEW(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("device_name", ctypes.c_wchar * 32), ("spec_version", ctypes.c_ushort),
        ("driver_version", ctypes.c_ushort), ("size", ctypes.c_ushort),
        ("driver_extra", ctypes.c_ushort), ("fields", ctypes.c_uint32),
        ("union", MODE_UNION), ("color", ctypes.c_short), ("duplex", ctypes.c_short),
        ("y_resolution", ctypes.c_short), ("tt_option", ctypes.c_short),
        ("collate", ctypes.c_short), ("form_name", ctypes.c_wchar * 32),
        ("log_pixels", ctypes.c_ushort), ("bits_per_pixel", ctypes.c_uint32),
        ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
        ("display_flags", ctypes.c_uint32), ("frequency", ctypes.c_uint32),
        ("icm_method", ctypes.c_uint32), ("icm_intent", ctypes.c_uint32),
        ("media_type", ctypes.c_uint32), ("dither_type", ctypes.c_uint32),
        ("reserved1", ctypes.c_uint32), ("reserved2", ctypes.c_uint32),
        ("panning_width", ctypes.c_uint32), ("panning_height", ctypes.c_uint32),
    ]


class DisplayController:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._original: DEVMODEW | None = None
        self._changed = False

    @staticmethod
    def _api():
        if os.name != "nt":
            return None
        return ctypes.windll.user32

    def resize(self, width: int, height: int) -> bool:
        api = self._api()
        if api is None:
            return False
        with self._lock:
            current = DEVMODEW()
            current.size = ctypes.sizeof(DEVMODEW)
            if not api.EnumDisplaySettingsW(None, -1, ctypes.byref(current)):
                return False
            if self._original is None:
                self._original = DEVMODEW.from_buffer_copy(current)
            target = DEVMODEW.from_buffer_copy(current)
            target.width, target.height = width, height
            target.fields = 0x00080000 | 0x00100000  # DM_PELSWIDTH | DM_PELSHEIGHT
            # CDS_FULLSCREEN applies the mode for the current session only.
            result = api.ChangeDisplaySettingsW(ctypes.byref(target), 0x00000004)
            if result == 0:
                self._changed = True
                return True
            # Keep the original mode armed for restoration if a later resize
            # request is rejected after an earlier mode change succeeded.
            return False

    def restore(self) -> None:
        api = self._api()
        with self._lock:
            if api is None or not self._changed:
                return
            try:
                # Reapplying the captured driver mode restores the native mode.
                if self._original is not None:
                    api.ChangeDisplaySettingsW(ctypes.byref(self._original), 0x00000004)
            except Exception:
                LOG.exception("Could not restore the original display resolution")
            finally:
                self._original = None
                self._changed = False
