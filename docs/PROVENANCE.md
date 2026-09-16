# Provenance

The initial Windows implementation was created for the repository owner as a local utility and later recovered from a preserved Windows C-drive backup.

Original recovered-file SHA-256 values before public-boundary sanitization:

- `Launch Page Capture.cmd`: `9b1af26059ea0990568c0a8b5bf5b8a8046c944f828211bef2587494f0c387ef`
- `README.txt`: `4aac065369931113694edd0c65d9d29fa8c6caeb516bdff488fafa4f7d16ecde`
- `page_capture.py`: `76177c2e06ccc5b0356238865a62c64d697b1362e76287b3d4aea75679920c6b`

The public launcher removes a private absolute path to a previously bundled Windows Python runtime. The functional Python source and legacy instructions were otherwise copied from the recovered artifact. Generated caches, capture sessions, demonstration material, and user documents are not part of the public source boundary.

The macOS implementation is a new platform port. Its behavior must be traced to explicit requirements rather than claimed as a byte-for-byte or API-level translation of Windows-specific code.
