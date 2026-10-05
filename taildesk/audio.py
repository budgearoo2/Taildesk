from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import queue
import os
import subprocess
import threading
import zipfile
from pathlib import Path

from taildesk.config import APP_DIR

LOG = logging.getLogger("TailDesk.audio")
VIRTUAL_SPEAKER_NAME = "Virtual Audio Driver"
DRIVER_ARCHIVE = Path(__file__).resolve().parent / "third_party" / "VirtualAudioDriver-25.7.14.zip"
DRIVER_SHA256 = "DD10560994DE65A7E587FB8B93C0D7E9838292D9C3566A0976C2786D727292BD"
AUDIO_STATE_FILE = APP_DIR / "audio-routing.json"
AUDIO_RATE = 48_000
AUDIO_CHANNELS = 2
FRAMES_PER_CHUNK = 4_800


class AudioRouter:
    """Switch Windows playback to the virtual speaker and relay its WASAPI loopback."""

    def __init__(self, recover_interrupted_route: bool = True) -> None:
        self.lock = threading.RLock()
        self.chunks: queue.Queue[bytes] = queue.Queue(maxsize=12)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.device_id: str | None = None
        self.original_defaults: dict[int, str] = {}
        self.error: str | None = None
        if recover_interrupted_route:
            self._recover_stale_route()

    @staticmethod
    def _read_saved_route() -> dict | None:
        try:
            data = json.loads(AUDIO_STATE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _save_route(phase: str, defaults: dict[int, str], device_id: str | None = None) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        temp = AUDIO_STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({"phase": phase, "device_id": device_id, "defaults": defaults}, indent=2), encoding="utf-8")
        temp.replace(AUDIO_STATE_FILE)

    @staticmethod
    def _forget_saved_route() -> None:
        try:
            AUDIO_STATE_FILE.unlink(missing_ok=True)
        except OSError:
            LOG.exception("Could not clear the saved audio endpoint state")

    @staticmethod
    def _current_defaults(enumerator, flow, role) -> dict[int, str]:
        defaults: dict[int, str] = {}
        for role_id in (role.eConsole.value, role.eMultimedia.value, role.eCommunications.value):
            endpoint = enumerator.GetDefaultAudioEndpoint(flow.eRender.value, role_id)
            defaults[role_id] = endpoint.GetId()
        return defaults

    @staticmethod
    def _restore_defaults(enumerator, policy, flow, device_id: str, defaults: dict[int, str]) -> bool:
        restored = True
        for role_id, original_id in defaults.items():
            try:
                current = enumerator.GetDefaultAudioEndpoint(flow.eRender.value, role_id)
                if current.GetId() == device_id:
                    policy.SetDefaultEndpoint(original_id, role_id)
            except Exception:
                restored = False
                LOG.exception("Could not restore a Windows audio output")
        return restored

    def _recover_stale_route(self) -> None:
        saved = self._read_saved_route()
        if not saved or saved.get("phase") != "active" or not saved.get("device_id"):
            return
        try:
            (comtypes, _soundcard, policy_interface, policy_class, flow, _role, utilities) = self._audio_apis()
            comtypes.CoInitialize()
            try:
                enumerator = utilities.GetDeviceEnumerator()
                policy = comtypes.CoCreateInstance(policy_class, policy_interface, comtypes.CLSCTX_ALL)
                restored = self._restore_defaults(
                    enumerator, policy, flow, str(saved["device_id"]),
                    {int(key): str(value) for key, value in saved.get("defaults", {}).items()},
                )
            finally:
                comtypes.CoUninitialize()
            if restored:
                self._forget_saved_route()
                LOG.info("Recovered the previous Windows audio output after an interrupted session")
        except Exception:
            LOG.exception("Could not recover a previous Windows audio route")

    def _remember_defaults_before_install(self) -> None:
        saved = self._read_saved_route()
        if saved and saved.get("phase") == "pending":
            return
        (comtypes, _soundcard, _policy_interface, _policy_class, flow, role, utilities) = self._audio_apis()
        comtypes.CoInitialize()
        try:
            defaults = self._current_defaults(utilities.GetDeviceEnumerator(), flow, role)
        finally:
            comtypes.CoUninitialize()
        self._save_route("pending", defaults)

    @staticmethod
    def open_driver_setup() -> None:
        """Open the local Windows device wizard with the pinned signed driver unpacked."""
        router = AudioRouter(recover_interrupted_route=False)
        if router._find_virtual_device():
            ctypes.windll.user32.MessageBoxW(
                None, "The TailDesk virtual audio device is already installed.",
                "TailDesk audio", 0x40,
            )
            return
        router._remember_defaults_before_install()
        if hashlib.sha256(DRIVER_ARCHIVE.read_bytes()).hexdigest().upper() != DRIVER_SHA256:
            raise ValueError("The bundled virtual audio driver package failed its SHA-256 check.")
        destination = APP_DIR / "audio-driver-25.7.14"
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(DRIVER_ARCHIVE) as package:
            for member in package.infolist():
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError("The audio driver package contains an unsafe path.")
            package.extractall(destination)
        driver_folder = destination / "Virtual Audio Driver"
        message = (
            "TailDesk uses the signed Virtual Audio Driver by VirtualDrivers (MIT; Microsoft Sysvad notices included).\n\n"
            "In Device Manager, choose Action > Add legacy hardware > install manually > Sound, video and game controllers > Have Disk, then select:\n\n"
            f"{driver_folder / 'VirtualAudioDriver.inf'}\n\n"
            "Approve the Windows administrator prompt. Restart Windows if it asks. On your next TailDesk connection, the virtual speaker will become the default output and return to your previous output when you disconnect."
        )
        ctypes.windll.user32.MessageBoxW(None, message, "Install TailDesk virtual audio", 0x40)
        os.startfile(destination)
        subprocess.Popen(["mmc.exe", "devmgmt.msc"], close_fds=True)

    @staticmethod
    def _audio_apis():
        import comtypes
        import soundcard
        from pycaw.api.policyconfig import IPolicyConfig
        from pycaw.constants import CLSID_CPolicyConfigClient, EDataFlow, ERole
        from pycaw.utils import AudioUtilities

        return comtypes, soundcard, IPolicyConfig, CLSID_CPolicyConfigClient, EDataFlow, ERole, AudioUtilities

    @staticmethod
    def _is_active(device) -> bool:
        return getattr(getattr(device, "state", None), "name", "").casefold() == "active"

    def _find_virtual_device(self):
        (comtypes, _soundcard, _policy_interface, _policy_class, flow, _role, utilities) = self._audio_apis()
        comtypes.CoInitialize()
        try:
            devices = utilities.GetAllDevices(data_flow=flow.eRender.value)
            return next(
                (device for device in devices
                 if self._is_active(device) and device.FriendlyName
                 and VIRTUAL_SPEAKER_NAME.casefold() in device.FriendlyName.casefold()),
                None,
            )
        finally:
            comtypes.CoUninitialize()

    def status(self) -> dict[str, object]:
        with self.lock:
            if self.device_id:
                return {"available": True, "active": self.thread is not None and self.thread.is_alive(),
                        "message": self.error or "Virtual speaker is routing audio to this remote session."}
            error = self.error
        try:
            device = self._find_virtual_device()
        except Exception:
            LOG.exception("Could not inspect Windows audio devices")
            device = None
        if device:
            return {"available": True, "active": False,
                    "message": error or "Virtual speaker is installed; audio will switch on connection."}
        return {"available": False, "active": False,
                "message": error or "Install the TailDesk signed virtual audio device to enable remote sound."}

    def connect(self) -> None:
        with self.lock:
            if self.device_id:
                return
            self.error = None
        try:
            (comtypes, soundcard, policy_interface, policy_class, flow, role, utilities) = self._audio_apis()
            comtypes.CoInitialize()
            try:
                enumerator = utilities.GetDeviceEnumerator()
                defaults = self._current_defaults(enumerator, flow, role)
                devices = utilities.GetAllDevices(data_flow=flow.eRender.value)
                virtual = next(
                    (device for device in devices
                     if self._is_active(device) and device.FriendlyName
                     and VIRTUAL_SPEAKER_NAME.casefold() in device.FriendlyName.casefold()),
                    None,
                )
                if virtual is None:
                    raise RuntimeError("The TailDesk virtual speaker is not installed yet.")
                loopback = next(
                    (mic for mic in soundcard.all_microphones(include_loopback=True)
                     if getattr(mic, "isloopback", False)
                     and VIRTUAL_SPEAKER_NAME.casefold() in mic.name.casefold()),
                    None,
                )
                if loopback is None:
                    raise RuntimeError("Windows did not expose loopback capture for the TailDesk virtual speaker.")
                saved = self._read_saved_route()
                if saved and saved.get("phase") == "pending":
                    before_install = {int(key): str(value) for key, value in saved.get("defaults", {}).items()}
                    for role_id, endpoint_id in defaults.items():
                        if endpoint_id == virtual.id and role_id in before_install:
                            defaults[role_id] = before_install[role_id]
                policy = comtypes.CoCreateInstance(policy_class, policy_interface, comtypes.CLSCTX_ALL)
                with self.lock:
                    self.device_id = virtual.id
                    self.original_defaults = defaults
                self._save_route("active", defaults, virtual.id)
                try:
                    for role_id in defaults:
                        policy.SetDefaultEndpoint(virtual.id, role_id)
                except Exception:
                    for role_id, endpoint_id in defaults.items():
                        try:
                            policy.SetDefaultEndpoint(endpoint_id, role_id)
                        except Exception:
                            LOG.exception("Could not restore an audio endpoint after switching failed")
                    raise
            finally:
                comtypes.CoUninitialize()

            self.stop_event.clear()
            with self.lock:
                self.device_id = virtual.id
                self.original_defaults = defaults
                self.error = None
                self.thread = threading.Thread(
                    target=self._capture_loop, args=(loopback,),
                    daemon=True, name="taildesk-audio-loopback",
                )
                self.thread.start()
            LOG.info("Switched Windows playback to the TailDesk virtual speaker")
        except Exception as exc:
            LOG.exception("Could not activate remote audio")
            self.disconnect()
            with self.lock:
                self.error = str(exc)

    def _capture_loop(self, loopback) -> None:
        import numpy

        try:
            with loopback.recorder(samplerate=AUDIO_RATE) as recorder:
                while not self.stop_event.is_set():
                    samples = recorder.record(numframes=FRAMES_PER_CHUNK)
                    if samples.ndim == 1:
                        samples = numpy.repeat(samples[:, None], AUDIO_CHANNELS, axis=1)
                    elif samples.shape[1] == 1:
                        samples = numpy.repeat(samples, AUDIO_CHANNELS, axis=1)
                    samples = numpy.clip(samples[:, :AUDIO_CHANNELS], -1.0, 1.0)
                    chunk = (samples * 32767).astype("<i2", copy=False).tobytes()
                    try:
                        self.chunks.put_nowait(chunk)
                    except queue.Full:
                        try:
                            self.chunks.get_nowait()
                        except queue.Empty:
                            pass
                        self.chunks.put_nowait(chunk)
        except Exception as exc:
            LOG.exception("Windows audio capture stopped")
            with self.lock:
                self.error = f"Remote audio capture stopped: {exc}"
                self.thread = None
            threading.Thread(target=self.disconnect, daemon=True, name="taildesk-audio-restore").start()

    def next_chunk(self) -> bytes | None:
        try:
            return self.chunks.get_nowait()
        except queue.Empty:
            return None

    def disconnect(self) -> None:
        with self.lock:
            thread = self.thread
            device_id = self.device_id
            defaults = dict(self.original_defaults)
            self.thread = None
            self.device_id = None
            self.original_defaults.clear()
            self.stop_event.set()
        if thread and thread.is_alive():
            thread.join(timeout=2)
        while True:
            try:
                self.chunks.get_nowait()
            except queue.Empty:
                break
        if not device_id or not defaults:
            return
        restored = False
        try:
            (comtypes, _soundcard, policy_interface, policy_class, flow, role, utilities) = self._audio_apis()
            comtypes.CoInitialize()
            try:
                enumerator = utilities.GetDeviceEnumerator()
                policy = comtypes.CoCreateInstance(policy_class, policy_interface, comtypes.CLSCTX_ALL)
                restored = self._restore_defaults(enumerator, policy, flow, device_id, defaults)
            finally:
                comtypes.CoUninitialize()
            LOG.info("Restored the previous Windows audio output")
        except Exception:
            LOG.exception("Could not restore the previous Windows audio output")
        if restored:
            self._forget_saved_route()
