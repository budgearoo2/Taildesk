"""Frame-rate validation and compact JPEG delta-frame encoding."""

from __future__ import annotations

import io
import math
import numbers
import struct
from typing import Iterable

from PIL import Image

MIN_FRAME_RATE = 1
MAX_FRAME_RATE = 60
MIN_JPEG_QUALITY = 25
MAX_JPEG_QUALITY = 90


def validate_frame_rate(value: object) -> int:
    """Parse an allowed whole-number frame-rate cap from 1 to 60 FPS."""
    if isinstance(value, bool) or (
        isinstance(value, numbers.Real) and not math.isfinite(value)
    ):
        raise ValueError("Frame rate must be from 1 to 60.")
    if isinstance(value, numbers.Real) and not float(value).is_integer():
        raise ValueError("Frame rate must be a whole number from 1 to 60.")
    try:
        fps = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Frame rate must be from 1 to 60.") from exc
    if not MIN_FRAME_RATE <= fps <= MAX_FRAME_RATE:
        raise ValueError("Frame rate must be from 1 to 60.")
    return fps


def effective_jpeg_quality(configured: object, requested: object) -> int:
    """Apply a client quality hint without exceeding the host's configured cap."""
    try:
        host_cap = int(configured)
    except (TypeError, ValueError, OverflowError):
        host_cap = 65
    host_cap = max(MIN_JPEG_QUALITY, min(MAX_JPEG_QUALITY, host_cap))
    try:
        requested_quality = int(requested)
    except (TypeError, ValueError, OverflowError):
        requested_quality = host_cap
    return max(MIN_JPEG_QUALITY, min(host_cap, requested_quality))


def encode_delta_frame(
    width: int,
    height: int,
    tiles: Iterable[tuple[int, int, Image.Image]],
    quality: int,
) -> bytes:
    """Encode independent JPEG tiles in a compact, bounds-checked binary frame.

    Header: ``TDL1``, uint32 width, uint32 height, uint16 tile count.
    Each tile: uint16 x/y/width/height, uint32 JPEG byte length, then JPEG bytes.
    All integers are big-endian. The browser parser uses the same layout.
    """
    if not 1 <= width <= 65535 or not 1 <= height <= 65535:
        raise ValueError("Invalid frame dimensions")
    quality = max(MIN_JPEG_QUALITY, min(MAX_JPEG_QUALITY, int(quality)))
    tile_list = list(tiles)
    if len(tile_list) > 65535:
        raise ValueError("Too many frame tiles")

    payload = bytearray(struct.pack(">4sIIH", b"TDL1", width, height, len(tile_list)))
    for x, y, tile in tile_list:
        tile_width, tile_height = tile.size
        if (
            x < 0 or y < 0 or tile_width < 1 or tile_height < 1
            or x + tile_width > width or y + tile_height > height
            or x > 65535 or y > 65535 or tile_width > 65535 or tile_height > 65535
        ):
            raise ValueError("Frame tile is outside the frame bounds")
        output = io.BytesIO()
        tile.save(output, format="JPEG", quality=quality, optimize=False)
        jpeg = output.getvalue()
        payload.extend(struct.pack(">HHHHI", x, y, tile_width, tile_height, len(jpeg)))
        payload.extend(jpeg)
    return bytes(payload)
