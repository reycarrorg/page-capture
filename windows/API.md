# Windows implementation interfaces

`pagecapture/core.py` owns project storage, imports and PDF export. `ocr.py` supplies local Windows recognition; `app.py` and `ui_viewer.py` supply the workspace and page review. `legacy_engine.py` preserves the public Windows capture engine's Win32 selection, hotkey and stable-page primitives with its existing attribution and license.

## Core interface

All files stay local. Original images and imported PDFs remain unchanged. Every persisted page is hash verified before OCR and export. Save manifests and PDFs atomically. Undo retains recoverable originals. Hold a project write lock so two app instances cannot edit the same project concurrently. Jobs support cancellation between pages and leave completed progress recoverable.

`Page`: fields `id`, `filename`, `sha256`, `width`, `height`, `created_at`, `ocr` (dict or None).

`Session`:

- `Session.create(base_dir: Path, title: str = "Untitled capture") -> Session`
- `Session.load(directory: Path) -> Session`
- Properties `directory: Path`, `title: str`, `pages: list[Page]` (active ordered pages), `region: tuple[int,int,int,int] | None`, `page_paths: list[Path]`
- `page_path(page: Page) -> Path`
- `add_image(image: PIL.Image.Image, source_name: str | None = None) -> Page | None` (None for exact duplicate)
- `undo_last() -> Page | None`; `restore_last() -> Page | None`
- `set_region(region: tuple | None) -> None`; `close() -> None`

Module functions:

- `documents_directory() -> Path` (actual Windows redirected Documents folder)
- `list_sessions(base_dir: Path) -> list[dict]`: each has `directory` (str), `title`, `page_count`, `updated_at`; metadata only, no full image/hash scan for library listing.
- `import_files(session, paths: list[Path], cancel=None, progress=None, dpi=144, page_range=None) -> int`: images and rendered PDF pages, order preserved; optional one-based `(start,end)` range for one PDF; no silent skipped failures.
- `ocr_available() -> dict`: `available`, `languages` (language-code strings), `reason`. Windows.Media.Ocr via official PyWinRT packages. No downloaded model/API.
- `recognize_pages(session, language=None, cancel=None, progress=None) -> dict`: `processed`, `total`, `skipped`; retain word boxes for a searchable text layer. Cached OCR is valid only for the exact source hash.
- `export_pdf(session, output_path: Path, searchable=True, compact=False, cancel=None, progress=None) -> dict`: `path` (str), `pages` (int), `searchable_pages` (int). Does not implicitly run OCR. Missing OCR is reflected honestly in count; UI runs recognition first when requested and available.
- `JobCancelled` exception.

Progress callback: `progress(completed: int, total: int, message: str)` called in worker thread. Cancel: `threading.Event`; operations raise JobCancelled between pages. Module functions are synchronous wrappers suitable for Tk background threads.

## UI

Use the existing Tk/Pillow Win32 selection/hotkey/stable-page logic as the heavy lifter. Extend its PageCaptureApp rather than rebuilding negative-coordinate/mixed-monitor capture. Provide a polished full workspace with library sidebar, current page preview/review, clear select/capture/import/OCR/export actions, progress and Stop, keyboard navigation and recoverable Undo. Keep all Tk changes on main thread. Do not execute or transmit captured text. Persist selected project preference and support reopen. Clear stale capture selection on project reload; region selection still checks live monitor topology. An optional pin-on-top setting is a user choice, not default. Use clear capability labels; automatic capture observes pages, it does not silently inject page turns into other apps.

## Entry point

`app.launch(project_dir: Path | None = None) -> None` launches Tk and owns shutdown, job stop, hotkey stop and project lock release. `PageCaptureApp` subclasses the established engine and is importable for synthetic integration tests. `windows/main.py` supplies GUI and CLI verification entry points.

Dependencies are Pillow, ReportLab, pypdfium2, pypdf and official PyWinRT packages for local Windows OCR. `requirements.txt` and `requirements-build.txt` pin the verified distributions. Tests distinguish persistence and output integrity, hidden-widget integration, native OCR capability, and physical capture behavior.
