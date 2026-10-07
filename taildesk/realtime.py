"""Tailnet-only WebRTC media with bounded buffering and hardware H.264."""
from __future__ import annotations

import asyncio
import concurrent.futures
import ctypes
import ipaddress
import json
import logging
import threading
import time
from fractions import Fraction

import aioice.ice
import av
import mss
from aiortc import MediaStreamTrack, RTCConfiguration, RTCPeerConnection, RTCRtpSender, RTCSessionDescription
from aiortc.codecs.h264 import H264Encoder
from aiortc.mediastreams import MediaStreamError

from taildesk.cursor import cursor_style
from taildesk.desktop_access import CAPTURE_PAUSED_MESSAGE, SECURE_DESKTOP_MESSAGE, notice_image, secure_desktop_active

LOG = logging.getLogger("TailDesk.realtime")
VIDEO_CLOCK = Fraction(1, 90000)
DXGI_RETRY_SECONDS = 5
_host_addresses = aioice.ice.get_host_addresses


def tailnet_address(address):
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def private_candidates(sdp):
    """Never contact public/LAN/mDNS candidates supplied by a remote browser."""
    lines = []
    for line in sdp.splitlines():
        if line.startswith("a=candidate:"):
            parts = line.split()
            if len(parts) < 8 or not tailnet_address(parts[4]) or parts[2].lower() != "udp":
                continue
        lines.append(line)
    return "\r\n".join(lines) + "\r\n"


# Pin aioice in requirements: bind media sockets only to Tailnet IPv4 interfaces.
aioice.ice.get_host_addresses = lambda use_ipv4, use_ipv6: [
    address for address in _host_addresses(use_ipv4, False) if tailnet_address(address)
]


