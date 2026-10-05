import ctypes
import unittest
from unittest.mock import patch

from taildesk.cursor import CURSORINFO, cursor_style


class CursorTests(unittest.TestCase):
    def cursor(self, flags=1, handle=0x100000002, success=True):
        class API:
            def GetCursorInfo(inner, ptr):
                info = ctypes.cast(ptr, ctypes.POINTER(CURSORINFO)).contents
                self.assertEqual(info.cbSize, ctypes.sizeof(CURSORINFO))
                info.flags = flags
                info.hCursor = handle
                return success
        return API()

    def test_text_cursor_preserves_64_bit_handle(self):
        with patch("taildesk.cursor._api", return_value=(self.cursor(), {0x100000002: "text"})):
            self.assertEqual(cursor_style(), "text")

    def test_hidden_or_suppressed_cursor_is_hidden(self):
        for flags in (0, 2):
            with patch("taildesk.cursor._api", return_value=(self.cursor(flags=flags), {})):
                self.assertEqual(cursor_style(), "none")

    def test_unknown_or_failed_cursor_read_falls_back(self):
        for result in ((None, {}), (self.cursor(), {}), (self.cursor(success=False), {})):
            with patch("taildesk.cursor._api", return_value=result):
                self.assertEqual(cursor_style(), "default")
