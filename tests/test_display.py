from __future__ import annotations

import unittest
import ctypes
import os

from taildesk.display import DisplayController, DEVMODEW


class FakeDisplayApi:
    def __init__(self, width: int, height: int, result: int = 0) -> None:
        self.current = (width, height)
        self.result = result
        self.change_calls: list[tuple[int, int]] = []

    def EnumDisplaySettingsW(self, _device, _mode, destination) -> int:
        mode = destination._obj
        mode.width, mode.height = self.current
        return 1

    def ChangeDisplaySettingsW(self, requested, _flags) -> int:
        mode = requested._obj
        requested_size = (mode.width, mode.height)
        self.change_calls.append(requested_size)
        if self.result == 0:
            self.current = requested_size
        return self.result


class DisplayControllerTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DEVMODEW ABI")
    def test_windows_display_mode_layout_matches_native_structure(self):
        self.assertEqual(ctypes.sizeof(DEVMODEW), 220)
        self.assertEqual(DEVMODEW.width.offset, 172)
        self.assertEqual(DEVMODEW.height.offset, 176)

    def test_switching_restores_previous_device_and_disconnect_restores_selected_device(self):
        class MultiApi:
            def __init__(self):
                self.modes = {"display-1": (1920, 1080), "display-2": (2560, 1440)}
                self.calls = []

            def EnumDisplaySettingsW(self, device, _mode, target):
                target._obj.width, target._obj.height = self.modes[device]
                return 1

            def ChangeDisplaySettingsExW(self, device, target, _hwnd, _flags, _data):
                size = (target._obj.width, target._obj.height)
                self.calls.append((device, size))
                self.modes[device] = size
                return 0

        api = MultiApi()
        display = DisplayController()
        display._api = lambda: api
        display.select_monitor("display-1")
        display.resize(1280, 720)
        display.select_monitor("display-2")
        self.assertEqual(api.modes["display-1"], (1920, 1080))
        display.resize(1280, 720)
        display.restore()
        self.assertEqual(api.modes["display-2"], (2560, 1440))
        self.assertEqual(len(api.calls), 4)

    def test_repeated_heartbeat_size_does_not_reapply_display_mode(self) -> None:
        api = FakeDisplayApi(1920, 1080)
        display = DisplayController()
        display._api = lambda: api

        self.assertTrue(display.resize(1280, 720))
        self.assertTrue(display.resize(1280, 720))
        self.assertTrue(display.resize(1280, 720))
        self.assertEqual(api.change_calls, [(1280, 720)])

        display.restore()
        self.assertEqual(api.current, (1920, 1080))
        self.assertEqual(api.change_calls, [(1280, 720), (1920, 1080)])

    def test_matching_native_size_is_not_counted_as_a_display_change(self) -> None:
        api = FakeDisplayApi(1920, 1080)
        display = DisplayController()
        display._api = lambda: api

        self.assertTrue(display.resize(1920, 1080))
        display.restore()

        self.assertEqual(api.change_calls, [])

    def test_rejected_browser_size_is_not_retried_on_every_heartbeat(self) -> None:
        api = FakeDisplayApi(1920, 1080, result=-2)
        display = DisplayController()
        display._api = lambda: api

        self.assertFalse(display.resize(1536, 864))
        self.assertFalse(display.resize(1536, 864))
        self.assertFalse(display.resize(1536, 864))
        self.assertEqual(api.change_calls, [(1536, 864)])

        # A real host-mode change makes a retry useful again.
        api.current = (1600, 900)
        self.assertFalse(display.resize(1536, 864))
        self.assertEqual(api.change_calls, [(1536, 864), (1536, 864)])


if __name__ == "__main__":
    unittest.main()
