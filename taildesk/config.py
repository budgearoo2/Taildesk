from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "TailDesk"
CONFIG_FILE = APP_DIR / "settings.json"
DEFAULT_TRANSFER_DIR = Path.home() / "Downloads" / "TailDesk"
DEFAULTS: dict[str, Any] = {
    "bind_host": "auto",
    "port": 8765,
    "fps": 8,
    "jpeg_quality": 65,
    "clipboard_enabled": True,
    "transfer_folder": str(DEFAULT_TRANSFER_DIR),
    "startup": True,
    "tailscale_https": True,
    "tailscale_https_port": None,
    "secret_key": "",
    "password_salt": "",
    "password_hash": "",
}
PBKDF2_ITERATIONS = 310_000


class ConfigStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self._data = dict(DEFAULTS)
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self._data.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
        if not self._data["secret_key"]:
            self._data["secret_key"] = secrets.token_urlsafe(48)
            self._save()

    def _save(self) -> None:
        with self._lock:
            temp = CONFIG_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            temp.replace(CONFIG_FILE)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def public(self) -> dict[str, Any]:
        data = self.snapshot()
        for secret in ("secret_key", "password_salt", "password_hash", "tailscale_https_port"):
            data.pop(secret, None)
        return data

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(values)
            self._save()

    def has_password(self) -> bool:
        return bool(self._data.get("password_salt") and self._data.get("password_hash"))

    def set_password(self, password: str) -> None:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
        with self._lock:
            self._data["password_salt"] = salt.hex()
            self._data["password_hash"] = digest.hex()
            self._save()

    def verify_password(self, password: str) -> bool:
        try:
            salt = bytes.fromhex(self._data["password_salt"])
            expected = bytes.fromhex(self._data["password_hash"])
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
            return hmac.compare_digest(actual, expected)
        except (ValueError, KeyError, TypeError):
            return False
