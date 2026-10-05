# TailDesk update and release pipeline

This file is the durable project instruction for future sessions and feature requests.

For every follow-up fix, feature, or additional idea:

1. Make the change in the project and update the version and documentation.
2. Review the full change and make sure the setup, security notes, and release notes match the implementation.
3. Commit and push the complete change to the repository's `main` branch. The `.github/workflows/release.yml` workflow packages the source and publishes a new versioned GitHub Release from each push to `main`.
4. Wait for the release workflow to finish successfully. It publishes a source ZIP and a self-contained `TailDesk-Setup-<version>.exe`. Use that setup executable to install or update the host.
5. Only after the release is published, update the host from that release. Back up `%APPDATA%\TailDesk\settings.json` first. Run the published setup program, preserve the startup choice, settings, `%APPDATA%\TailDesk\audio-routing.json`, and `%USERPROFILE%\Downloads\TailDesk`, then start TailDesk and verify the host returns to its native resolution and original audio output after disconnect.

The setup executable bundles TailDesk and its Python runtime/packages; installed hosts do not need a separate Python or pip setup. Packaged app starts check GitHub's latest stable public release and install only a setup asset whose SHA-256 matches GitHub release metadata. The TailDesk repository must be public; if it is private, auto-update checks cannot read it and hosts need manual setup updates. Driver packages require separate Windows administrator approval and must remain signed; do not enable Windows test-signing mode.

Do not install a working copy on the host before the matching GitHub Release exists. Do not call an iteration complete until its repository changes and release are published. Preserve configuration and transfer files during updates.

## Initial release status

The private repository is `budgearoo2/Taildesk`. The initial project has been uploaded to `main`, including `.github/workflows/release.yml`. Confirm that GitHub Actions ran successfully and that a versioned release is visible before installing TailDesk on the host PC. If a push does not start a workflow, check the repository's Actions settings and enable GitHub Actions.
