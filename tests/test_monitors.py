import unittest
from unittest.mock import patch
from PIL import Image

import test_settings_api
from test_settings_api import TEST_MONITORS, FakeCapture


class MonitorApiTests(unittest.TestCase):
    setUp = test_settings_api.SettingsApiTests.setUp
    remote_client = test_settings_api.SettingsApiTests.remote_client
    def test_all_detected_screens_are_listed(self):
        four = TEST_MONITORS + [dict(TEST_MONITORS[1], id=f"display-{i}", label=f"Screen {i}") for i in (3, 4)]
        self.monitors.return_value = four
        response = self.app.test_client().get("/api/state")
        self.assertEqual(len(response.get_json()["monitors"]), 4)

    def test_switch_updates_capture_bounds_and_negative_pointer_coordinates(self):
        client = self.remote_client()
        client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        state = self.app.config["TAILDESK_STATE"]
        state.previous_frame = Image.new("RGB", (256, 128))
        response = client.post("/api/monitor", json={"monitor": "display-2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["selected_monitor"], "display-2")
        self.assertIsNone(state.previous_frame)
        self.assertEqual(self.display.selected, "display-2")
        with patch("taildesk.server.pyautogui.moveTo") as move:
            response = client.post("/api/input", json={"kind": "move", "monitor": "display-2", "x": 0.5, "y": 0.5})
        self.assertEqual(response.status_code, 200)
        move.assert_called_once_with(-960, 340)
        capture = FakeCapture(bytes(256 * 128 * 3))
        with patch("taildesk.server.mss.mss", return_value=capture), patch.object(capture, "grab", wraps=capture.grab) as grab:
            frame = client.get("/api/screen?monitor=display-2")
        self.assertEqual(frame.status_code, 200)
        self.assertEqual(frame.headers["X-TailDesk-Monitor"], "display-2")
        grab.assert_called_once_with({"left": -1920, "top": -200, "width": 1920, "height": 1080})

    def test_stale_input_and_other_controller_cannot_change_selected_screen(self):
        client = self.remote_client()
        client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        other = self.remote_client()
        with other.session_transaction() as session:
            session["client_id"] = "another-browser"
        self.assertEqual(other.post("/api/monitor", json={"monitor": "display-2"}).status_code, 409)
        self.assertEqual(client.post("/api/monitor", json={"monitor": "missing"}).status_code, 400)
        client.post("/api/monitor", json={"monitor": "display-2"})
        with patch("taildesk.server.pyautogui.moveTo") as move:
            response = client.post("/api/input", json={"kind": "move", "monitor": "display-1", "x": 0, "y": 0})
        self.assertEqual(response.status_code, 409)
        move.assert_not_called()

    def test_switch_releases_held_keys_and_buttons_and_hotplug_falls_back(self):
        client = self.remote_client()
        client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        state = self.app.config["TAILDESK_STATE"]
        state.held_keys.add("a")
        state.held_buttons.add("left")
        with patch("taildesk.server.pyautogui.keyUp") as key, patch("taildesk.server.pyautogui.mouseUp") as button:
            client.post("/api/monitor", json={"monitor": "display-2"})
        key.assert_called_once_with("a")
        button.assert_called_once_with(button="left")
        self.monitors.return_value = TEST_MONITORS[:1]
        response = client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        self.assertEqual(response.get_json()["selected_monitor"], "display-1")
        self.assertEqual(self.display.selected, "display-1")

    def test_disconnect_restores_display_even_when_audio_restoration_raises(self):
        client = self.remote_client()
        client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        with patch.object(self.app.config["TAILDESK_STATE"].audio, "disconnect", side_effect=RuntimeError("audio unavailable")), self.assertLogs("TailDesk.server", level="ERROR"):
            response = client.post("/api/disconnect")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.display.restores, 1)

    def test_switch_during_capture_discards_old_monitor_frame(self):
        client = self.remote_client()
        client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        state = self.app.config["TAILDESK_STATE"]
        capture = FakeCapture(bytes(256 * 128 * 3))
        def grab(bounds):
            state.select_monitor("remote-test-controller", "display-2")
            return capture.grab(bounds)
        with patch("taildesk.server.mss.mss") as factory:
            factory.return_value.__enter__.return_value.grab.side_effect = grab
            response = client.get("/api/screen?monitor=display-1")
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(state.previous_frame)


if __name__ == "__main__":
    unittest.main()
