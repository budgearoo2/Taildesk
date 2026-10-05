from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

HTTPS_PORT = 8443


def tailscale_executable() -> str | None:
    candidates = [shutil.which("tailscale")]
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates.extend(
        str(path)
        for path in (
            program_files / "Tailscale IPN" / "tailscale.exe",
            program_files / "Tailscale" / "tailscale.exe",
            Path(__file__).resolve().parents[1] / "tailscale.exe",
        )
    )
    return next((path for path in candidates if path and Path(path).exists()), None)


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    executable = tailscale_executable()
    if not executable:
        return None
    try:
        return subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _status_text() -> str:
    result = _run(["serve", "status"])
    return result.stdout if result else ""


def dns_name() -> str | None:
    result = _run(["status", "--json"])
    if not result or result.returncode != 0:
        return None
    try:
        name = str(json.loads(result.stdout).get("Self", {}).get("DNSName", "")).rstrip(".")
        return name or None
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def configure_https(enabled: bool, port: int, managed_port: int | None = None) -> tuple[str | None, str | None, int | None]:
    """Set up the optional private HTTPS endpoint without replacing another route."""
    current = _status_text()
    target = f"127.0.0.1:{port}"
    previous_target = f"127.0.0.1:{managed_port}" if managed_port else None
    port_marker = f":{HTTPS_PORT}"
    if enabled:
        if port_marker in current:
            if target in current:
                name = dns_name()
                return (f"https://{name}:{HTTPS_PORT}/" if name else None), None, port
            if previous_target and previous_target in current:
                removed = _run(["serve", f"--https={HTTPS_PORT}", "--bg", previous_target, "off"])
                if removed is None or removed.returncode != 0:
                    return None, "Could not replace the previous TailDesk HTTPS route.", managed_port
            else:
                return None, f"Tailscale Serve already uses HTTPS port {HTTPS_PORT}; its existing route was left untouched.", managed_port
        result = _run(["serve", f"--https={HTTPS_PORT}", "--bg", target])
        if result is None:
            return None, "The Tailscale CLI could not be found or started.", managed_port
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return None, detail or "Tailscale Serve could not be enabled.", managed_port
        name = dns_name()
        return (f"https://{name}:{HTTPS_PORT}/" if name else None), None, port

    owned_target = previous_target or target
    if port_marker in current and owned_target and owned_target in current:
        result = _run(["serve", f"--https={HTTPS_PORT}", "--bg", owned_target, "off"])
        if result is None or result.returncode != 0:
            detail = ((result.stderr or result.stdout).strip() if result else "")
            return None, detail or "Tailscale Serve could not be disabled.", managed_port
    return None, None, None
