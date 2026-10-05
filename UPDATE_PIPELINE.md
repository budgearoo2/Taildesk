# TailDesk update and release pipeline

This file is the durable project instruction for future sessions and feature requests.

For every follow-up fix, feature, or additional idea:

1. Make the change in the project and update the version and documentation.
2. Review the full change and make sure the setup, security notes, and release notes match the implementation.
3. Bump `taildesk/__init__.py` to the next semantic version, then commit and push the complete change to the repository's `main` branch. Before building, the `.github/workflows/release.yml` workflow rewrites reachable commit emails to the GitHub actor's no-reply address and force-updates `main` and release tags using a lease on the triggering `main` commit. Then it runs dependency vulnerability review, Python unit tests and compilation, browser JavaScript syntax and behavior checks, and the Windows packaging build before publishing a release with that exact version.
4. Wait for the release workflow to finish successfully. It publishes a source ZIP and a self-contained `TailDesk-Setup-<version>.exe`, and creates the matching `v<version>` tag. Use that setup executable to install or update the host.
5. Only after the release is published, update the host from that release. Back up `%APPDATA%\TailDesk\settings.json` first. Run the published setup program, preserve the startup choice, settings, `%APPDATA%\TailDesk\audio-routing.json`, and `%USERPROFILE%\Downloads\TailDesk`, then start TailDesk and verify the host returns to its native resolution and original audio output after disconnect.

The setup executable bundles TailDesk and its Python runtime/packages; installed hosts do not need a separate Python or pip setup. Packaged app starts check GitHub's latest stable release when a fine-grained, repository-only, read-only token is configured in the local tray. The token is encrypted with Windows DPAPI in `%APPDATA%\TailDesk\github-token.dpapi`; never commit it, place it in release files, log it, or expose it through remote web settings. Auto-updates install only a setup asset whose SHA-256 matches authenticated GitHub release metadata. VB-CABLE is downloaded directly from VB-Audio only when the user requests it, verified against the pinned SHA-256, and installed using the vendor's unchanged installer after Windows administrator approval. Do not enable Windows test-signing mode.

The repository is public. Before each release, scan the staged source and packaged assets for credentials, settings files, Tailnet-specific addresses or names, and local user paths. Keep generated settings, credentials, private keys, and file transfers excluded from Git. If commit metadata contains a personal address the owner considers sensitive, discuss a history rewrite before changing refs or releases. A release commit must use a privacy-safe GitHub no-reply address after history has been rewritten.

Do not install a working copy on the host before the matching GitHub Release exists. Do not call an iteration complete until its repository changes and release are published. Preserve configuration and transfer files during updates.

## Initial release status

The repository is `budgearoo2/Taildesk`. The project is on `main`, including `.github/workflows/release.yml`. Confirm that GitHub Actions ran successfully and that a versioned release is visible before installing TailDesk on the host PC. If a push does not start a workflow, check the repository's Actions settings and enable GitHub Actions.
