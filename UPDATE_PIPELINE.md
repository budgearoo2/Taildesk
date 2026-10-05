# TailDesk update and release pipeline

This file is the durable project instruction for future sessions and feature requests.

For every follow-up fix, feature, or additional idea:

1. Make the change in the project and update the version and documentation.
2. Review the full change and make sure the setup, security notes, and release notes match the implementation.
3. Commit and push the complete change to the repository's `main` branch. The `.github/workflows/release.yml` workflow packages the source and publishes a new versioned GitHub Release from each push to `main`.
4. Wait for the release workflow to finish successfully. Download the versioned ZIP and use that artifact for installation.
5. Only after the release is published, update the host PC from that release. Back up `%APPDATA%\TailDesk\settings.json` first, install the released artifact, start TailDesk, and verify the host returns to its native resolution after a disconnect.

Do not install a working copy on the host before the matching GitHub Release exists. Do not call an iteration complete until its repository changes and release are published. Preserve configuration and transfer files during updates.

## Current deployment blocker

The GitHub connection is available, but no repository is currently listed for the connected account and the available GitHub actions in this session do not create a new repository. Once a private repository is created and its owner/name is known, upload this project to its `main` branch; the included workflow will create the first release.
