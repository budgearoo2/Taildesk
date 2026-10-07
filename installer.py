from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import ctypes
import winreg
import zipfile
from pathlib import Path
from tkinter import BooleanVar, Tk, filedialog, messagebox, ttk

APP_NAME = "TailDesk"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPDATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / APP_NAME
START_MENU_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
# Paths travel in environment variables so folder names are never parsed as script.
SHORTCUT_SCRIPT = (
    "$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($env:TAILDESK_SHORTCUT); "
    "$shortcut.TargetPath = $env:TAILDESK_TARGET; "
    "$shortcut.WorkingDirectory = $env:TAILDESK_DIR; "
    "$shortcut.IconLocation = $env:TAILDESK_TARGET + ',0'; "
    "$shortcut.Description = 'Start the TailDesk remote desktop host'; "
    "$shortcut.Save()"
)


def payload_dir() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "payload" / APP_NAME


def set_startup(install_dir: Path, enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            command = f'"{install_dir / (APP_NAME + ".exe")}" --minimized'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def create_start_menu_shortcut(install_dir: Path) -> bool:
    """Add TailDesk to the Start menu so Windows search can find and relaunch it."""
    try:
        START_MENU_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", SHORTCUT_SCRIPT],
            env={
                **os.environ,
                "TAILDESK_SHORTCUT": str(START_MENU_DIR / f"{APP_NAME}.lnk"),
                "TAILDESK_TARGET": str(install_dir / f"{APP_NAME}.exe"),
                "TAILDESK_DIR": str(install_dir),
            },
            capture_output=True, timeout=60, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def wait_for_process_exit(process_id: int, timeout_seconds: int = 45) -> None:
    synchronize = 0x00100000
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(synchronize, False, process_id)
    if not handle:
        return
    try:
        result = kernel.WaitForSingleObject(handle, timeout_seconds * 1000)
        if result == 0x00000102:
            raise TimeoutError("TailDesk is still closing. Run setup again after it exits.")
        if result != 0:
            raise ctypes.WinError()
    finally:
        kernel.CloseHandle(handle)


def install(install_dir: Path, startup: bool | None, launch: bool, minimized: bool = False) -> bool:
    source = payload_dir()
    if not (source / f"{APP_NAME}.exe").is_file():
        raise FileNotFoundError("The bundled TailDesk application files are missing.")

    install_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, install_dir, dirs_exist_ok=True)

    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    settings = APPDATA_DIR / "settings.json"
    if not settings.exists():
        startup = bool(startup)
        settings.write_text(json.dumps({"startup": startup}, indent=2), encoding="utf-8")
    elif startup is not None:
        try:
            existing = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict):
            existing["startup"] = startup
            temporary = settings.with_suffix(".installer.tmp")
            temporary.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            temporary.replace(settings)
    if startup is None:
        try:
            startup = bool(json.loads(settings.read_text(encoding="utf-8")).get("startup", False))
        except (OSError, json.JSONDecodeError, AttributeError):
            startup = False
    set_startup(install_dir, startup)
    shortcut_created = create_start_menu_shortcut(install_dir)

    if launch:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        subprocess.Popen(
            [str(install_dir / f"{APP_NAME}.exe"), *( ["--minimized"] if minimized else [])],
            cwd=install_dir,
            creationflags=flags,
            close_fds=True,
        )
    return shortcut_created


def main() -> int:
    silent = "--silent" in sys.argv
    launch = "--no-launch" not in sys.argv
    wait_pid_arg = next((item.partition("=")[2] for item in sys.argv if item.startswith("--wait-pid=")), "")
    target_arg = next((item.partition("=")[2] for item in sys.argv if item.startswith("--target=")), "")
    target_dir = Path(target_arg).expanduser() if target_arg else DEFAULT_DIR
    try:
        if silent:
            if wait_pid_arg:
                wait_for_process_exit(int(wait_pid_arg))
            install(target_dir, startup=None, launch=launch, minimized=True)
            return 0

        root = Tk()
        root.title(f"Install {APP_NAME}")
        root.resizable(False, False)
        root.geometry("470x260")
        frame = ttk.Frame(root, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Install TailDesk", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="TailDesk includes its Python runtime and required packages. No separate Python setup is needed.",
            wraplength=420,
        ).pack(anchor="w", pady=(8, 16))

        path_var = __import__("tkinter").StringVar(value=str(target_dir))
        path_row = ttk.Frame(frame)
        path_row.pack(fill="x")
        ttk.Entry(path_row, textvariable=path_var).pack(side="left", fill="x", expand=True)

        def choose_folder() -> None:
            chosen = filedialog.askdirectory(initialdir=path_var.get(), parent=root)
            if chosen:
                path_var.set(chosen)

        ttk.Button(path_row, text="Browse…", command=choose_folder).pack(side="left", padx=(8, 0))
        saved_startup = True
        try:
            saved_startup = bool(json.loads((APPDATA_DIR / "settings.json").read_text(encoding="utf-8")).get("startup", True))
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        startup_var = BooleanVar(value=saved_startup)
        launch_var = BooleanVar(value=True)
        ttk.Checkbutton(
            frame, text="Start TailDesk when I sign in to Windows", variable=startup_var
        ).pack(anchor="w", pady=(16, 3))
        ttk.Checkbutton(frame, text="Open TailDesk after setup", variable=launch_var).pack(anchor="w")

        def do_install() -> None:
            target = Path(path_var.get()).expanduser()
            try:
                shortcut_created = install(target, startup_var.get(), launch_var.get())
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                messagebox.showerror(APP_NAME, f"Setup could not finish:\n{exc}", parent=root)
                return
            if shortcut_created:
                message = "TailDesk is installed. Search for TailDesk in the Start menu to open it again."
            else:
                message = f"TailDesk is installed, but its Start menu shortcut could not be created. Start it from {target}."
            messagebox.showinfo(APP_NAME, message, parent=root)
            root.destroy()

        ttk.Button(frame, text="Install", command=do_install).pack(anchor="e", pady=(14, 0))
        root.mainloop()
        return 0
    except Exception as exc:
        if silent:
            return 1
        messagebox.showerror(APP_NAME, f"Setup could not finish:\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
