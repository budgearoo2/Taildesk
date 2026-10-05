# Changelog

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
