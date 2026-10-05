from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_startup(enabled: bool) -> None:
    if os.name != "nt":
        return
    import winreg

    executable = Path(sys.executable)
    if executable.name.lower() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
    if getattr(sys, "frozen", False):
        command = f'"{executable}" --minimized'
    else:
        script = Path(__file__).resolve().parents[1] / "remote_desktop_connection.py"
        command = f'"{executable}" "{script}" --minimized'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, "TailDesk", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "TailDesk")
            except FileNotFoundError:
                pass
