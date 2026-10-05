# TailDesk update and release pipeline

This file is the durable project instruction for future sessions and feature requests.

For every follow-up fix, feature, or additional idea:

1. Make the change in the project and update the version and documentation.
2. Review the full change and make sure the setup, security notes, and release notes match the implementation.
3. Commit and push the complete change to the repository's `main` branch. The `.github/workflows/release.yml` workflow packages the source and publishes a new versioned GitHub Release from each push to `main`.
4. Wait for the release workflow to finish successfully. Download the versioned ZIP and use that artifact for installation.
5. Only after the release is published, update the host PC from that release. Back up `%APPDATA%\TailDesk\settings.json` first, install the released artifact, start TailDesk, and verify the host returns to its native resolution after a disconnect.

Do not install a working copy on the host before the matching GitHub Release exists. Do not call an iteration complete until its repository changes and release are published. Preserve configuration and transfer files during updates.

## Initial release status

The private repository is `budgearoo2/Taildesk`. The initial project has been uploaded to `main`, including `.github/workflows/release.yml`. Confirm that GitHub Actions ran successfully and that a versioned release is visible before installing TailDesk on the host PC. If a push does not start a workflow, check the repository's Actions settings and enable GitHub Actions.