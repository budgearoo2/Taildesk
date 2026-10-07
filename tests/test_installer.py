from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import installer


class StartMenuShortcutTests(unittest.TestCase):
    def test_shortcut_points_at_installed_app_without_interpolating_paths(self):
        install_dir = Path(r"C:\Programs\Task'; Remove-Item x; '\TailDesk")
        start_menu = Path(r"C:\StartMenu\Programs")
        with patch.object(installer, "START_MENU_DIR", start_menu), \
                patch.object(installer.Path, "mkdir"), \
                patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertTrue(installer.create_start_menu_shortcut(install_dir))
        command = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertEqual(command[-1], installer.SHORTCUT_SCRIPT)
        self.assertNotIn(str(install_dir), " ".join(command))
        self.assertEqual(env["TAILDESK_SHORTCUT"], str(start_menu / "TailDesk.lnk"))
        self.assertEqual(env["TAILDESK_TARGET"], str(install_dir / "TailDesk.exe"))
        self.assertEqual(env["TAILDESK_DIR"], str(install_dir))

    def test_shortcut_failure_does_not_fail_setup(self):
        with patch.object(installer.Path, "mkdir"), \
                patch.object(installer.subprocess, "run", side_effect=OSError("blocked")):
            self.assertFalse(installer.create_start_menu_shortcut(Path(r"C:\TailDesk")))


if __name__ == "__main__":
    unittest.main()
