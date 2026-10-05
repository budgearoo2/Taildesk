from __future__ import annotations

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
from pathlib import Path

from taildesk import __version__

LOG = logging.getLogger("TailDesk.updater")
REPOSITORY = "budgearoo2/Taildesk"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_SETUP_BYTES = 700 * 1024 * 1024


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})", value.strip())
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def _read_latest_release() -> dict:
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"TailDesk/{__version__}",
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read(1024 * 1024))


def _download_setup(asset: dict, version: str, destination: Path) -> bool:
    url = str(asset.get("browser_download_url", ""))
    parsed = urllib.parse.urlparse(url)
    expected_path_prefix = f"/{REPOSITORY}/releases/download/"
    expected_hash = str(asset.get("digest") or "")
    if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith(expected_path_prefix):
        LOG.error("The release setup URL is outside the TailDesk GitHub repository; refusing to download it")
        return False
    if not expected_hash.startswith("sha256:"):
        LOG.error("The release setup asset has no SHA-256 digest; refusing to install it")
        return False

    request = urllib.request.Request(url, headers={"User-Agent": f"TailDesk/{__version__}"})
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(request, timeout=20) as response, destination.open("wb") as output:
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
        LOG.error("The setup file SHA-256 does not match the public release metadata")
        return False
    return True


def check_startup_update() -> bool:
    """Download and launch a verified setup when a newer stable release exists."""
    if not getattr(sys, "frozen", False):
        return False
    try:
        release = _read_latest_release()
        latest = _version_tuple(str(release.get("tag_name", "")))
        current = _version_tuple(__version__)
        if not latest or not current or latest <= current or release.get("prerelease"):
            return False
        version = ".".join(map(str, latest))
        asset_name = f"TailDesk-Setup-{version}.exe"
        asset = next((item for item in release.get("assets", []) if item.get("name") == asset_name), None)
        if not asset:
            LOG.error("The latest release does not contain %s", asset_name)
            return False

        destination_dir = Path(tempfile.gettempdir()) / "TailDesk" / "updates"
        destination_dir.mkdir(parents=True, exist_ok=True)
        setup = destination_dir / asset_name
        if not _download_setup(asset, version, setup):
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
