# TailDesk project instructions

For every requested code change or feature in this project, read `UPDATE_PIPELINE.md` and follow it: complete and review the change, commit and push it to the connected GitHub repository, publish a new versioned GitHub Release, then update the host PC from that release. Do not deploy an unreleased working copy.

Keep the default listener bound to the Tailnet IPv4 address only. Do not add router port-forwarding or Tailscale Funnel instructions. Preserve password authentication, safe file-path handling, single-controller ownership, held-key and mouse-button release, and unconditional display-mode and audio-output restoration on disconnect or timeout.

Keep the update pipeline documented in `UPDATE_PIPELINE.md` so it remains with the project across ChatGPT sessions.

The Windows release setup must bundle the host runtime and packages, provide a sign-in startup choice, and preserve `%APPDATA%\TailDesk` and `%USERPROFILE%\Downloads\TailDesk` when updating. Auto-update reads public GitHub releases and validates the setup asset digest before installing. Do not embed credentials in the client.
