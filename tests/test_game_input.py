import ctypes
import unittest
from unittest.mock import MagicMock, call, patch

import test_settings_api
from taildesk import game_input, supervisor

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008


class ScanCodeTests(unittest.TestCase):
    def test_physical_keys_are_sent_as_scan_codes_without_a_virtual_key(self):
        press = game_input.key_event("KeyF").union.ki
        self.assertEqual((press.wVk, press.wScan, press.dwFlags), (0, 0x21, KEYEVENTF_SCANCODE))
        release = game_input.key_event("KeyF", up=True).union.ki
        self.assertEqual(release.dwFlags, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)

    def test_extended_keys_set_the_extended_flag_but_keypad_digits_do_not(self):
        arrow = game_input.key_event("ArrowUp").union.ki
        self.assertEqual((arrow.wScan, arrow.dwFlags), (0x48, KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY))
        self.assertEqual(game_input.key_event("Numpad8").union.ki.dwFlags, KEYEVENTF_SCANCODE)
        self.assertEqual(game_input.key_event("ControlRight", up=True).union.ki.dwFlags,
                         KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY)

    def test_input_structure_matches_the_windows_layout(self):
        self.assertEqual(ctypes.sizeof(game_input._INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_send_key_passes_one_event_and_reports_acceptance(self):
        sent = []

        def send_input(count, event, size):
            ki = event._obj.union.ki
            sent.append((count, ki.wScan, ki.dwFlags, size))
            return 1

        with patch.object(game_input, "_send_input", return_value=send_input):
            self.assertTrue(game_input.send_key("KeyD"))
        self.assertEqual(sent, [(1, 0x20, KEYEVENTF_SCANCODE, ctypes.sizeof(game_input._INPUT))])

    def test_supervisor_release_includes_scan_codes(self):
        held = {0x46: 0x21, 0x26: 0xE048}  # F and the Up arrow, as MAPVK_VK_TO_VSC_EX reports them
        user32 = MagicMock()
        user32.GetAsyncKeyState.side_effect = lambda key: -32768 if key in held else 0
        user32.MapVirtualKeyW.side_effect = lambda key, _mode: held.get(key, 0)
        user32.ChangeDisplaySettingsExW.return_value = 0
        with patch.object(supervisor.ctypes, "windll", MagicMock(user32=user32)):
            supervisor.restore_desktop()
        self.assertEqual(sorted(entry.args for entry in user32.keybd_event.call_args_list), [
            (0x26, 0x48, KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY, 0),
            (0x46, 0x21, KEYEVENTF_KEYUP, 0),
        ])


class GameModeApiTests(unittest.TestCase):
    setUp = test_settings_api.SettingsApiTests.setUp
    remote_client = test_settings_api.SettingsApiTests.remote_client

    def connected_client(self):
        client = self.remote_client()
        client.post("/api/heartbeat", json={"width": 1280, "height": 720})
        return client

    def send(self, client, **body):
        return client.post("/api/input", json={"monitor": "display-1", **body})

    def test_game_keys_are_held_repeated_and_released_once(self):
        client = self.connected_client()
        with patch("taildesk.server.game_input.send_key", return_value=True) as send, \
                patch("taildesk.server.pyautogui.keyDown") as typed:
            for body in (
                {"kind": "game_down", "code": "KeyF"},
                {"kind": "game_down", "code": "KeyF"},
                {"kind": "game_down", "code": "KeyF", "repeat": True},
                {"kind": "game_up", "code": "KeyF"},
                {"kind": "game_up", "code": "KeyF"},
            ):
                self.assertEqual(self.send(client, **body).status_code, 200)
        self.assertEqual(send.call_args_list, [call("KeyF"), call("KeyF"), call("KeyF", up=True)])
        typed.assert_not_called()

    def test_unknown_physical_keys_are_rejected(self):
        client = self.connected_client()
        with patch("taildesk.server.game_input.send_key") as send:
            for code in ("", "Fn", "keyf", None):
                self.assertEqual(self.send(client, kind="game_down", code=code).status_code, 400)
        send.assert_not_called()

    def test_screen_switch_and_disconnect_release_held_game_keys(self):
        client = self.connected_client()
        with patch("taildesk.server.game_input.send_key", return_value=True) as send:
            self.send(client, kind="game_down", code="KeyW")
            client.post("/api/monitor", json={"monitor": "display-2"})
            self.assertEqual(send.call_args_list[-1], call("KeyW", up=True))
            client.post("/api/input", json={"kind": "game_down", "monitor": "display-2", "code": "ShiftLeft"})
            client.post("/api/disconnect")
        self.assertEqual(send.call_args_list[-1], call("ShiftLeft", up=True))
        self.assertEqual(self.app.config["TAILDESK_STATE"].held_game_keys, set())

    def test_normal_typing_keeps_the_existing_key_path(self):
        client = self.connected_client()
        with patch("taildesk.server.pyautogui.keyDown") as typed, \
                patch("taildesk.server.game_input.send_key") as send:
            self.send(client, kind="down", key="f")
        typed.assert_called_once_with("f")
        send.assert_not_called()

    def test_game_mode_script_loads_before_sign_in(self):
        client = self.app.test_client()
        client.environ_base["REMOTE_ADDR"] = "192.0.2.10"
        self.assertEqual(client.get("/static/game_mode.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()
