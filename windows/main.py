"""GUI entry point and bounded developer acceptance commands."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from pagecapture import __version__


def verify(destination: Path) -> dict:
    from PIL import Image, ImageDraw, ImageFont
    from pypdf import PdfReader
    from pagecapture.core import Session, export_pdf, import_files, ocr_available, recognize_pages
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    # Exercise the bundled Tcl/Tk and image bridge without showing a window,
    # registering hotkeys, capturing the Desktop or injecting any input.
    import tkinter as tk
    from tkinter import ttk
    from PIL import ImageTk
    hidden_root = tk.Tk()
    hidden_root.withdraw()
    try:
        style = ttk.Style(hidden_root)
        bridge_image = ImageTk.PhotoImage(Image.new("RGB", (12, 12), "white"), master=hidden_root)
        label = ttk.Label(hidden_root, image=bridge_image)
        label.pack()
        hidden_root.update_idletasks()
        gui_runtime = {"tk": hidden_root.tk.call("info", "patchlevel"), "theme": style.theme_use(),
                       "image_bridge": bridge_image.width() == 12, "hidden": hidden_root.state() == "withdrawn"}
        assert gui_runtime["image_bridge"] and gui_runtime["hidden"]
    finally:
        hidden_root.destroy()
    session = Session.create(destination / "projects", "Windows verification sample")
    sources = []
    fonts = Path(__import__("os").environ.get("WINDIR", "C:/Windows")) / "Fonts"
    font = ImageFont.truetype(str(fonts / "arial.ttf"), 62)
    smaller = ImageFont.truetype(str(fonts / "arial.ttf"), 36)
    try:
        for number in range(1, 4):
            image = Image.new("RGB", (1400, 1800), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((80, 85, 1320, 110), fill="#187153")
            draw.text((90, 180), "PAGE CAPTURE WINDOWS", font=font, fill="#15251e")
            draw.text((90, 290), f"REFERENCE PAGE {number}", font=font, fill="black")
            draw.text((90, 430), "Synthetic sample document", font=smaller, fill="#454f49")
            draw.text((90, 505), "Recovery code CANARY42", font=smaller, fill="black")
            draw.text((90, 580), "Original images stay unchanged.", font=smaller, fill="black")
            draw.rounded_rectangle((90, 730, 1310, 1360), radius=20, outline="#cdd7d0", width=3)
            draw.text((140, 800), "Capture. Review. Search. Export.", font=smaller, fill="#187153")
            path = destination / f"sample-page-{number}.png"
            image.save(path)
            sources.append(path)
        source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        assert import_files(session, sources) == 3
        with Image.open(sources[0]) as duplicate:
            assert session.add_image(duplicate) is None
        assert len(session.pages) == 3
        assert session.undo_last() is not None and len(session.pages) == 2
        assert session.restore_last() is not None and len(session.pages) == 3
        capability = ocr_available()
        if capability["available"]:
            # Tk/OLE may initialize the GUI thread as STA. Native OCR's blocking
            # API belongs to an MTA worker, just as it does in the workspace.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as pool:
                ocr_result = pool.submit(recognize_pages, session, language="en-US").result(timeout=120)
        else:
            ocr_result = None
        pdf = destination / "windows-page-capture-sample.pdf"
        result = export_pdf(session, pdf, searchable=capability["available"])
        reader = PdfReader(pdf)
        assert len(reader.pages) == 3
        text = [page.extract_text() or "" for page in reader.pages]
        if capability["available"]:
            assert all("CANARY42" in value.replace(" ", "") for value in text)
        assert source_hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        project = str(session.directory)
        session.close()
        session = Session.load(Path(project))
        assert len(session.pages) == 3
        receipt = {"passed": True, "version": __version__, "verified_utc": datetime.now(timezone.utc).isoformat(),
            "synthetic_inputs_only": True, "hidden_gui_runtime": gui_runtime,
            "project": project, "pdf": str(pdf), "page_count": 3,
            "ocr_capability": capability, "ocr_result": ocr_result, "export": result,
            "searchable_text_verified": capability["available"], "source_hashes": source_hashes,
            "originals_unchanged": True, "duplicate_suppression": True, "undo_restore": True,
            "reopen_verified": True, "sample_text": text}
        (destination / "verification.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return receipt
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Page Capture for Windows")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--project", type=Path, help="Reopen an existing local project")
    parser.add_argument("--diagnose", action="store_true", help="Print local runtime capabilities; no capture")
    parser.add_argument("--verify", type=Path, metavar="DIRECTORY", help="Create synthetic acceptance inputs and PDF in this directory")
    arguments = parser.parse_args()
    if arguments.diagnose:
        from pagecapture.core import documents_directory, ocr_available
        print(json.dumps({"version": __version__, "platform": sys.platform, "documents": str(documents_directory()), "ocr": ocr_available()}, indent=2))
        return 0
    if arguments.verify:
        print(json.dumps(verify(arguments.verify), indent=2))
        return 0
    if sys.platform != "win32":
        parser.error("The Windows app requires Windows 10 or 11. See the separate macOS implementation.")
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PageCapture.Windows")
    from pagecapture.app import launch
    launch(arguments.project)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        verification = "--verify" in sys.argv
        diagnostic = "--diagnose" in sys.argv
        if verification:
            try:
                destination = Path(sys.argv[sys.argv.index("--verify") + 1]).resolve()
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "verification-error.json").write_text(json.dumps({"passed": False, "error_type": type(error).__name__, "error": str(error)}, indent=2), encoding="utf-8")
            except (OSError, ValueError, IndexError):
                pass
        if sys.platform == "win32" and not verification and not diagnostic:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"Page Capture could not start.\n\n{error}", "Page Capture", 0x10)
        if sys.stderr is not None:
            print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
