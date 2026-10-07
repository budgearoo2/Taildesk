from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import installer


class StartMenuShortcutTests(unittest.TestCase):
    def test_shortcut_points_at_installed_app_without_a_script_host(self):
        install_dir = Path(r"C:\Program Files\Some Dir\TailDesk")
        with tempfile.TemporaryDirectory() as start_menu, \
                patch.object(installer, "START_MENU_DIR", Path(start_menu)), \
                patch.object(installer.subprocess, "run") as run, \
                patch.object(installer.subprocess, "Popen") as popen:
            self.assertTrue(installer.create_start_menu_shortcut(install_dir))
            run.assert_not_called()
            popen.assert_not_called()
            import comtypes.client
            from comtypes.persist import IPersistFile
            from comtypes.shelllink import IShellLinkW, ShellLink
            link = comtypes.client.CreateObject(ShellLink, interface=IShellLinkW)
            link.QueryInterface(IPersistFile).Load(str(Path(start_menu) / "TailDesk.lnk"), 0)
            self.assertEqual(link.GetPath(0), str(install_dir / "TailDesk.exe"))
            self.assertEqual(link.GetWorkingDirectory(), str(install_dir))

    def test_shortcut_failure_does_not_fail_setup(self):
        with patch.object(installer.Path, "mkdir", side_effect=OSError("blocked")):
            self.assertFalse(installer.create_start_menu_shortcut(Path(r"C:\TailDesk")))


class RunningCopyTests(unittest.TestCase):
    def test_running_supervisor_and_respawned_worker_are_closed_then_desktop_restored(self):
        with patch.object(installer, "running_copies", side_effect=[[11, 12], [13], []]), \
                patch.object(installer, "_terminate") as terminate, \
                patch("taildesk.supervisor.restore_desktop") as restore:
            self.assertTrue(installer.stop_running_copies(Path(r"C:\TailDesk")))
        self.assertEqual([call.args[0] for call in terminate.call_args_list], [11, 12, 13])
        restore.assert_called_once()

    def test_nothing_running_changes_nothing(self):
        with patch.object(installer, "running_copies", return_value=[]), \
                patch.object(installer, "_terminate") as terminate, \
                patch("taildesk.supervisor.restore_desktop") as restore:
            self.assertFalse(installer.stop_running_copies(Path(r"C:\TailDesk")))
        terminate.assert_not_called()
        restore.assert_not_called()

    def test_copy_that_cannot_be_closed_reports_an_error(self):
        with patch.object(installer, "running_copies", return_value=[11]), patch.object(installer, "_terminate"):
            with self.assertRaises(TimeoutError):
                installer.stop_running_copies(Path(r"C:\TailDesk"), timeout_seconds=0)

    def test_detection_ignores_other_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(installer.running_copies(Path(folder)), [])


class InstallOverRunningAppTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.payload = root / "payload"
        self.payload.mkdir()
        (self.payload / "TailDesk.exe").write_bytes(b"new")
        self.target = root / "installed"
        self.target.mkdir()
        (self.target / "TailDesk.exe").write_bytes(b"old")
        for name, value in (("payload_dir", lambda: self.payload), ("APPDATA_DIR", root / "appdata"),
                            ("set_startup", lambda *args: None), ("create_start_menu_shortcut", lambda *args: True)):
            patcher = patch.object(installer, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_app_closed_by_setup_is_restarted_even_without_launch_option(self):
        with patch.object(installer, "stop_running_copies", return_value=True), patch.object(installer, "start_app") as start_app:
            installer.install(self.target, startup=False, launch=False)
        self.assertEqual((self.target / "TailDesk.exe").read_bytes(), b"new")
        start_app.assert_called_once_with(self.target, True)

    def test_failed_copy_restarts_the_installed_version(self):
        with patch.object(installer, "stop_running_copies", return_value=True), \
                patch.object(installer.shutil, "copytree", side_effect=PermissionError("in use")), \
                patch.object(installer, "start_app") as start_app:
            with self.assertRaises(PermissionError):
                installer.install(self.target, startup=False, launch=True)
        start_app.assert_called_once_with(self.target, minimized=True)


if __name__ == "__main__":
    unittest.main()
