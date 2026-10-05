from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import queue
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from taildesk.config import APP_DIR

LOG = logging.getLogger("TailDesk.audio")
VIRTUAL_SPEAKER_NAME = "CABLE Input"
DRIVER_DOWNLOAD_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
DRIVER_ARCHIVE = APP_DIR / "VBCABLE_Driver_Pack45.zip"
DRIVER_SHA256 = "B950E39F01AF1D04EA623C8F6D8EB9B6EA5C477C637295FABF20631C85116BFB"
DRIVER_PACKAGE_DIR = APP_DIR / "vb-cable-pack45"
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
        self.installing = False
        self.setup_process_handle: int | None = None
        self.driver_installed_callback = None
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

    def open_driver_setup(self) -> str:
        """Open the original signed VB-CABLE installer with Windows UAC."""
        if self._find_virtual_device():
            return "VB-CABLE is already installed."
        with self.lock:
            if self.installing:
                return "The VB-CABLE installer is already open on the host."
            self.installing = True
            self.error = None
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            if not DRIVER_ARCHIVE.is_file() or hashlib.sha256(DRIVER_ARCHIVE.read_bytes()).hexdigest().upper() != DRIVER_SHA256:
                request = urllib.request.Request(
                    DRIVER_DOWNLOAD_URL,
                    headers={"User-Agent": "TailDesk/0.1 VB-CABLE installer"},
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.geturl() != DRIVER_DOWNLOAD_URL:
                        raise ValueError("The VB-CABLE download redirected away from its official vendor URL.")
                    archive_bytes = response.read(5 * 1024 * 1024 + 1)
                if len(archive_bytes) > 5 * 1024 * 1024:
                    raise ValueError("The VB-CABLE package exceeded its expected download size.")
                if hashlib.sha256(archive_bytes).hexdigest().upper() != DRIVER_SHA256:
                    raise ValueError("The VB-CABLE download failed its SHA-256 verification.")
                temporary_archive = DRIVER_ARCHIVE.with_suffix(".download")
                temporary_archive.write_bytes(archive_bytes)
                temporary_archive.replace(DRIVER_ARCHIVE)
            if hashlib.sha256(DRIVER_ARCHIVE.read_bytes()).hexdigest().upper() != DRIVER_SHA256:
                raise ValueError("The official VB-CABLE package failed its SHA-256 check.")
            DRIVER_PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(DRIVER_ARCHIVE) as package:
                for member in package.infolist():
                    member_path = Path(member.filename)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError("The audio driver package contains an unsafe path.")
                package.extractall(DRIVER_PACKAGE_DIR)
            setup_path = DRIVER_PACKAGE_DIR / "VBCABLE_Setup_x64.exe"
            if not setup_path.is_file():
                raise FileNotFoundError("The VB-CABLE 64-bit installer is missing from the verified package.")
            self._remember_defaults_before_install()
            process = self._launch_elevated_setup(setup_path)
            self.setup_process_handle = process
            threading.Thread(
                target=self._watch_driver_install, args=(process,), name="taildesk-driver-install-watch", daemon=True
            ).start()
            return "VB-Audio's installer opened on the host. Choose Install Driver there; TailDesk will update this status when it finishes. VB-CABLE is donationware."
        except Exception:
            with self.lock:
                self.installing = False
            raise

    @staticmethod
    def _launch_elevated_setup(setup_path: Path) -> int:
        class ShellExecuteInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong),
                ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
                ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
                ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                ("dwHotKey", ctypes.c_ulong), ("hIconOrMonitor", ctypes.c_void_p),
                ("hProcess", ctypes.c_void_p),
            ]

        info = ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        info.lpVerb = "runas"
        info.lpFile = str(setup_path)
        info.lpDirectory = str(setup_path.parent)
        info.nShow = 1
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
        shell32.ShellExecuteExW.restype = ctypes.c_int
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            error = ctypes.get_last_error()
            if error == 1223:
                raise RuntimeError("Windows administrator approval was cancelled.")
            raise OSError(error, "Windows could not open the VB-CABLE installer.")
        if not info.hProcess:
            raise OSError("Windows started the installer without returning a process handle.")
        return int(info.hProcess)

    def _watch_driver_install(self, process_handle: int) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        wait_for_single_object.restype = ctypes.c_uint32
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        process_exited = False
        try:
            for _ in range(1800):
                time.sleep(2)
                process_exited = wait_for_single_object(process_handle, 0) == 0
                if process_exited:
                    self.setup_process_handle = None
                try:
                    device = self._find_virtual_device()
                except Exception:
                    LOG.exception("Could not check whether VB-CABLE was installed")
                    device = None
                if device:
                    self.installing = False
                    self.error = "VB-CABLE is installed. Windows may require a restart before it becomes available to apps."
                    if self.driver_installed_callback:
                        self.driver_installed_callback()
                    return
                if process_exited:
                    self.installing = False
                    self.error = "VB-CABLE setup closed before its audio device appeared. If you canceled it, click Install VB-CABLE to try again."
                    return
            self.installing = False
            self.error = "VB-CABLE setup is still running. Finish or close it on the host."
        finally:
            close_handle(process_handle)
            self.setup_process_handle = None

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
            installing = self.installing
        try:
            device = self._find_virtual_device()
        except Exception:
            LOG.exception("Could not inspect Windows audio devices")
            device = None
        if device:
            return {"available": True, "active": False,
                    "message": error or "Virtual speaker is installed; audio will switch on connection."}
        return {"available": False, "active": False, "installing": installing,
                "message": error or ("Waiting for Windows administrator approval to install VB-CABLE."
                                     if installing else "Install VB-CABLE from VB-Audio to enable remote sound.")}

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
