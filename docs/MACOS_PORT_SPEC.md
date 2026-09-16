# macOS port specification

## Goal

Create a native, local-only macOS application that reproduces Page Capture's user-visible workflow without Windows-specific APIs or a Python runtime dependency.

## Required workflow

1. Explain Screen Recording permission and open the normal macOS permission flow when needed.
2. Let the user draw a capture rectangle over one display. Multi-display support may follow after single-display coordinate correctness is proven.
3. Show the selected region and a page counter without covering captured content.
4. Support a deliberate manual capture action and configurable keyboard shortcut.
5. Provide best-effort automatic capture only after manual capture is reliable. Automatic mode waits for change followed by multiple stable observations and stores the newest stable frame.
6. Skip exact duplicates while preserving small text or layout changes.
7. Display ordered thumbnails or another clear review surface.
8. Undo by moving the last page to a recoverable `Undone` location.
9. Preserve original lossless page images in a unique session directory.
10. Export an ordered image-based PDF in lossless-text and compact-photo modes.
11. Build the PDF to a temporary sibling and atomically replace the destination only after success.
12. Require a successful export of the current page set before reset is enabled.

## Native implementation direction

- Swift 6 and SwiftUI/AppKit where appropriate.
- ScreenCaptureKit for modern screen capture and normal macOS Screen Recording consent.
- Core Graphics/ImageIO for image processing and exact-duplicate checks.
- PDFKit or Core Graphics for deterministic one-image-per-page PDF generation.
- A narrow, reviewable global-shortcut implementation; do not request Accessibility permission merely for convenience if a supported hotkey API can avoid it.
- App Sandbox and signing decisions remain separate packaging gates; do not claim them until verified.

## Acceptance gates

- Unit tests for session ordering, duplicate decisions, small-change preservation, undo state, unique session names, and atomic export behavior.
- Synthetic image fixtures only.
- Coordinate tests covering Retina scale and at least one non-Retina or simulated scale case.
- Permission-denied and permission-revoked behavior that preserves existing session data.
- Multi-display behavior labeled unsupported until verified, rather than guessed.
- No network entitlements, network code, telemetry, or remote dependencies.
- Source and packaged application scans contain no credentials, private paths, captured pages, or signing secrets.
- A short real-device canary with beginning/middle/end PDF inspection before calling the port usable.

## Non-goals

- DRM, screenshot-protection, paywall, authentication, or access-control bypass.
- Automated page turning or synthetic input into third-party readers.
- OCR in the initial port.
- Cloud storage, collaboration, or remote analysis.
- Capturing entire applications or unrelated displays when a selected region is sufficient.
