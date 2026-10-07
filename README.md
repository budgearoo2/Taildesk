# TailDesk

TailDesk is an early Windows 11 Home remote-control host. It serves an authenticated browser desktop on the PC's Tailnet IPv4 address, so a second Windows, macOS, Linux, or mobile device can connect through a browser while Tailscale is connected.

## Realtime video and sound (1.0.4)

Refresh the remote browser after updating. Realtime mode starts automatically; click **Enable remote sound** once to allow browser playback. Play YouTube or other media on the host and watch/listen in the remote browser. VB-CABLE must be installed for sound; apps pinned to another audio output may need Windows Volume Mixer set to CABLE Input.

Steady 30 FPS with better picture quality is the default target. Settings supports up to 60 FPS, but 30 FPS leaves more encoding time and bandwidth per frame. Existing saved frame-rate settings are preserved when upgrading. Stats reports received/decoded FPS and network RTT. Capture and audio queues are bounded, H.264 disables B-frames and lookahead, and the sender adapts bitrate to receiver feedback. Frame pacing skips missed deadlines instead of accumulating old frames. Audio uses 48 kHz stereo in 20 ms chunks, sent as Opus. NVIDIA encoding uses the P5 quality preset with spatial adaptive quantization and variable bitrate, while keeping lookahead and B-frames disabled for responsive control.

Signaling retains the existing password and controller ownership checks. WebRTC uses encrypted media/data sockets bound only to Tailnet IPv4 addresses, with no public STUN or TURN service. Windows Firewall must allow UDP for TailDesk from the Tailnet for realtime mode. If media cannot connect, the browser falls back to HTTP/JPEG compatibility mode. Existing TCP-only firewall rules permit fallback but do not enable realtime media.

## Current features

- Live host screen in a browser, keyboard and mouse forwarding, and a responsive host display mode. Pointer positions are mapped across the visible desktop and the host process uses per-monitor DPI awareness to keep pointer input aligned with captured pixels. Mouse buttons stay held through pointer movement so click-and-drag text selection works; redundant display-mode resets are skipped on heartbeats so Windows hover state and taskbar flyouts can remain open; held keys send browser repeat events.
- The remote masthead shows the host app version. **Stats** toggles a live overlay with image payload bitrate, updated-frame rate, poll rate, request time, host resolution, browser viewport, and adaptive stream settings.
- The **Screen** dropdown beside **Fullscreen** lists all detected monitors and their resolutions. Capture and pointer coordinates follow the selection, including screens left of or above the primary monitor. Switching releases held keys/buttons, restores the previous monitor's resolution, and requests a fresh frame. The list refreshes on heartbeats; removing the selected display falls back to an available screen.
- The laptop cursor follows the host's standard Windows cursor shape: text caret, link hand, resize arrows, busy, and hidden cursors. Cursor metadata rides on existing input/frame responses, including unchanged frames. The visible pointer is rendered locally; custom application cursor artwork currently falls back to the default arrow.
- Compatibility mode screen updates use changed 128-pixel tiles when possible. Delta frames use a compact binary envelope to avoid JSON/base64 overhead. The browser adapts polling rate and JPEG quality to measured end-to-end capture, transfer, and decode time; the default 30 FPS setting is an upper limit and actual performance depends on the host and connection.
- The original display mode is saved before the first resolution change and restored on explicit disconnect, a 12-second lost-client timeout, sign-out, or app shutdown.
- Pointer events are sent without an extra fixed timer, and only adjacent unsent motion is coalesced so drag/key transitions stay ordered. Fast JPEG encoding trades a modest payload increase for less CPU delay; independent changed tiles decode concurrently with a limit of eight. Image comparison and compression do not hold the input/controller lock.
- Clipboard text sync in both directions, including Command+C/Command+V from a Mac browser. Browsers permit automatic clipboard access on HTTPS; Safari may still require a user gesture. The direct Tailnet HTTP address uses a manual copy/paste panel opened on demand from the masthead Clipboard button, so host clipboard updates do not cover the remote desktop.
- Trackpad wheel deltas are forwarded to Windows as smooth wheel input, including browser-provided momentum events.
- Remote audio switches Windows playback to VB-Audio's VB-CABLE during the controller session, relays its 48 kHz stereo audio to the browser, and restores each prior Windows output on disconnect or timeout. Click **Enable remote sound** once per browser session to satisfy playback policies. Audio status and the vendor link are in Settings rather than the masthead.
- Upload to and download from a dedicated host transfer folder. Uploads are limited to 256 MB and stored by sanitized basename.
- Tray icon switches between disconnected and connected colors; its menu opens a host-local settings dashboard without a password, configures GitHub updates, or exits the host. The dashboard displays a copyable URL for the laptop and never captures the screen, takes desktop control, or changes display/audio settings. Direct connections from this PC's own listening address also cannot control it. Other Tailnet browser sessions still require the admin password.
- Settings page can change port, bind address, maximum frame rate, image quality, clipboard, transfer folder, and Windows sign-in startup behavior.
- A 14-character minimum admin password is required on first launch for Tailnet connections. Only a salted PBKDF2 hash is stored in the app settings.
- The Windows setup executable includes TailDesk and its Python runtime/packages. Its startup checkbox controls whether TailDesk runs when that Windows user signs in.
- A small supervisor process keeps the host running. If the host process crashes, or stops answering its local health check for about a minute after it has started listening, the supervisor ends it, resets every display to its saved Windows mode, releases any keys or mouse buttons still held, and starts it again minimized. Audio output is restored by the restarted host from its saved routing snapshot. Restarts back off from 2 seconds to 1 minute when crashes repeat quickly. Choosing **Quit TailDesk** in the tray stops both processes.
- Setup adds TailDesk to the Start menu, so searching Windows for **TailDesk** starts it again. Launching it while it is already running opens the host settings page instead of starting a second copy.
- Packaged installations check the latest stable GitHub release at each start after a repository read-only token is configured from the tray. A newer setup program is installed only after its SHA-256 matches the release metadata.

