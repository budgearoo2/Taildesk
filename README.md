# TailDesk

TailDesk is an early Windows 11 Home remote-control host. It serves an authenticated browser desktop on the PC's Tailnet IPv4 address, so a second Windows, macOS, Linux, or mobile device can connect through a browser while Tailscale is connected.

## Current features

- Live host screen in a browser, keyboard and mouse forwarding, and a responsive host display mode. Pointer positions are mapped across the visible desktop and the host process uses per-monitor DPI awareness to keep pointer input aligned with captured pixels. Mouse buttons stay held through pointer movement so click-and-drag text selection works; held keys send browser repeat events.
- Screen updates use changed 128-pixel tiles when possible and adapt their polling rate to measured capture, transfer, and browser decode time. The Frame rate setting is the upper limit.
- The original display mode is saved before the first resolution change and restored on explicit disconnect, a 12-second lost-client timeout, sign-out, or app shutdown.
- Clipboard text sync in both directions, including Command+C/Command+V from a Mac browser. Browsers permit automatic clipboard access on HTTPS; Safari may still require a user gesture. The direct Tailnet HTTP address uses a manual copy/paste panel where browser policy blocks clipboard access.
- Trackpad wheel deltas are forwarded to Windows as smooth wheel input, including browser-provided momentum events.
- Remote audio switches Windows playback to a virtual speaker during the controller session, relays its 48 kHz stereo audio to the browser, and restores each prior Windows output on disconnect or timeout. Click **Enable remote sound** once per browser session to satisfy playback policies.
- Upload to and download from a dedicated host transfer folder. Uploads are limited to 256 MB and stored by sanitized basename.
- Tray icon switches between disconnected and connected colors; its menu opens the local admin page, starts the one-time virtual audio driver setup, or exits the host.
- Settings page can change port, bind address, maximum frame rate, image quality, clipboard, transfer folder, and Windows sign-in startup behavior.
- A 14-character minimum admin password is required on first launch. Only a salted PBKDF2 hash is stored in the app settings.

## Requirements and first launch

1. Install 64-bit Python 3.11 or later on the host PC.
2. Open PowerShell in this folder and install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Ensure Tailscale is installed, connected, and signed in on the host. Run:

   ```powershell
   python .\remote_desktop_connection.py
   ```

4. Set an admin password in the first-run dialog. The app opens the admin page. From another Tailnet device, open `http://<host-tailnet-ipv4>:8765/` and sign in with that password. Find the host address with `tailscale ip -4`.

### Remote audio setup

TailDesk includes the signed **Virtual Audio Driver** release 25.7.14 from [VirtualDrivers](https://github.com/VirtualDrivers/Virtual-Audio-Driver/releases/tag/25.7.14). This Windows driver is separately licensed under MIT, with Microsoft Sysvad sample notices included in `taildesk/third_party/`. Driver installation needs Windows administrator approval. In the TailDesk tray menu, choose **Install virtual audio device**; in Device Manager, choose **Action > Add legacy hardware > install manually > Sound, video and game controllers > Have Disk**, then select the extracted `VirtualAudioDriver.inf`. Restart Windows if requested. TailDesk detects this virtual speaker on the next remote connection. Windows per-app output assignments can override the system default, so route those apps to **Virtual Audio Driver** in Windows Volume Mixer if their sound still plays locally.

By default, TailDesk also asks Tailscale Serve to provide a private HTTPS URL on port `8443` pointing to its local web server. Tailscale Serve may ask a Tailnet administrator to enable HTTPS certificates the first time. When it succeeds, the tray opens that HTTPS URL, which removes the browser's insecure-context restriction on clipboard APIs; browser permission rules still apply. The `http://100.x.x.x:8765/` URL remains available when you need direct IP access; ordinary HTTP pages use the manual clipboard controls. The HTTPS link uses the host's Tailnet DNS name and is available only inside your Tailnet. See [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) and the [Serve CLI reference](https://tailscale.com/docs/reference/tailscale-cli/serve).

The default bind setting is `auto`: it binds only to the IPv4 address returned by `tailscale ip -4`; if Tailscale is unavailable, it falls back to localhost and remote access is disabled. The app does not offer a wildcard bind. The port defaults to `8765`. The first-run setting enables Windows sign-in startup; it can be disabled in Settings. The transfer folder defaults to `Downloads\TailDesk`.

The app grants full desktop control. Keep the address on your Tailnet, use a unique strong password, and do not expose the app through router port forwarding or Tailscale Funnel. Tailscale Serve is private to the Tailnet and uses an automatically provisioned TLS certificate; the app still has its own login. Windows may require an inbound firewall rule for direct IPv4 access on the Tailnet interface. If needed, run this in an elevated PowerShell window:

```powershell
New-NetFirewallRule -DisplayName "TailDesk (Tailnet only)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Profile Any
```

Only create this rule when you need it. If you change the app port, update the firewall rule to match. You can remove it with:

```powershell
Remove-NetFirewallRule -DisplayName "TailDesk (Tailnet only)"
```

## Limitations in this first iteration

- Requires an interactive signed-in Windows session. UAC secure desktop, Windows sign-in screen, and other isolated desktops are not captured or controlled.
- Display drivers can reject browser viewport resolutions. In that case the app keeps the last accepted mode and still restores the saved original mode on disconnect.
- Clipboard browser APIs require a secure browser context for unattended synchronization. Over direct `http://100.x.x.x:8765`, use the visible manual clipboard controls.
- The connection is optimized for a responsive control session, not high-frame-rate video. Increase the frame-rate cap or JPEG quality in Settings if the host and network can keep up. Tile updates lower bandwidth for mostly stationary screens, while moving content still needs frequent full-screen capture and comparison.
- Mac browsers require a user gesture before playing remote audio; click **Enable remote sound** after connecting. Remote audio requires the signed virtual audio driver to be installed on the host.
- Windows applications explicitly pinned to a physical output device in Volume Mixer may continue playing there while connected. Set those applications to the virtual speaker if they do not follow the system default.
- Python and its dependencies must be installed on the host. A packaged installer is a later release task.

## Configuration and logs

Settings and password hash: `%APPDATA%\TailDesk\settings.json`.

TailDesk temporarily saves the prior Windows output endpoints in `%APPDATA%\TailDesk\audio-routing.json` while installing the virtual audio device or redirecting a connected session. It clears the active snapshot after restoring the prior endpoints.

Host file transfer directory: `%USERPROFILE%\Downloads\TailDesk` by default.

The tray icon opens the local admin page. The same settings are available from the authenticated remote browser page.

## Development

This app currently targets Windows 11. Keep Windows-specific APIs inside `taildesk/display.py` and startup registration in `taildesk/startup.py`. Keep the web interface in `taildesk/web/`.

See [UPDATE_PIPELINE.md](UPDATE_PIPELINE.md) for the release-before-install process that should be followed for each iteration and new feature.
