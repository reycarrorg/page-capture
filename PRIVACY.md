# Privacy

Page Capture is designed to operate entirely on the user's device.

## Data handled

The application may process pixels inside a user-selected screen rectangle, the resulting page images, local session metadata, and a user-selected PDF destination. Screen content can be sensitive even when the application itself does not interpret the text.

## Product boundaries

- Capture begins only after a deliberate user action.
- The user selects the region and controls when a session starts and stops.
- Images and PDFs stay in local user-selected locations.
- No telemetry, analytics, remote logging, cloud synchronization, or model API is included.
- No credentials, cookies, browser profiles, authorization headers, clipboard history, or keystroke content are requested.
- The macOS port must explain and request Screen Recording permission through the normal operating-system flow.

Do not include captured pages, personal documents, session images, or generated PDFs in bug reports or public repositories. Reproduce issues with synthetic fixtures whenever possible.
