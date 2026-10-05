from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from taildesk.network import HostListeners, validate_bind_address


class NetworkTests(unittest.TestCase):
    def test_rejects_wildcards_multicast_and_hostnames(self):
        for host in ("0.0.0.0", "224.0.0.1", "example.com", "::", "127.0.0.2"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_bind_address(host)

    def test_unavailable_remote_address_keeps_local_recovery_listener(self):
        app = SimpleNamespace(config={})
        listeners = HostListeners(app, "192.0.2.99", 8765, Mock())
        with patch.object(listeners, "_listen") as listen, patch("taildesk.network.validate_bind_address", side_effect=ValueError("not assigned")):
            listeners.start()
        listen.assert_called_once_with("127.0.0.1", 4)
        self.assertIsNone(app.config["TAILDESK_BIND"])
        self.assertIn("not assigned", app.config["TAILDESK_LISTENER_ERROR"])

    def test_auto_recovers_after_tailscale_becomes_available(self):
        app = SimpleNamespace(config={})
        discover = Mock(side_effect=[None, "192.0.2.20"])
        listeners = HostListeners(app, "auto", 8765, discover)
        with patch.object(listeners, "_listen") as listen, patch("taildesk.network.validate_bind_address"):
            self.assertFalse(listeners._start_remote())
            self.assertIn("Waiting for Tailscale", app.config["TAILDESK_LISTENER_ERROR"])
            self.assertTrue(listeners._start_remote())
        listen.assert_called_once_with("192.0.2.20", 8)
        self.assertEqual(app.config["TAILDESK_BIND"], "192.0.2.20")
        self.assertIsNone(app.config["TAILDESK_LISTENER_ERROR"])

    def test_bind_failure_releases_workers_and_sockets(self):
        listeners = HostListeners(SimpleNamespace(config={}), "auto", 8765, Mock())
        with patch("taildesk.network.create_server", side_effect=OSError("port in use")), patch("taildesk.network.ThreadedTaskDispatcher") as dispatcher, patch("taildesk.network.wasyncore.close_all") as close_all:
            with self.assertRaises(OSError):
                listeners._listen("127.0.0.1", 4)
            dispatcher.return_value.shutdown.assert_called_once()
            close_all.assert_called_once()
            dispatcher.return_value.set_thread_count.assert_not_called()


if __name__ == "__main__":
    unittest.main()
