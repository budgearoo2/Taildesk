from __future__ import annotations

import threading
import unittest
from fractions import Fraction
from unittest.mock import MagicMock, patch

import av

import test_settings_api
from test_settings_api import FakeCapture
from taildesk import desktop_access
from taildesk.desktop_access import ELEVATED_WINDOW_MESSAGE, SECURE_DESKTOP_MESSAGE, notice_image
from taildesk.realtime import DesktopTrack


class SecureDesktopServerTests(unittest.TestCase):
    setUp = test_settings_api.SettingsApiTests.setUp
    remote_client = test_settings_api.SettingsApiTests.remote_client

    def test_secure_prompt_sends_explanation_then_a_full_frame(self):
        client = self.remote_client()
        client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        state = self.app.config["TAILDESK_STATE"]
        with patch("taildesk.server.mss.mss", side_effect=[FakeCapture(bytes(256 * 128 * 3))]):
            self.assertEqual(client.get("/api/screen").status_code, 200)
        self.assertIsNotNone(state.previous_frame)
        with patch("taildesk.server.secure_desktop_active", return_value=True), patch("taildesk.server.mss.mss") as capture:
            response = client.get("/api/screen")
        capture.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertIsNone(state.previous_frame)
        with patch("taildesk.server.mss.mss", side_effect=[FakeCapture(bytes(256 * 128 * 3))]):
            resumed = client.get("/api/screen")
        self.assertEqual(resumed.headers["Content-Type"], "image/jpeg")

    def test_heartbeat_reports_why_the_host_cannot_be_controlled(self):
        client = self.remote_client()
        with patch("taildesk.server.desktop_notice", return_value=ELEVATED_WINDOW_MESSAGE):
            state = client.post("/api/heartbeat", json={"width": 1280, "height": 720}).get_json()
        self.assertEqual(state["notice"], ELEVATED_WINDOW_MESSAGE)
        self.assertIsNone(client.post("/api/heartbeat", json={"width": 1280, "height": 720}).get_json()["notice"])


class RealtimePlaceholderTests(unittest.TestCase):
    def track(self):
        state = MagicMock()
        state.lock = threading.RLock()
        state.can_control.return_value = True
        state.current_monitor.return_value = {"id": "display-1", "left": 0, "top": 0, "width": 321, "height": 181}
        track = DesktopTrack(state, "owner", 30)
        track.com_ready = True
        self.addCleanup(track.executor.shutdown)
        return track

    def test_secure_desktop_keeps_stream_alive_without_capturing(self):
        track = self.track()
        with patch("taildesk.realtime.secure_desktop_active", return_value=True), \
                patch.object(track, "capture_frame", side_effect=AssertionError("captured secure desktop")):
            frame = track.grab()
        self.assertEqual((frame.width, frame.height, frame.format.name), (320, 180, "yuv420p"))

    def test_capture_failure_sends_placeholder_and_recovers(self):
        track = self.track()
        real = av.VideoFrame(320, 180, "yuv420p")
        real.pts, real.time_base = 1, Fraction(1, 90000)
        with patch("taildesk.realtime.secure_desktop_active", return_value=False), \
                patch.object(track, "capture_frame", side_effect=[RuntimeError("DXGI access lost"), real]):
            placeholder = track.grab()
            self.assertTrue(track.capture_failing)
            self.assertEqual((placeholder.width, placeholder.height), (320, 180))
            self.assertIs(track.grab(), real)
        self.assertFalse(track.capture_failing)


class DesktopAccessTests(unittest.TestCase):
    def test_notice_image_matches_host_size(self):
        image = notice_image(1280, 720, SECURE_DESKTOP_MESSAGE)
        self.assertEqual(image.size, (1280, 720))
        self.assertEqual(image.mode, "RGB")

    def test_notice_prefers_secure_desktop_over_elevated_window(self):
        with patch.object(desktop_access, "secure_desktop_active", return_value=True), \
                patch.object(desktop_access, "foreground_window_elevated", return_value=True):
            self.assertEqual(desktop_access.desktop_notice(), SECURE_DESKTOP_MESSAGE)
        with patch.object(desktop_access, "secure_desktop_active", return_value=False), \
                patch.object(desktop_access, "foreground_window_elevated", return_value=False):
            self.assertIsNone(desktop_access.desktop_notice())

    def test_live_windows_checks_return_booleans(self):
        self.assertIsInstance(desktop_access.secure_desktop_active(), bool)
        self.assertIsInstance(desktop_access.foreground_window_elevated(), bool)


if __name__ == "__main__":
    unittest.main()