class LowLatencyH264(H264Encoder):
    def __init__(self, fps):
        super().__init__()
        self.fps = fps
        self.hardware = True
        self.name = "Starting H.264"
        self.preset = "p5"
        self.configured_bitrate = 0
        self.target_bitrate = 12_000_000

    @property
    def target_bitrate(self):
        return self._bitrate

    @target_bitrate.setter
    def target_bitrate(self, value):
        self._bitrate = max(500_000, min(20_000_000, int(value)))

    def _encode_frame(self, frame, force_keyframe):
        if self.codec and (self.codec.width != frame.width or self.codec.height != frame.height
                           or abs(self.target_bitrate - self.configured_bitrate) > self.configured_bitrate * .25):
            self.codec = None
        if self.codec is None:
            for name in (["h264_nvenc", "libx264"] if self.hardware else ["libx264"]):
                try:
                    codec = av.CodecContext.create(name, "w")
                    codec.width, codec.height = frame.width, frame.height
                    codec.pix_fmt = "yuv420p"
                    codec.time_base = VIDEO_CLOCK
                    codec.framerate = Fraction(self.fps)
                    codec.bit_rate = self.target_bitrate
                    codec.gop_size = self.fps
                    codec.max_b_frames = 0
                    codec.options = ({"preset": self.preset, "tune": "ull", "zerolatency": "1",
                                      "delay": "0", "rc-lookahead": "0", "profile": "baseline", "rc": "vbr",
                                      "spatial-aq": "1", "aq-strength": "8", "cq": "20",
                                      "maxrate": str(self.target_bitrate), "bufsize": str(self.target_bitrate // 10)}
                                     if name == "h264_nvenc" else
                                     {"preset": "ultrafast", "tune": "zerolatency", "profile": "baseline"})
                    codec.open()
                    self.codec = codec
                    self.configured_bitrate = self.target_bitrate
                    self.name = "NVIDIA H.264" if name == "h264_nvenc" else "CPU H.264"
                    self.hardware = name == "h264_nvenc"
                    LOG.info("Realtime encoder: %s", self.name)
                    break
                except Exception:
                    if name == "libx264":
                        raise
                    LOG.info("NVIDIA encoder unavailable; using low-delay CPU H.264")
                    self.hardware = False
        frame.pict_type = av.video.frame.PictureType.I if force_keyframe else av.video.frame.PictureType.NONE
        data = b"".join(bytes(packet) for packet in self.codec.encode(frame))
        if data:
            yield from self._split_bitstream(data)


class DesktopTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, state, owner, fps):
        super().__init__()
        self.state, self.owner, self.fps = state, owner, fps
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="taildesk-capture")
        self.capture = None
        self.camera = None
        self.camera_key = None
        self.dxgi_retry_at = 0.0
        self.capture_failing = False
        self.com_ready = False
        self.close_future = None
        self.next_frame = 0

    def grab(self):
        with self.state.lock:
            if not self.state.can_control(self.owner):
                raise MediaStreamError
            monitor = self.state.current_monitor()
        if not self.com_ready:
            import comtypes
            comtypes.CoInitialize()
            self.com_ready = True
        # Keep frames flowing while Windows hides the desktop so the browser keeps
        # the realtime connection and the picture resumes by itself afterwards.
        if secure_desktop_active():
            self.release_camera()
            return self.notice_frame(monitor, SECURE_DESKTOP_MESSAGE)
        try:
            frame = self.capture_frame(monitor)
        except Exception:
            if not self.capture_failing:
                LOG.warning("Desktop capture failed; sending a placeholder until it recovers", exc_info=True)
            self.capture_failing = True
            self.release_camera()
            if self.capture:
                self.capture.close()
                self.capture = None
            return self.notice_frame(monitor, CAPTURE_PAUSED_MESSAGE)
        if self.capture_failing:
            LOG.info("Desktop capture recovered")
            self.capture_failing = False
        return frame

    def release_camera(self):
        if self.camera:
            try:
                self.camera.release()
            except Exception:
                LOG.debug("Could not release the DXGI camera", exc_info=True)
        self.camera = None
        self.camera_key = None

    def notice_frame(self, monitor, message):
        image = notice_image(monitor["width"] // 2 * 2, monitor["height"] // 2 * 2, message)
        frame = av.VideoFrame.from_image(image).reformat(format="yuv420p")
        frame.pts, frame.time_base = int(time.monotonic() * 90000), VIDEO_CLOCK
        return frame

    def capture_frame(self, monitor):
        if time.monotonic() >= self.dxgi_retry_at:
            try:
                import dxcam
                key = (monitor["id"], monitor["width"], monitor["height"])
                if self.camera_key != key:
                    self.release_camera()
                    # Version 0.3 is pinned; match actual Windows device names,
                    # never assume primary-first enumeration matches DXGI order.
                    factory = getattr(dxcam, "__factory")
                    indices = next((d, o) for d, outputs in enumerate(factory.outputs)
                                   for o, output in enumerate(outputs) if output.devicename == monitor["id"])
                    self.camera = dxcam.create(device_idx=indices[0], output_idx=indices[1],
                                               output_color="BGRA", processor_backend="numpy", max_buffer_len=2)
                    self.camera_key = key
                pixels = self.camera.grab(new_frame_only=False)
                if pixels is not None:
                    frame = av.VideoFrame.from_ndarray(pixels, format="bgra")
                    frame = frame.reformat(width=frame.width // 2 * 2, height=frame.height // 2 * 2, format="yuv420p")
                    frame.pts, frame.time_base = int(time.monotonic() * 90000), VIDEO_CLOCK
                    return frame
            except Exception:
                # DXGI loses access around desktop switches; retry it later instead of
                # staying on slower GDI capture for the rest of the session.
                LOG.warning("DXGI capture unavailable; using GDI for now", exc_info=True)
                self.dxgi_retry_at = time.monotonic() + DXGI_RETRY_SECONDS
                self.release_camera()
        if self.capture is None:
            self.capture = mss.mss()
        shot = self.capture.grab({key: monitor[key] for key in ("left", "top", "width", "height")})
        frame = av.VideoFrame(shot.width, shot.height, "bgra")
        # PyAV may pad rows; use ndarray only when the Windows stride differs.
        if frame.planes[0].line_size == shot.width * 4:
            frame.planes[0].update(shot.bgra)
        else:
            import numpy
            frame = av.VideoFrame.from_ndarray(numpy.asarray(shot), format="bgra")
        frame = frame.reformat(width=shot.width // 2 * 2, height=shot.height // 2 * 2, format="yuv420p")
        frame.pts = int(time.monotonic() * 90000)
        frame.time_base = VIDEO_CLOCK
        return frame

    async def recv(self):
        if self.readyState != "live" or not self.state.can_control(self.owner):
            raise MediaStreamError
        await asyncio.sleep(max(0, self.next_frame - time.monotonic()))
        now = time.monotonic()
        self.next_frame = max(self.next_frame + 1 / self.fps, now)
        # Pull only when the sender is ready: no growing capture/frame queue.
        return await asyncio.get_running_loop().run_in_executor(self.executor, self.grab)

    def stop(self):
        if self.readyState == "ended":
            return
        super().stop()
        def close():
            if self.capture:
                self.capture.close()
            if self.camera:
                self.camera.release()
            if self.com_ready:
                import comtypes
                comtypes.CoUninitialize()
        self.close_future = self.executor.submit(close)
        self.executor.shutdown(wait=False)


class DesktopAudio(MediaStreamTrack):
    kind = "audio"

    def __init__(self, state, owner):
        super().__init__()
        self.state, self.owner = state, owner
        self.next_frame = 0
        self.pts = None

    async def recv(self):
        if self.readyState != "live" or not self.state.can_control(self.owner):
            raise MediaStreamError
        await asyncio.sleep(max(0, self.next_frame - time.monotonic()))
        now = time.monotonic()
        self.next_frame = max(self.next_frame + .02, now)
        # Drop queued old sound after stalls; send silence instead of blocking video.
        chunk = self.state.audio.next_chunk()
        newer = self.state.audio.next_chunk()
        while newer is not None:
            chunk, newer = newer, self.state.audio.next_chunk()
        frame = av.AudioFrame(format="s16", layout="stereo", samples=960)
        frame.planes[0].update(chunk if chunk and len(chunk) == 3840 else bytes(3840))
        frame.sample_rate = 48000
        self.pts = self.pts + 960 if self.pts is not None else int(now * 48000)
        if int(now * 48000) - self.pts > 4800:
            self.pts = int(now * 48000)
        frame.pts, frame.time_base = self.pts, Fraction(1, 48000)
        return frame


class RealtimeSession:
    def __init__(self, state):
        self.state = state
        self.loop = asyncio.new_event_loop()
        self.peer = None
        self.tracks = []
        self.encoder = None
        self.lock = asyncio.Lock()
        self.generation = 0
        self.timer_active = False
        threading.Thread(target=self.loop.run_forever, daemon=True, name="taildesk-webrtc").start()

    def close(self):
        self.generation += 1
        asyncio.run_coroutine_threadsafe(self._close(), self.loop)

    async def _close(self):
        peer, self.peer = self.peer, None
        if self.timer_active:
            ctypes.windll.winmm.timeEndPeriod(1)
            self.timer_active = False
        tracks, self.tracks = self.tracks, []
        for track in tracks:
            track.stop()
        for track in tracks:
            if getattr(track, "close_future", None):
                await asyncio.wrap_future(track.close_future)
        if peer:
            await peer.close()

    def offer(self, owner, sdp, fps, apply_input):
        future = asyncio.run_coroutine_threadsafe(self._offer(owner, sdp, fps, apply_input), self.loop)
        try:
            return future.result(timeout=15)
        except Exception:
            future.cancel()
            self.close()
            raise

    async def _offer(self, owner, sdp, fps, apply_input):
        async with self.lock:
            await self._close()
            generation = self.generation
            if not self.state.can_control(owner):
                raise PermissionError("Desktop session ended")
            self.timer_active = ctypes.windll.winmm.timeBeginPeriod(1) == 0
            peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            self.peer = peer

            @peer.on("datachannel")
            def datachannel(channel):
                if channel.label != "control":
                    channel.close()
                    return

                @channel.on("message")
                def message(raw):
                    if not isinstance(raw, str) or len(raw) > 4096:
                        return
                    try:
                        data = json.loads(raw)
                        with self.state.lock:
                            if not self.state.can_control(owner) or self.peer is not peer:
                                return
                            monitor = self.state.current_monitor()
                            if data.get("monitor") != monitor["id"]:
                                return
                            apply_input(data, monitor)
                        channel.send(json.dumps({"cursor": cursor_style(), "stamp": str(time.monotonic_ns() // 1000)}))
                    except Exception:
                        LOG.debug("Discarded invalid realtime input", exc_info=True)

            @peer.on("connectionstatechange")
            async def changed():
                if peer.connectionState in {"failed", "closed"} and self.peer is peer:
                    await self._close()

            await peer.setRemoteDescription(RTCSessionDescription(sdp=private_candidates(sdp), type="offer"))
            for track in (DesktopTrack(self.state, owner, fps), DesktopAudio(self.state, owner)):
                self.tracks.append(track)
                sender = peer.addTrack(track)
                if track.kind == "video":
                    transceiver = next(t for t in peer.getTransceivers() if t.sender is sender)
                    transceiver.setCodecPreferences([c for c in RTCRtpSender.getCapabilities("video").codecs if c.mimeType.lower() == "video/h264"])
                    self.encoder = LowLatencyH264(fps)
                    # aiortc 1.14 is pinned; regression tests cover this encoder hook.
                    sender._RTCRtpSender__encoder = self.encoder
            await peer.setLocalDescription(await peer.createAnswer())
            if generation != self.generation or not self.state.can_control(owner):
                await self._close()
                raise PermissionError("Desktop session ended")
            return {"sdp": peer.localDescription.sdp, "type": "answer"}
