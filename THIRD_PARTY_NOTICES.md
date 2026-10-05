# Third-party notices

TailDesk includes the unmodified signed Windows virtual audio driver package from [VirtualDrivers/Virtual-Audio-Driver release 25.7.14](https://github.com/VirtualDrivers/Virtual-Audio-Driver/releases/tag/25.7.14).

- The upstream project is licensed under MIT; its license is in `taildesk/third_party/Virtual-Audio-Driver-LICENSE.txt`.
- The driver includes code derived from Microsoft's Windows Driver Kit Sysvad sample, licensed under Microsoft Public License (MS-PL); its required notice and license are in `taildesk/third_party/Virtual-Audio-Driver-THIRD_PARTY_NOTICES.md`.
- TailDesk's bundled ZIP SHA-256: `DD10560994DE65A7E587FB8B93C0D7E9838292D9C3566A0976C2786D727292BD`.
- The bundled `VirtualAudioDriver.sys` was verified locally with a valid Authenticode signature from SignPath Foundation.

TailDesk does not modify this driver package. Windows administrator approval is required to install it.