## Install the packaged application

1. Download and run `TailDesk-Setup-<version>.exe` from the [latest GitHub release](https://github.com/budgearoo2/Taildesk/releases/latest). It installs TailDesk with its runtime and dependencies, so Python does not need to be installed separately. Choose whether it should start when you sign in to Windows. Setup also adds a **TailDesk** Start menu shortcut, so you can find it in Windows search.
2. On first launch, create the TailDesk admin password.
3. To enable automatic updates, open the local tray menu and choose **Configure GitHub updates**. Create a fine-grained personal access token restricted to `budgearoo2/Taildesk` with **Contents: Read-only** access. TailDesk verifies it against GitHub, then encrypts it with Windows DPAPI for the current Windows account. You can revoke the token or remove it from the tray menu later.

## Run from source for development

Install 64-bit Python 3.13 or later, then open PowerShell in this folder and install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

Ensure Tailscale is installed, connected, and signed in on the host. Run:

   ```powershell
   python .\remote_desktop_connection.py
   ```

Then set an admin password in the first-run dialog. The app opens the admin page. From another Tailnet device, open `http://<host-tailnet-ipv4>:8765/` and sign in with that password. Find the host address with `tailscale ip -4`.

### Remote audio setup

VB-Audio's [VB-CABLE Driver Pack 45](https://vb-audio.com/Cable/) supports Windows 11 and is supplied as donationware. Settings and the tray identify the vendor and link to its site. Click **Install VB-CABLE** in the web UI or choose **Install VB-CABLE audio device** in the tray menu. TailDesk downloads the unmodified package from VB-Audio's official download host and verifies its pinned SHA-256 before opening the vendor installer. Windows shows its standard administrator approval prompt; in VB-Audio's installer choose **Install Driver**. TailDesk watches the installer and reports when the device appears. VB-Audio says Windows may need a restart after installation. Windows per-app output assignments can override the system default, so route those apps to **CABLE Input (VB-Audio Virtual Cable)** in Windows Volume Mixer if their sound still plays locally.

By default, TailDesk also asks Tailscale Serve to provide a private HTTPS URL on port `8443` pointing to its local web server. Tailscale Serve may ask a Tailnet administrator to enable HTTPS certificates the first time. Open that HTTPS URL from a remote Tailnet device to enable browser clipboard APIs; browser permission rules still apply. The `http://100.x.x.x:8765/` URL remains available when you need direct IP access; ordinary HTTP pages use the manual clipboard controls. The HTTPS link uses the host's Tailnet DNS name and is available only inside your Tailnet. See [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) and the [Serve CLI reference](https://tailscale.com/docs/reference/tailscale-cli/serve).

The default bind setting is `auto`: it binds only to the IPv4 address returned by `tailscale ip -4`. If Tailscale is still starting, local settings remain available and TailDesk retries every five seconds. Invalid or unavailable explicit addresses leave the local dashboard and tray available with an error; choose `auto`, save, quit from the tray, and reopen the app. Settings reject addresses not currently assigned to this PC. The dashboard reports the actual listening URL and indicates when saved connection changes require a restart. The app does not offer a wildcard bind. The port defaults to `8765`. The setup checkbox controls Windows sign-in startup; it can also be changed in Settings. The transfer folder defaults to `Downloads\TailDesk`.

The app grants full desktop control. Keep the address on your Tailnet, use a unique strong password, and do not expose the app through router port forwarding or Tailscale Funnel. Tailscale Serve is private to the Tailnet and uses an automatically provisioned TLS certificate; the app still has its own login. Windows may require an inbound firewall rule for direct IPv4 access on the Tailnet interface. If needed, run this in an elevated PowerShell window:

```powershell
New-NetFirewallRule -DisplayName "TailDesk (Tailnet only)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Profile Any
```

Only create this rule when you need it. If you change the app port, update the firewall rule to match. You can remove it with:

```powershell
Remove-NetFirewallRule -DisplayName "TailDesk (Tailnet only)"
```

### Reaching other web apps on the host

Tailscale does not limit how many ports one device can use on another, so a laptop can use TailDesk and any other host web app at the same time. If another app on the host does not load at `http://<host-tailnet-ipv4>:<port>/`, the usual causes are:

- The app listens only on `127.0.0.1`/`localhost`. Start it bound to the host's Tailnet IPv4 address (check its host or bind option), or publish it privately to the Tailnet with `tailscale serve --bg --tcp=<port> tcp://127.0.0.1:<port>`.
- Windows Firewall blocks the port. The first-run firewall prompt appears on the host screen; if it was dismissed, add a Tailnet-only rule like the TailDesk one above with that app's port.

Do not use router port forwarding or Tailscale Funnel for these apps either.

## Known limitations

- Realtime mode uses DXGI capture, NVIDIA H.264 when supported (CPU H.264 otherwise), WebRTC video/audio, and a reliable ordered input channel. The default cap is 30 FPS; Settings supports up to 60 FPS. Actual unique motion is limited by the host display/content refresh rate and client hardware. Relative mouse/pointer lock, gamepad forwarding, and custom game cursor artwork are not implemented. Network RTT and decoder time in Stats are not full input-to-display latency.
- Requires an interactive signed-in Windows session. UAC secure desktop, Windows sign-in screen, and other isolated desktops are not captured or controlled.
- Display drivers can reject browser viewport resolutions. In that case the app keeps the last accepted mode and still restores the saved original mode on disconnect.
- Clipboard browser APIs require a secure browser context for unattended synchronization. Over direct `http://100.x.x.x:8765`, use the visible manual clipboard controls.
- In JPEG compatibility mode, TailDesk begins at a moderate rate and raises it while frames complete quickly; if processing or transfer exceeds the frame budget, it lowers the rate and JPEG quality. Static screens send no image payload when unchanged. Fast moving content still requires frequent host capture and comparison, so 60 FPS is not guaranteed on every PC or Tailnet connection.
- Remote audio requires the signed VB-CABLE driver to be installed on the host. Click **Enable remote sound** after installation; Mac browsers require that user gesture before playing audio.
- Windows applications explicitly pinned to a physical output device in Volume Mixer may continue playing there while connected. Set those applications to the virtual speaker if they do not follow the system default.
- Automatic release checks use a fine-grained, repository-only, read-only GitHub token configured from the local tray. Without one, TailDesk runs normally but skips update checks.

## Configuration and logs

Settings, generated secret key, password salt, and password hash: `%APPDATA%\TailDesk\settings.json`. The admin password itself is never stored there; TailDesk stores a random salt and a PBKDF2-HMAC-SHA256 hash. The encrypted update token is stored separately as `%APPDATA%\TailDesk\github-token.dpapi`; only the same Windows account can decrypt it.

TailDesk temporarily saves the prior Windows output endpoints in `%APPDATA%\TailDesk\audio-routing.json` while redirecting a connected session. It clears the active snapshot after restoring the prior endpoints.

Host file transfer directory: `%USERPROFILE%\Downloads\TailDesk` by default.

The tray icon opens `http://127.0.0.1:<port>/`; direct requests from that local page skip the password screen and show host settings, without a remote-control session. Open the displayed connection URL on the laptop, not the host. Remote Tailnet browser settings remain protected by the admin password. A newly opened remote browser can load all viewer scripts before signing in, while control and settings APIs remain authenticated.

Startup and connection diagnostics are saved in `%APPDATA%\TailDesk\taildesk.log` (rotated at 1 MB with two backups). Supervisor restarts, with the host process's exit code (for example `0xC0000005` for a native access violation), are logged in `%APPDATA%\TailDesk\taildesk-supervisor.log`. Python tracebacks from native crashes are appended to `%APPDATA%\TailDesk\taildesk-crash.log`, so a crash leaves evidence even when it bypasses normal logging. If the local port is already occupied, startup reports the error in a dialog rather than failing silently. Optional Tailscale HTTPS setup runs in the background so it cannot delay the tray or direct connection.

## Development

This app currently targets Windows 11. Keep Windows-specific APIs inside `taildesk/display.py` and startup registration in `taildesk/startup.py`. Keep the web interface in `taildesk/web/`.

See [UPDATE_PIPELINE.md](UPDATE_PIPELINE.md) for the release-before-install process that should be followed for each iteration and new feature.
