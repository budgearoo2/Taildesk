from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from test_settings_api import MemoryStore, FakeAudioRouter, FakeDisplay, TEST_MONITORS
from taildesk.server import create_app


class HostAccessTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("taildesk.server.list_monitors", return_value=TEST_MONITORS)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (("secure_desktop_active", False), ("desktop_notice", None)):
            desktop = patch(f"taildesk.server.{name}", return_value=value)
            desktop.start()
            self.addCleanup(desktop.stop)
        self.store = MemoryStore()
        self.display = FakeDisplay()
        with patch("taildesk.server.AudioRouter", FakeAudioRouter):
            self.app = create_app(self.store, self.display)
        self.app.testing = True
        self.app.config["TAILDESK_LOCAL_ADDRESSES"] = {"127.0.0.1", "192.0.2.20"}
        self.addCleanup(self.app.config["TAILDESK_STATE"]._stop.set)

    def test_host_page_does_not_start_controller_or_resize_display(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/").status_code, 200)
        state = client.get("/api/state").get_json()
        self.assertTrue(state["host_browser"])
        self.assertTrue(state["local_access"])
        for path, method in (("heartbeat", "post"), ("input", "post"), ("screen", "get"), ("audio", "get"), ("clipboard", "get"), ("realtime", "post"), ("realtime/stop", "post")):
            self.assertEqual(getattr(client, method)(f"/api/{path}").status_code, 403)
        self.assertFalse(self.app.config["TAILDESK_STATE"].connected)
        self.assertEqual(self.display.resolutions, [])
        self.assertEqual(client.get("/api/settings").status_code, 200)

    def test_own_interface_address_blocks_control_but_never_bypasses_password(self):
        client = self.app.test_client()
        client.environ_base["REMOTE_ADDR"] = "192.0.2.20"
        self.assertEqual(client.get("/api/settings").status_code, 401)
        self.assertEqual(client.post("/api/heartbeat").status_code, 403)
        with client.session_transaction() as session:
            session["authenticated"] = True
        state = client.get("/api/state").get_json()
        self.assertTrue(state["host_browser"])
        self.assertFalse(state["local_access"])

    def test_remote_login_page_can_load_every_script_before_authentication(self):
        client = self.app.test_client()
        client.environ_base["REMOTE_ADDR"] = "192.0.2.10"
        html = client.get("/").get_data(as_text=True)
        assets = re.findall(r'(?:src|href)="(/static/[^\"]+)"', html)
        self.assertGreaterEqual(len(assets), 6)
        for asset in assets:
            response = client.get(asset)
            self.assertEqual(response.status_code, 200, asset)
            response.close()
        self.assertEqual(client.get("/api/state").status_code, 401)
        self.assertEqual(client.get("/static/index.html").status_code, 401)

    def test_proxy_and_non_loopback_host_requests_require_a_password(self):
        for headers in (
            {"Host": "remote.example"},
            {"Tailscale-User-Login": "test-user"},
            {"Tailscale-Headers-Info": "present"},
            {"X-Forwarded-For": "192.0.2.10"},
            {"Forwarded": "for=192.0.2.10"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            with self.subTest(headers=headers):
                client = self.app.test_client()
                self.assertEqual(client.get("/api/settings", headers=headers).status_code, 401)

    def test_proxy_can_log_in_and_connect_as_remote_controller(self):
        client = self.app.test_client()
        headers = {"Host": "remote.example", "Tailscale-User-Login": "test-user"}
        with patch.object(self.store, "verify_password", return_value=True):
            self.assertEqual(client.post("/api/login", headers=headers, json={"password": "test password"}).status_code, 200)
        self.assertFalse(client.get("/api/state", headers=headers).get_json()["host_browser"])
        self.assertEqual(client.post("/api/heartbeat", headers=headers, json={"width": 1280, "height": 720}).status_code, 200)
        self.assertEqual(client.post("/api/disconnect", headers=headers).status_code, 200)
        self.assertEqual(self.display.restores, 1)

    def test_invalid_interface_is_rejected_without_changing_settings(self):
        client = self.app.test_client()
        with patch("taildesk.network.socket.socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.bind.side_effect = OSError("not assigned")
            response = client.post("/api/settings", json={"bind_host": "192.0.2.99"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not available", response.get_json()["error"])
        self.assertEqual(self.store.values["bind_host"], "127.0.0.1")

    def test_reported_url_uses_running_listener_until_restart(self):
        self.app.config.update(TAILDESK_BIND="192.0.2.20", TAILDESK_PORT=8765, TAILDESK_START_BIND="auto")
        self.store.values.update(bind_host="auto", port=9000)
        state = self.app.test_client().get("/api/state").get_json()
        self.assertEqual(state["remote_url"], "http://192.0.2.20:8765/")
        self.assertTrue(state["restart_required"])

    def test_health_check_reports_bind_only_to_the_local_supervisor(self):
        self.app.config["TAILDESK_BIND"] = "192.0.2.20"
        local = self.app.test_client().get("/api/health")
        self.assertEqual(local.get_json(), {"ok": True, "bind": "192.0.2.20"})
        for headers, address in (({"Host": "remote.example"}, "192.0.2.10"), ({}, "192.0.2.20")):
            client = self.app.test_client()
            client.environ_base["REMOTE_ADDR"] = address
            response = client.get("/api/health", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {"ok": True, "bind": None})
            self.assertEqual(client.get("/api/settings", headers=headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()
