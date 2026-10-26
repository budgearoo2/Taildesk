"""Keyboard input for game mode, sent as hardware scan codes.

Games (Godot, Unity and others) identify keys by the scan code Windows attaches
to each key message. PyAutoGUI sends scan code 0, so games ignore those keys.
Game mode presses the physical key named by the browser's KeyboardEvent.code
with SendInput and KEYEVENTF_SCANCODE; Windows derives the virtual key from the
scan code, so ordinary windows still receive the right key too.
"""
from __future__ import annotations

import ctypes
import functools
from ctypes import wintypes

_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008

# KeyboardEvent.code -> scan code set 1. Extended keys carry an 0xE0 prefix.
SCAN_CODES = {
    "Escape": 0x01,
    "Digit1": 0x02, "Digit2": 0x03, "Digit3": 0x04, "Digit4": 0x05, "Digit5": 0x06,
    "Digit6": 0x07, "Digit7": 0x08, "Digit8": 0x09, "Digit9": 0x0A, "Digit0": 0x0B,
    "Minus": 0x0C, "Equal": 0x0D, "Backspace": 0x0E, "Tab": 0x0F,
    "KeyQ": 0x10, "KeyW": 0x11, "KeyE": 0x12, "KeyR": 0x13, "KeyT": 0x14,
    "KeyY": 0x15, "KeyU": 0x16, "KeyI": 0x17, "KeyO": 0x18, "KeyP": 0x19,
    "BracketLeft": 0x1A, "BracketRight": 0x1B, "Enter": 0x1C, "ControlLeft": 0x1D,
    "KeyA": 0x1E, "KeyS": 0x1F, "KeyD": 0x20, "KeyF": 0x21, "KeyG": 0x22,
    "KeyH": 0x23, "KeyJ": 0x24, "KeyK": 0x25, "KeyL": 0x26, "Semicolon": 0x27,
    "Quote": 0x28, "Backquote": 0x29, "ShiftLeft": 0x2A, "Backslash": 0x2B,
    "KeyZ": 0x2C, "KeyX": 0x2D, "KeyC": 0x2E, "KeyV": 0x2F, "KeyB": 0x30,
    "KeyN": 0x31, "KeyM": 0x32, "Comma": 0x33, "Period": 0x34, "Slash": 0x35,
    "ShiftRight": 0x36, "NumpadMultiply": 0x37, "AltLeft": 0x38, "Space": 0x39,
    "CapsLock": 0x3A, "F1": 0x3B, "F2": 0x3C, "F3": 0x3D, "F4": 0x3E, "F5": 0x3F,
    "F6": 0x40, "F7": 0x41, "F8": 0x42, "F9": 0x43, "F10": 0x44, "ScrollLock": 0x46,
    "Numpad7": 0x47, "Numpad8": 0x48, "Numpad9": 0x49, "NumpadSubtract": 0x4A,
    "Numpad4": 0x4B, "Numpad5": 0x4C, "Numpad6": 0x4D, "NumpadAdd": 0x4E,
    "Numpad1": 0x4F, "Numpad2": 0x50, "Numpad3": 0x51, "Numpad0": 0x52,
    "NumpadDecimal": 0x53, "IntlBackslash": 0x56, "F11": 0x57, "F12": 0x58,
    "NumpadEnter": 0xE01C, "ControlRight": 0xE01D, "NumpadDivide": 0xE035,
    "PrintScreen": 0xE037, "AltRight": 0xE038, "NumLock": 0xE045,
    "Home": 0xE047, "ArrowUp": 0xE048, "PageUp": 0xE049, "ArrowLeft": 0xE04B,
    "ArrowRight": 0xE04D, "End": 0xE04F, "ArrowDown": 0xE050, "PageDown": 0xE051,
    "Insert": 0xE052, "Delete": 0xE053, "MetaLeft": 0xE05B, "MetaRight": 0xE05C,
    "ContextMenu": 0xE05D,
}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    # Unused, but it is the largest INPUT member and sets the size Windows expects.
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


@functools.lru_cache(maxsize=1)
def _send_input():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    return user32.SendInput


def key_event(code: str, up: bool = False) -> _INPUT:
    scan = SCAN_CODES[code]
    flags = _KEYEVENTF_SCANCODE | (_KEYEVENTF_KEYUP if up else 0)
    if scan > 0xFF:
        flags |= _KEYEVENTF_EXTENDEDKEY
    return _INPUT(type=_INPUT_KEYBOARD, union=_INPUTUNION(ki=_KEYBDINPUT(wScan=scan & 0xFF, dwFlags=flags)))


def send_key(code: str, up: bool = False) -> bool:
    """Press or release a physical key; False if Windows did not accept it.

    Windows silently drops input aimed at an elevated window, like PyAutoGUI's.
    """
    event = key_event(code, up)
    return _send_input()(1, ctypes.byref(event), ctypes.sizeof(_INPUT)) == 1
