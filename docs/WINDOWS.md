# Page Capture for Windows

The maintained Windows application is in `windows/`. It reuses the established Windows capture engine and adds persistent projects, a page workspace, local text recognition and searchable PDF output.

## Run the portable application

1. Download the Windows ZIP from this repository's releases or Windows-build artifacts.
2. Extract the whole ZIP into a folder. Keep `PageCapture.exe` and `_internal` together.
3. Open `PageCapture.exe`. Python installation and administrator privileges are not required for the portable bundle.

The preview package is unsigned. It does not install a certificate, change Windows security settings, add an updater or bypass OS warnings. The normal window can be closed to quit; projects are saved as work completes.

## Everyday use

- Choose **New project** or reopen a project from the sidebar.
- Use **Select area** to mark the visible page region, then **Capture** or F8 for each page.
- **Automatic** watches for a changed page to become stable; turn the pages yourself. It does not inject turns into another application.
- **Import** accepts images and PDFs, keeping their originals unchanged. PDF import has an optional page range.
- Double-click the preview or select **Open page** for Fit/100% zoom, scrolling and full-page review. **Text** shows saved recognized text.
- **Recognize text** uses the installed local Windows OCR engine. Choose **Searchable PDF** for export with the saved text layer.
- **Undo** and **Restore** preserve original images. **Stop** ends a job between pages; completed pages and OCR are retained.

Keyboard controls: F6 select area, F7 Undo, F8 Capture, F9 Export, F10 automatic observation, left/right arrows to review. Pin-on-top is an optional setting and is off by default.

## Where the files go

Projects live under the actual Windows **Documents / Page Capture Sessions** location, including redirected Documents folders. Default exports go to **Documents / Page Capture Exports**, or another path selected in the native save dialog. Each project contains `manifest.json`, original PNG pages and recovery history. Keep the entire project folder when moving it.

The original PNGs remain lossless. Compact PDF export uses JPEG for the PDF only. Exact duplicate pages are suppressed; modified originals or inconsistent project records are rejected before OCR/export. A project write lock prevents two processes from changing the same project simultaneously.

Legacy session folders can be brought into a new project with **Import** by selecting their PNG pages in reading order. The maintained Windows project format is versioned; do not assume the macOS port uses the same internal project format. PDFs and images are the portable exchange formats.

## Local OCR availability

OCR requires Windows 10/11 and an installed OCR language. The app shows the available languages and a readable reason when recognition is unavailable; capture/import and image-only export remain available. No model is silently downloaded.

The unpackaged Windows runtime was tested successfully on the development Windows device. Microsoft's documented desktop support for Windows.Media.Ocr concerns apps with package identity; the current portable preview is not an MSIX package. [Microsoft API documentation](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr). Packaged distribution and broader OS/language validation remain release decisions.

## Build from source

Install Python 3.12 from python.org, then from the repository root:

```powershell
& '.\windows\Build Windows.ps1'
```

Alternatively create an isolated `.windows-runtime`, install `windows/requirements-build.txt`, and run:

```powershell
.\.windows-runtime\Scripts\python.exe windows\build.py
```

The result is `dist/PageCapture/PageCapture.exe` and `dist/PageCapture-Windows-0.2.0.zip`. The source launcher at `windows/Launch Page Capture.cmd` prefers a built executable, then the isolated source runtime. It does not depend on a private absolute Python path.

## Automated verification

```powershell
.\.windows-runtime\Scripts\python.exe -m unittest discover -s windows\tests -p test_*.py
.\.windows-runtime\Scripts\python.exe windows\tests\ui_smoke.py
.\.windows-runtime\Scripts\python.exe windows\main.py --verify windows\.verification\sample
```

The hidden-widget smoke check mocks hotkeys, file dialogs and all display/input presentation. It uses synthetic data and does not capture the Desktop or send keyboard/mouse input. The native CLI check creates three known sample pages, verifies original preservation, duplicates, Undo/Restore and reopening, then checks searchable PDF text independently with pypdf when OCR is available. The same `--verify` command works on the packaged executable and writes a JSON receipt into the chosen verification directory.

No physical hotkey/selection verification is claimed by these automated checks. Actual protected applications, unusual monitor arrangements and other languages need their own validation. The app retains the previous engine's signed-coordinate and monitor-change checks.

## Licensing and source

The existing repository license remains in force. Windows delivery does not by itself change commercial rights. Exact dependency credits and legal texts are shipped with the portable bundle; see `windows/THIRD_PARTY_NOTICES.md`. There is no credential copying, cloud OCR, source-material upload or paid inference.
