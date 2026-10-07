from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import installer
import test_settings_api
from taildesk import updater
from taildesk.supervisor import EXIT_UPDATE, SUPERVISOR_PID_ENV, Supervisor

RELEASE = {
    "tag_name": "v99.0.0",
    "prerelease": False,
    "assets": [{"name": "TailDesk-Setup-99.0.0.exe", "url": "https://api.github.com/repos/budgearoo2/Taildesk/releases/assets/1", "digest": "sha256:00"}],
}


class UpdateApiTests(unittest.TestCase):
    setUp = test_settings_api.SettingsApiTests.setUp
    remote_client = test_settings_api.SettingsApiTests.remote_client

    def test_update_requires_login_and_the_running_app(self):
        unauthenticated = self.app.test_client()
        unauthenticated.environ_base["REMOTE_ADDR"] = "192.0.2.10"
        self.assertEqual(unauthenticated.post("/api/update").status_code, 401)
        self.assertEqual(self.remote_client().post("/api/update").status_code, 503)

    def test_latest_version_does_not_restart(self):
        exit_for_update = MagicMock()
        self.app.config["TAILDESK_EXIT_FOR_UPDATE"] = exit_for_update
        with patch("taildesk.server.updater.install_latest", return_value=None):
            result = self.remote_client().post("/api/update").get_json()
        self.assertFalse(result["updating"])
        exit_for_update.assert_not_called()

    def test_started_update_closes_host_for_setup_and_blocks_repeats(self):
        exit_for_update = MagicMock()
        self.app.config["TAILDESK_EXIT_FOR_UPDATE"] = exit_for_update
        client = self.remote_client()
        with patch("taildesk.server.updater.install_latest", return_value="99.0.0") as install, \
                patch("taildesk.server.threading.Timer") as timer, \
                patch.dict("os.environ", {SUPERVISOR_PID_ENV: "4321"}):
            result = client.post("/api/update").get_json()
            self.assertEqual(client.post("/api/update").status_code, 409)
        self.assertEqual((result["updating"], result["version"]), (True, "99.0.0"))
        install.assert_called_once_with(4321)
        timer.assert_called_once_with(1.0, exit_for_update)
        timer.return_value.start.assert_called_once()

    def test_failed_update_keeps_host_running_and_can_retry(self):
        exit_for_update = MagicMock()
        self.app.config["TAILDESK_EXIT_FOR_UPDATE"] = exit_for_update
        client = self.remote_client()
        with patch("taildesk.server.updater.install_latest", side_effect=updater.UpdateError("blocked by Windows Security")):
            for _ in range(2):
                response = client.post("/api/update")
                self.assertEqual(response.status_code, 503)
                self.assertIn("blocked", response.get_json()["error"])
        exit_for_update.assert_not_called()


class InstallLatestTests(unittest.TestCase):
    def test_source_runs_and_missing_token_refuse_to_install(self):
        with self.assertRaises(updater.UpdateError):
            updater.install_latest(1)
        with patch.object(updater.sys, "frozen", True, create=True), patch.object(updater, "_load_token", return_value=None):
            with self.assertRaisesRegex(updater.UpdateError, "tray"):
                updater.install_latest(1)

    def test_newer_release_is_verified_then_setup_waits_for_given_process(self):
        with patch.object(updater.sys, "frozen", True, create=True), \
                patch.object(updater, "_load_token", return_value="token"), \
                patch.object(updater, "_latest_release", return_value=RELEASE), \
                patch.object(updater, "_download_setup", return_value=True) as download, \
                patch.object(updater.subprocess, "Popen") as popen:
            self.assertEqual(updater.install_latest(4321), "99.0.0")
        download.assert_called_once()
        self.assertIn("--wait-pid=4321", popen.call_args.args[0])

    def test_digest_mismatch_or_blocked_setup_is_reported(self):
        with patch.object(updater.sys, "frozen", True, create=True), \
                patch.object(updater, "_load_token", return_value="token"), \
                patch.object(updater, "_latest_release", return_value=RELEASE):
            with patch.object(updater, "_download_setup", return_value=False), patch.object(updater.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(updater.UpdateError, "SHA-256"):
                    updater.install_latest(1)
                popen.assert_not_called()
            with patch.object(updater, "_download_setup", return_value=True), \
                    patch.object(updater.subprocess, "Popen", side_effect=OSError("Operation did not complete successfully because the file contains a virus")):
                with self.assertRaisesRegex(updater.UpdateError, "could not be started"):
                    updater.install_latest(1)

    def test_current_release_is_not_reinstalled(self):
        current = dict(RELEASE, tag_name=f"v{updater.__version__}")
        with patch.object(updater.sys, "frozen", True, create=True), \
                patch.object(updater, "_load_token", return_value="token"), \
                patch.object(updater, "_latest_release", return_value=current):
            self.assertIsNone(updater.install_latest(1))


class UpdateRestartTests(unittest.TestCase):
    def test_supervisor_exits_for_setup_and_shares_its_pid(self):
        process = MagicMock()
        process.wait.return_value = EXIT_UPDATE
        popen = MagicMock(return_value=process)
        result = Supervisor(["TailDesk.exe"], 8765, popen=popen, probe=lambda url: None, restore=MagicMock(), sleep=MagicMock()).run()
        self.assertEqual(result, EXIT_UPDATE)
        self.assertEqual(popen.call_count, 1)
        self.assertTrue(popen.call_args.kwargs["env"][SUPERVISOR_PID_ENV].isdigit())

    def test_failed_silent_install_restarts_the_installed_version(self):
        argv = ["TailDesk-Setup.exe", "--silent", r"--target=C:\TailDesk"]
        with patch.object(installer.sys, "argv", argv), \
                patch.object(installer, "install", side_effect=OSError("access denied")), \
                patch.object(installer.Path, "is_file", return_value=True), \
                patch.object(installer, "start_app") as start_app:
            self.assertEqual(installer.main(), 1)
        start_app.assert_called_once_with(Path(r"C:\TailDesk"), minimized=True)


if __name__ == "__main__":
    unittest.main()
