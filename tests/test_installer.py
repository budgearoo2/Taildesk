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


if __name__ == "__main__":
    unittest.main()
