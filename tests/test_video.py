from __future__ import annotations

import io
import base64
import json
import struct
import unittest

from PIL import Image

from taildesk.video import (
    effective_jpeg_quality,
    encode_delta_frame,
    validate_frame_rate,
)


class FrameRateTests(unittest.TestCase):
    def test_accepts_supported_endpoints_and_middle_values(self) -> None:
        for value in (1, 8, 20, 60, "60"):
            with self.subTest(value=value):
                self.assertEqual(validate_frame_rate(value), int(value))

    def test_rejects_out_of_range_non_integer_and_boolean_values(self) -> None:
        for value in (0, 61, -1, 1.5, True, None, "fast"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_frame_rate(value)


class FrameCompressionTests(unittest.TestCase):
    def test_requested_quality_cannot_exceed_host_setting(self) -> None:
        self.assertEqual(effective_jpeg_quality(70, 25), 25)
        self.assertEqual(effective_jpeg_quality(70, 90), 70)
        self.assertEqual(effective_jpeg_quality(15, 90), 25)
        self.assertEqual(effective_jpeg_quality("invalid", "invalid"), 65)

    def test_delta_frame_has_documented_header_and_decodable_jpeg(self) -> None:
        image = Image.new("RGB", (16, 12), (20, 110, 220))
        payload = encode_delta_frame(640, 480, [(32, 64, image)], quality=55)

        magic, width, height, count = struct.unpack_from(">4sIIH", payload)
        self.assertEqual((magic, width, height, count), (b"TDL1", 640, 480, 1))
        x, y, tile_width, tile_height, jpeg_length = struct.unpack_from(">HHHHI", payload, 14)
        self.assertEqual((x, y, tile_width, tile_height), (32, 64, 16, 12))
        jpeg = payload[26:]
        self.assertEqual(len(jpeg), jpeg_length)
        with Image.open(io.BytesIO(jpeg)) as decoded:
            self.assertEqual(decoded.size, image.size)
            self.assertEqual(decoded.format, "JPEG")

    def test_binary_delta_is_smaller_than_legacy_json_base64_envelope(self) -> None:
        image = Image.new("RGB", (128, 128), (20, 110, 220))
        payload = encode_delta_frame(640, 480, [(0, 0, image)], quality=55)
        jpeg_length = struct.unpack_from(">I", payload, 22)[0]
        jpeg = payload[26:26 + jpeg_length]
        legacy = json.dumps({
            "width": 640,
            "height": 480,
            "tiles": [{"x": 0, "y": 0, "width": 128, "height": 128,
                      "jpeg": base64.b64encode(jpeg).decode("ascii")}],
        }, separators=(",", ":")).encode("utf-8")
        self.assertLess(len(payload), len(legacy))

    def test_rejects_tiles_outside_frame(self) -> None:
        tile = Image.new("RGB", (8, 8), "black")
        with self.assertRaisesRegex(ValueError, "outside"):
            encode_delta_frame(10, 10, [(5, 5, tile)], quality=65)


if __name__ == "__main__":
    unittest.main()
