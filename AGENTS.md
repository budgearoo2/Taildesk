# TailDesk project instructions

Read `UPDATE_PIPELINE.md` for every change. Review and verify repository changes before committing and pushing them. Ordinary source, documentation, and workflow updates run verification without publishing. Publish a new versioned release only when an application release is explicitly intended; update the host only from that published release. Do not deploy an unreleased working copy.

Keep the default listener bound to the Tailnet IPv4 address only. Do not add router port-forwarding or Tailscale Funnel instructions. Preserve password authentication, safe file-path handling, single-controller ownership, held-key and mouse-button release, and unconditional display-mode and audio-output restoration on disconnect or timeout.

Host-local settings may auto-authenticate only for a direct loopback request using a loopback Host value. Do not grant this exception to Tailscale Serve proxy traffic or any non-loopback client; remote browser sessions must retain the admin password. Audio-driver installation must use only the pinned official VB-Audio VB-CABLE package, keep its vendor files unchanged, disclose the vendor and donationware terms, and use Windows UAC elevation.

Keep the update pipeline documented in `UPDATE_PIPELINE.md` so it remains with the project across ChatGPT sessions.

The Windows release setup must bundle the host runtime and packages, provide a sign-in startup choice, and preserve `%APPDATA%\TailDesk` and `%USERPROFILE%\Downloads\TailDesk` when updating. Auto-update reads GitHub releases with a repository-only read token encrypted by Windows DPAPI, and validates the setup asset digest before installing. Do not embed credentials in the client or expose them through remote web settings.
