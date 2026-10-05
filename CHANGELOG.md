# Changelog

## 1.0.3

- Add a screen selector beside Fullscreen for every detected monitor. Switch capture, input coordinates, and per-monitor display mode together; release held input and restore the previous monitor before switching, and recover if a monitor is unplugged.
- Synchronize standard Windows cursor shapes, including text, link, resize, busy, and hidden cursors, using input/frame response headers with ordering protection and no additional polling connection.
- Remove the fixed 16 ms pointer delay, keep coalesced motion in order around drag transitions, speed up BGRA conversion and JPEG encoding, decode up to eight changed tiles concurrently, and keep image processing outside the input lock.
- Correct the native Windows display-mode structure layout and preserve resolution restoration when audio restoration fails.

- Open a host settings dashboard on the host PC instead of starting a self-viewing desktop session. Show a copyable connection address for the other device; block host-local screen, input, heartbeat, and clipboard control requests.
- Let unauthenticated remote browsers load all required viewer scripts so the login screen works on a new laptop or browser.
- Reject IPv4 bind addresses that are not assigned to the host. Keep local settings and the tray available when a remote listener fails, and retry automatic Tailscale discovery while it starts.
- Report the actual running listener address, restart requirements, and startup errors. Save rotating diagnostic logs and avoid visible console windows for Tailscale CLI checks.
- Add regression tests for host-only startup, remote login assets and control, proxy authentication, interface validation, listener recovery, and display restoration.
- Add cursor, multi-monitor, negative-coordinate, screen-switch, and parallel tile-decoding regression coverage. Stop release packaging immediately if any verification command fails.

## 1.0.2

- Show the running host version in the remote masthead and add a toggleable Stats for nerds overlay with measured image payload bitrate, updated-frame rate, screen poll rate, frame request time, host resolution, browser viewport, and adaptive stream settings.
- Cache rejected or substituted host display-size requests while the requested and actual modes are unchanged, preventing heartbeat retries from repeatedly resetting Windows hover and popup state.
- Add regression coverage for rejected display modes and browser stream statistics.

## 1.0.1

- Avoid reapplying the host display mode on every browser heartbeat when the requested resolution is already active. This prevents needless Windows display resets that can dismiss taskbar flyouts and hover UI; native display restoration on disconnect is unchanged.
- Add display-controller regression tests for repeated heartbeat sizes and native-resolution sessions.

## 1.0.0

- Raise the configurable maximum frame rate from 20 to 60 FPS and use 60 FPS as the default cap for new installations.
- Keep the adaptive polling loop and adapt JPEG quality down when frame processing or transfer misses its budget, then restore quality as performance improves.
- Send changed screen tiles in a compact binary envelope instead of JSON with base64-encoded JPEGs, reducing delta-frame payload overhead.
- Compare the captured frame to its predecessor once per update before testing individual tiles, reducing repeated image-difference work.
- Coalesce queued pointer movement so slow remote requests do not leave stale movement events moving the host cursor after the user stops; keep hover state and menu interactions stable.
- Move remote audio status and the VB-Audio vendor link from the masthead into Settings.
- Add automated frame-rate validation, JPEG quality-cap, delta-frame format, and tile-bounds tests; verify Python compilation and browser JavaScript syntax in the Windows release workflow before packaging.
