# TailDesk update and release pipeline

This file is the durable project instruction for future sessions and feature requests.

For ordinary source, documentation, license, and workflow changes:

- Review and test the change, then commit using a GitHub no-reply email and push a branch for review. `verify.yml` runs dependency vulnerability review, compilation, Python regression tests (including workflow policy tests), and browser syntax and behavior checks on pull requests and pushes to `main`. It has read-only repository permissions and cannot publish.
- Do not bump the application version or install on the host solely for a repository maintenance change.

When an application release is explicitly intended:

1. Update `taildesk/__init__.py` to a new semantic version and update the changelog/documentation. Review the full change and security notes.
2. Merge the verified change to `main` using privacy-safe GitHub no-reply commit identities.
3. In Actions, run **Publish TailDesk** (`release.yml`) on `main`, entering the exact source version, for example `1.0.8`. Branches other than `main` cannot publish. The workflow reruns the read-only verification workflow on the selected commit before packaging.
4. Publication verifies reachable commit emails without rewriting history or moving existing tags. It fails closed on GitHub API errors, mismatched versions, existing releases, or existing version tags. Publication is serialized and never replaces assets. It builds the Windows setup, scans the allowlisted source archive for sensitive files and content, then creates a new release/tag targeting the exact verified commit. Wait for success and confirm the source ZIP and `TailDesk-Setup-<version>.exe` exist.
5. Only after the release is published, update the host from that release. Hosts running 1.0.6 or later can be updated remotely with **Settings > Check for updates**, which installs the published, digest-verified setup and preserves settings and transfers. Back up `%APPDATA%\TailDesk\settings.json` first. Run the published setup program, preserve the startup choice, settings, `%APPDATA%\TailDesk\audio-routing.json`, and `%USERPROFILE%\Downloads\TailDesk`, then start TailDesk and verify the host returns to its native resolution and original audio output after disconnect.

The setup executable bundles TailDesk and its Python runtime/packages; installed hosts do not need a separate Python or pip setup. Packaged app starts check GitHub's latest stable release when a fine-grained, repository-only, read-only token is configured in the local tray. The token is encrypted with Windows DPAPI in `%APPDATA%\TailDesk\github-token.dpapi`; never commit it, place it in release files, log it, or expose it through remote web settings. Auto-updates install only a setup asset whose SHA-256 matches authenticated GitHub release metadata. VB-CABLE is downloaded directly from VB-Audio only when the user requests it, verified against the pinned SHA-256, and installed using the vendor's unchanged installer after Windows administrator approval. Do not enable Windows test-signing mode.

The repository is public. Before each release, scan the staged source and packaged assets for credentials, settings files, Tailnet-specific addresses or names, and local user paths. Keep generated settings, credentials, private keys, and file transfers excluded from Git. If commit metadata contains a personal address the owner considers sensitive, discuss a history rewrite before changing refs or releases. Release history must use privacy-safe GitHub no-reply addresses. History cleanup is a separate, explicitly approved maintenance operation; CI never force-pushes branches or tags.

Do not install a working copy on the host before the matching GitHub Release exists. An ordinary repository change is complete after review and verification; an application release requires successful publication. Preserve configuration and transfer files during updates.

## Initial release status

The repository is `budgearoo2/Taildesk`. The project is on `main`, including `.github/workflows/release.yml`. Confirm that GitHub Actions ran successfully and that a versioned release is visible before installing TailDesk on the host PC. If a push does not start a workflow, check the repository's Actions settings and enable GitHub Actions.
