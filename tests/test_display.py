from __future__ import annotations

import unittest

from taildesk.display import DisplayController


class FakeDisplayApi:
    def __init__(self, width: int, height: int) -> None:
        self.current = (width, height)
        self.change_calls: list[tuple[int, int]] = []

    def EnumDisplaySettingsW(self, _device, _mode, destination) -> int:
        mode = destination._obj
        mode.width, mode.height = self.current
        return 1

    def ChangeDisplaySettingsW(self, requested, _flags) -> int:
        mode = requested._obj
        self.current = (mode.width, mode.height)
        self.change_calls.append(self.current)
        return 0


class DisplayControllerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
