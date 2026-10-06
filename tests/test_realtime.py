import asyncio
import threading
import unittest
from fractions import Fraction
from unittest.mock import MagicMock, patch

import av

from taildesk.realtime import DesktopAudio, LowLatencyH264, private_candidates, tailnet_address
from taildesk.audio import AudioRouter, FRAMES_PER_CHUNK


class RealtimeTests(unittest.TestCase):
    def test_candidates_restrict_media_to_tailnet_udp(self):
        sdp = "v=0\r\na=candidate:1 1 udp 1 100.64.0.8 4444 typ host\r\na=candidate:2 1 udp 1 192.168.0.1 4444 typ host\r\na=candidate:3 1 udp 1 secret.local 4444 typ host\r\na=candidate:4 1 udp 1 8.8.8.8 4444 typ host\r\n"
        result = private_candidates(sdp)
        self.assertIn("100.64.0.8", result)
        self.assertNotIn("192.168", result)
        self.assertNotIn("secret.local", result)
        self.assertNotIn("8.8.8.8", result)
        self.assertFalse(tailnet_address("127.0.0.1"))

    def test_cpu_encoder_immediately_outputs_decodable_keyframe_and_resize(self):
        encoder = LowLatencyH264(30)
        encoder.hardware = False
        decoder = av.CodecContext.create("h264", "r")
        for width, height in [(320, 180), (640, 360)]:
            frame = av.VideoFrame(width, height, "yuv420p")
            frame.pts, frame.time_base = 9000, Fraction(1, 90000)
            data = b"".join(b"\x00\x00\x00\x01" + nal for nal in encoder._encode_frame(frame, True))
            self.assertTrue(data)
            decoded = decoder.decode(av.Packet(data))
            self.assertEqual((decoded[0].width, decoded[0].height), (width, height))

    def test_audio_initializes_com_on_capture_thread_and_uninitializes(self):
        import numpy
        router = AudioRouter(recover_interrupted_route=False)
        recorder = MagicMock()
        def record(**kwargs):
            router.stop_event.set()
            return numpy.ones((960, 2)) * .25
        recorder.record.side_effect = record
        loopback = MagicMock()
        loopback.recorder.return_value.__enter__.return_value = recorder
        calls = []
        with patch("comtypes.CoInitialize", side_effect=lambda: calls.append(("init", threading.get_ident()))), patch("comtypes.CoUninitialize", side_effect=lambda: calls.append(("done", threading.get_ident()))):
            worker = threading.Thread(target=router._capture_loop, args=(loopback,))
            worker.start()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(calls, [("init", worker.ident), ("done", worker.ident)])
        self.assertEqual(len(router.next_chunk()), 3840)
        self.assertEqual(FRAMES_PER_CHUNK, 960)

    def test_audio_drops_old_buffer_and_preserves_twenty_ms_packet(self):
        state = MagicMock()
        state.can_control.return_value = True
        state.audio.next_chunk.side_effect = [bytes([1]) * 3840, bytes([2]) * 3840, None]
        track = DesktopAudio(state, "owner")
        frame = asyncio.run(track.recv())
        self.assertEqual(frame.samples, 960)
        self.assertEqual(bytes(frame.planes[0]), bytes([2]) * 3840)


if __name__ == "__main__":
    unittest.main()
