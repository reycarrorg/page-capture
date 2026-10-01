# Page Capture

Page Capture is a local utility for turning a fixed screen region into an ordered image-based PDF. Select a page area once, capture one sharp image per page, review or undo captures, and export the final sequence without uploading the source material.

## Project status

- `legacy/windows/` contains the recovered and public-boundary-sanitized Windows implementation.
- `macos/` will contain the native macOS port. It is under active development and must not yet be represented as finished.
- No packaged release is currently published.

The Windows implementation supports manual hotkey capture, best-effort automatic stable-page detection, exact-duplicate suppression, recoverable undo, lossless PNG preservation, and atomic lossless or compact PDF export.

## Intended use

Page Capture is intended for material the user is permitted to view and capture, such as personal documents, authorized course material, public webpages, and the user's own content.

It does not bypass DRM, screenshot protection, authentication, paywalls, access controls, or application capture restrictions. Users are responsible for copyright, contractual, privacy, and institutional rules governing captured material.

## Privacy

The design is local-first:

- no upload service;
- no telemetry;
- no remote model or API;
- no browser-profile, cookie, credential, or session access;
- original page images remain under user control;
- undo is recoverable rather than destructive.

See [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md).

## macOS port status

`macos/` now contains the first bounded native milestone: a Swift 6 `PageCaptureCore` package with unique local session directories, ordered lossless PNG records, exact decoded-pixel duplicate detection, recoverable undo, export preconditions, and one-image-per-page PDF export through a sibling temporary file and atomic install. A minimal SwiftUI executable can create/open a local session and display page, undo, and export state.

Region capture, Screen Recording permission flow, coordinate correctness, global shortcuts, thumbnails, automatic capture, and packaged-app verification remain pending. The executable explicitly does not claim capture is implemented; no screen content is captured by this milestone.

See [the macOS port specification](docs/MACOS_PORT_SPEC.md).

## Licensing

Page Capture is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE.md). Noncommercial use, modification, and redistribution are permitted under the license. Commercial use is not granted. This is not an OSI-approved open-source license.

## Maturity

The recovered Windows version was locally tested in its original environment, but the public repository has not yet reproduced every original Windows runtime check. The macOS port is pre-release work until its permission flow, capture accuracy, recovery behavior, PDF output, and packaged application are independently verified on macOS.
