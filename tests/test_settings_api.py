from __future__ import annotations

import unittest
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from taildesk.server import create_app

TEST_MONITORS = [
    {"id": "display-1", "label": "Screen 1 (primary)", "left": 0, "top": 0, "width": 1280, "height": 720, "primary": True},
    {"id": "display-2", "label": "Screen 2", "left": -1920, "top": -200, "width": 1920, "height": 1080, "primary": False},
]


class MemoryStore:
    def __init__(self) -> None:
        self.values = {
            "secret_key": "test-only-secret",
            "fps": 8,
            "jpeg_quality": 65,
            "bind_host": "127.0.0.1",
            "port": 8765,
            "transfer_folder": ".",
            "clipboard_enabled": True,
            "tailscale_https": False,
            "tailscale_https_port": None,
            "startup": False,
        }

    def snapshot(self) -> dict:
        return dict(self.values)

    def public(self) -> dict:
        return {key: value for key, value in self.values.items() if key not in {"secret_key", "tailscale_https_port"}}

    def update(self, updates: dict) -> None:
        self.values.update(updates)

    def verify_password(self, password: str) -> bool:
        return False


class FakeAudioRouter:
    def __init__(self) -> None:
        self.driver_installed_callback = None

    def disconnect(self) -> None:
        pass

    def connect(self) -> None:
        pass

    def status(self) -> dict:
        return {"available": False}


class FakeDisplay:
    def __init__(self) -> None:
        self.restores = 0
        self.resolutions = []
        self.selected = None

    def select_monitor(self, device):
        self.selected = device

    def resize(self, width: int, height: int) -> None:
        self.resolutions.append((width, height))

    def restore(self) -> None:
        self.restores += 1


class FakeCapture:
    def __init__(self, rgb: bytes) -> None:
        self.rgb = rgb
        self.bgra = b"".join(bytes((rgb[i + 2], rgb[i + 1], rgb[i], 255)) for i in range(0, len(rgb), 3))
        self.monitors = [None, {"left": 0, "top": 0, "width": 256, "height": 128}]

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def grab(self, _monitor):
        return SimpleNamespace(size=(256, 128), rgb=self.rgb, bgra=self.bgra)


class SettingsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("taildesk.server.list_monitors", return_value=TEST_MONITORS)
        self.monitors = patcher.start()
        self.addCleanup(patcher.stop)
        self.store = MemoryStore()
        self.display = FakeDisplay()
        with patch("taildesk.server.AudioRouter", FakeAudioRouter):
            self.app = create_app(self.store, self.display)
        self.app.testing = True
        self.addCleanup(self.app.config["TAILDESK_STATE"]._stop.set)

    def remote_client(self):
        client = self.app.test_client()
        client.environ_base["REMOTE_ADDR"] = "192.0.2.10"
        with client.session_transaction() as session:
            session["authenticated"] = True
            session["client_id"] = "remote-test-controller"
        return client

    def test_settings_api_accepts_60_fps_and_rejects_above_limit(self) -> None:
        client = self.app.test_client()
        accepted = client.post("/api/settings", json={"fps": 60})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()["settings"]["fps"], 60)

        rejected = client.post("/api/settings", json={"fps": 61})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.store.values["fps"], 60)

    def test_realtime_requires_authentication_and_controller_ownership(self):
        unauthenticated = self.app.test_client()
        unauthenticated.environ_base["REMOTE_ADDR"] = "192.0.2.10"
        self.assertEqual(unauthenticated.post("/api/realtime", json={}).status_code, 401)
        client = self.remote_client()
        self.assertEqual(client.post("/api/realtime", json={}).status_code, 409)
        self.assertEqual(client.post("/api/realtime/stop").status_code, 409)
        client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        self.assertEqual(client.post("/api/realtime", json={"sdp": [], "type": "offer"}).status_code, 400)
        self.assertIsNone(self.app.config["TAILDESK_STATE"].realtime)

    def test_remote_settings_requests_still_require_login(self) -> None:
        client = self.app.test_client()
        response = client.get("/api/settings", environ_overrides={"REMOTE_ADDR": "192.0.2.10"})
        self.assertEqual(response.status_code, 401)

    def test_cursor_updates_accompany_input_and_unchanged_frames(self):
        client = self.remote_client()
        client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        with patch("taildesk.server.cursor_style", return_value="text"), patch("taildesk.server.pyautogui.moveTo"), patch("taildesk.server.pyautogui.size", return_value=(1280, 720)):
            response = client.post("/api/input", json={"kind": "move", "x": 0.5, "y": 0.5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-TailDesk-Cursor"], "text")
        input_stamp = int(response.headers["X-TailDesk-Cursor-Stamp"])
        with patch("taildesk.server.mss.mss", side_effect=[FakeCapture(bytes(256 * 128 * 3)), FakeCapture(bytes(256 * 128 * 3))]), patch("taildesk.server.cursor_style", return_value="pointer"):
            first = client.get("/api/screen")
            unchanged = client.get("/api/screen")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(unchanged.status_code, 204)
        self.assertEqual(unchanged.headers["X-TailDesk-Cursor"], "pointer")
        self.assertGreaterEqual(int(unchanged.headers["X-TailDesk-Cursor-Stamp"]), input_stamp)

    def test_non_controller_cannot_read_cursor_state(self):
        response = self.remote_client().get("/api/screen")
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("X-TailDesk-Cursor", response.headers)

    def test_upload_download_and_traversal_filename_sanitizing(self) -> None:
        with TemporaryDirectory() as folder:
            self.store.values["transfer_folder"] = folder
            client = self.app.test_client()
            uploaded = client.post(
                "/api/upload",
                data=b"TailDesk transfer test",
                headers={"X-File-Name": "../../remote-test.txt"},
            )
            self.assertEqual(uploaded.status_code, 200)
            self.assertEqual(uploaded.get_json()["name"], "remote-test.txt")
            self.assertEqual((Path(folder) / "remote-test.txt").read_bytes(), b"TailDesk transfer test")

            downloaded = client.get("/api/download/remote-test.txt")
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(downloaded.data, b"TailDesk transfer test")
            downloaded.close()

            rejected = client.get("/api/download/..%2Foutside.txt")
            self.assertEqual(rejected.status_code, 404)

    def test_screen_endpoint_sends_full_jpeg_then_binary_delta_and_restores_display(self) -> None:
        first = bytes(256 * 128 * 3)
        changed = bytearray(first)
        changed[0:24] = bytes([255]) * 24
        client = self.remote_client()

        heartbeat = client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        self.assertEqual(heartbeat.status_code, 200)
        self.assertEqual(self.display.resolutions, [(1280, 720)])

        with patch("taildesk.server.mss.mss", side_effect=[FakeCapture(first), FakeCapture(bytes(changed))]):
            full = client.get("/api/screen?full=1")
            self.assertEqual(full.status_code, 200)
            self.assertEqual(full.content_type, "image/jpeg")
            delta = client.get("/api/screen?q=30")

        self.assertEqual(delta.status_code, 200)
        self.assertEqual(delta.content_type, "application/vnd.taildesk.tiles")
        magic, width, height, tile_count = struct.unpack_from(">4sIIH", delta.data)
        self.assertEqual((magic, width, height, tile_count), (b"TDL1", 256, 128, 1))

        disconnected = client.post("/api/disconnect")
        self.assertEqual(disconnected.status_code, 200)
        self.assertEqual(self.display.restores, 1)


if __name__ == "__main__":
    unittest.main()
