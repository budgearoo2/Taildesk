from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path
from tkinter import Tk, messagebox, simpledialog

from taildesk import __version__
from taildesk.config import APP_DIR

LOG = logging.getLogger("TailDesk.updater")
REPOSITORY = "budgearoo2/Taildesk"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
TOKEN_FILE = APP_DIR / "github-token.dpapi"
MAX_SETUP_BYTES = 700 * 1024 * 1024


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _as_blob(value: bytes):
    backing = ctypes.create_string_buffer(value)
    blob = _DataBlob(len(value), ctypes.cast(backing, ctypes.POINTER(ctypes.c_byte)))
    return blob, backing


def _dpapi(value: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is required to protect GitHub update credentials.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, keepalive = _as_blob(value)
    target = _DataBlob()
    if decrypt:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), "TailDesk GitHub token", None, None, None, 1, ctypes.byref(target)
        )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def _save_token(token: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_bytes(_dpapi(token.strip().encode("utf-8"), decrypt=False))


def _load_token() -> str | None:
    try:
        return _dpapi(TOKEN_FILE.read_bytes(), decrypt=True).decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        LOG.exception("Could not decrypt the GitHub update token")
        return None


def _request(url: str, token: str, *, accept: str = "application/vnd.github+json"):
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"TailDesk/{__version__}",
        },
    )
    return urllib.request.urlopen(request, timeout=8)


def configure_updates() -> None:
    """Configure or remove the private-repository token from the local tray menu."""
    root = Tk()
    root.withdraw()
    try:
        messagebox.showinfo(
            "TailDesk updates",
            "Create a fine-grained GitHub token restricted to budgearoo2/Taildesk with Contents: Read-only access. "
            "TailDesk stores it encrypted for this Windows account and uses it only to read releases and download setup files.",
            parent=root,
        )
        token = simpledialog.askstring("TailDesk updates", "Paste the repository read-only token:", show="*", parent=root)
        if token is None:
            return
        if not token.strip():
            if TOKEN_FILE.exists() and messagebox.askyesno(
                "TailDesk updates", "Remove the saved token and disable automatic updates?", parent=root
            ):
                TOKEN_FILE.unlink(missing_ok=True)
            return
        try:
            with _request(RELEASES_API, token.strip()) as response:
                release = json.loads(response.read(1024 * 1024))
            if not release.get("tag_name"):
                raise ValueError("GitHub returned no latest release.")
        except urllib.error.HTTPError as exc:
            messagebox.showerror("TailDesk updates", f"GitHub did not accept this token (HTTP {exc.code}).", parent=root)
            return
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            messagebox.showerror("TailDesk updates", f"Could not verify the token with GitHub:\n{exc}", parent=root)
            return
        _save_token(token)
        messagebox.showinfo(
            "TailDesk updates", f"Automatic updates are enabled. Latest release: {release['tag_name']}.", parent=root
        )
    finally:
        root.destroy()


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})", value.strip())
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        old_host = urllib.parse.urlparse(req.full_url).netloc
        new_host = urllib.parse.urlparse(newurl).netloc
        if redirected is not None and old_host != new_host:
            redirected.remove_header("Authorization")
        return redirected


def _download_setup(asset: dict, token: str, destination: Path) -> bool:
    asset_url = str(asset.get("url", ""))
    parsed = urllib.parse.urlparse(asset_url)
    expected_prefix = f"/repos/{REPOSITORY}/releases/assets/"
    expected_hash = str(asset.get("digest") or "")
    if parsed.scheme != "https" or parsed.netloc != "api.github.com" or not parsed.path.startswith(expected_prefix):
        LOG.error("The release setup URL is outside the TailDesk API; refusing to download it")
        return False
    if not expected_hash.startswith("sha256:"):
        LOG.error("The release setup asset has no SHA-256 digest; refusing to install it")
        return False

    opener = urllib.request.build_opener(_SafeRedirect())
    request = urllib.request.Request(
        asset_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/octet-stream",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"TailDesk/{__version__}",
        },
    )
    digest = hashlib.sha256()
    size = 0
    with opener.open(request, timeout=20) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_SETUP_BYTES:
                raise ValueError("The setup file is larger than the allowed update size.")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest().lower() != expected_hash.partition(":")[2].lower():
        destination.unlink(missing_ok=True)
        LOG.error("GitHub setup SHA-256 does not match the release metadata")
        return False
    return True


def check_startup_update() -> bool:
    """Download and launch a digest-verified setup if a newer release is available."""
    if not getattr(sys, "frozen", False):
        return False
    token = _load_token()
    if not token:
        return False
    try:
        with _request(RELEASES_API, token) as response:
            release = json.loads(response.read(1024 * 1024))
        latest = _version_tuple(str(release.get("tag_name", "")))
        current = _version_tuple(__version__)
        if not latest or not current or latest <= current or release.get("prerelease"):
            return False

        version = ".".join(map(str, latest))
        expected_name = f"TailDesk-Setup-{version}.exe"
        asset = next((a for a in release.get("assets", []) if a.get("name") == expected_name), None)
        if not asset:
            LOG.error("The latest release does not contain %s", expected_name)
            return False

        update_dir = Path(tempfile.gettempdir()) / "TailDesk" / "updates"
        update_dir.mkdir(parents=True, exist_ok=True)
        setup = update_dir / expected_name
        if not _download_setup(asset, token, setup):
            return False

        install_dir = Path(sys.executable).resolve().parent
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        subprocess.Popen(
            [str(setup), "--silent", f"--target={install_dir}", f"--wait-pid={os.getpid()}"],
            cwd=install_dir,
            creationflags=flags,
            close_fds=True,
        )
        return True
    except (OSError, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError):
        LOG.exception("Automatic update check failed; starting the installed version")
        return False
