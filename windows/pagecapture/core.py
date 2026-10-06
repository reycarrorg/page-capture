"""Local project storage, image import, and atomic PDF export for Page Capture.

Required Notice: Copyright 2026 Rolando Carreon.
Distributed under the repository's PolyForm Noncommercial License 1.0.0.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import ctypes
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import threading
import uuid

from PIL import Image, ImageOps


MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
_UNSET = object()
_PDF_FONT_LOCK = threading.Lock()


class JobCancelled(Exception):
    """A page job stopped; already committed pages remain recoverable."""


class ProjectLockedError(RuntimeError):
    """Another process has this project open for writing."""


@dataclass
class Page:
    id: str
    filename: str
    sha256: str
    width: int
    height: int
    created_at: str
    ocr: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _slug(value: str, default: str = "page") -> str:
    # A UUID suffix keeps even Windows reserved names away from device filenames.
    return re.sub(r"[^\w-]+", "-", value, flags=re.UNICODE).strip("-_ ")[:60] or default


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise JobCancelled("Stopped. Completed pages are saved in the project.")


def _progress(callback, completed: int, total: int, message: str) -> None:
    if callback is not None:
        callback(completed, total, message)


def _atomic_bytes(destination: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}-", suffix=".tmp", dir=destination.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_json(destination: Path, data: dict) -> None:
    payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    _atomic_bytes(destination, payload.encode("utf-8"))


def _region(value) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise ValueError("A capture region needs left, top, right, and bottom coordinates.")
    if any(type(coordinate) is not int for coordinate in value):
        raise ValueError("Capture region coordinates must be whole numbers.")
    left, top, right, bottom = value
    if right <= left or bottom <= top:
        raise ValueError("The capture region must have positive width and height.")
    return left, top, right, bottom


def _page_from_dict(value) -> Page:
    if not isinstance(value, dict):
        raise ValueError("The project contains an invalid page record.")
    try:
        page = Page(**{name: value[name] for name in (
            "id", "filename", "sha256", "width", "height", "created_at"
        )}, ocr=value.get("ocr"))
    except (KeyError, TypeError) as error:
        raise ValueError("The project contains an incomplete page record.") from error
    if not isinstance(page.id, str) or not re.fullmatch(r"[0-9a-f]{32}", page.id):
        raise ValueError("The project contains an invalid page identifier.")
    if not isinstance(page.filename, str) or "\\" in page.filename or ":" in page.filename:
        raise ValueError("The project contains an unsafe page filename.")
    relative = PurePosixPath(page.filename)
    if (relative.is_absolute() or len(relative.parts) != 2 or
            relative.parts[0] != "pages" or relative.suffix.lower() != ".png" or
            any(part in (".", "..") for part in relative.parts)):
        raise ValueError("Page files must be PNG images inside the project's pages folder.")
    if not isinstance(page.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", page.sha256):
        raise ValueError("The project contains an invalid image checksum.")
    if any(type(size) is not int or size <= 0 for size in (page.width, page.height)):
        raise ValueError("The project contains invalid image dimensions.")
    if not isinstance(page.created_at, str):
        raise ValueError("The project contains an invalid creation date.")
    if page.ocr is not None and not isinstance(page.ocr, dict):
        raise ValueError("The project contains an invalid OCR record.")
    return page


class _ProjectLock:
    """An OS file lock, released automatically if a process crashes."""

    def __init__(self, directory: Path):
        self.descriptor = os.open(directory / ".pagecapture.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.fstat(self.descriptor).st_size == 0:
                os.write(self.descriptor, b"\0")
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(self.descriptor)
            self.descriptor = None
            raise ProjectLockedError(
                "This project is already open in another Page Capture window. Close it there first."
            ) from error

    def close(self) -> None:
        if self.descriptor is not None:
            # Closing the handle releases the lock without deleting a shared lock file.
            os.close(self.descriptor)
            self.descriptor = None


class Session:
    def __init__(self, directory: Path, lock: _ProjectLock, title: str, created_at: str,
                 updated_at: str, pages: list[Page], archived: list[Page], region=None):
        self.directory = directory
        self._lock = lock
        self._mutex = threading.RLock()
        self._closed = False
        self._title = title
        self._created_at = created_at
        self._updated_at = updated_at
        self._pages = pages
        self._archived = archived
        self._region = region

    @classmethod
    def create(cls, base_dir: Path, title: str = "Untitled capture") -> Session:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Give the project a title.")
        base_dir = Path(base_dir).expanduser().resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        directory = base_dir / f"{datetime.now():%Y-%m-%d-%H%M%S}-{_slug(title, 'capture')}-{uuid.uuid4().hex[:8]}"
        directory.mkdir()
        (directory / "pages").mkdir()
        lock = _ProjectLock(directory)
        timestamp = _now()
        session = cls(directory, lock, title.strip(), timestamp, timestamp, [], [])
        try:
            session._commit()
        except Exception:
            session.close()
            raise
        return session

    @classmethod
    def load(cls, directory: Path) -> Session:
        directory = Path(directory).expanduser().resolve(strict=True)
        if not directory.is_dir():
            raise ValueError("Select a Page Capture project folder.")
        lock = _ProjectLock(directory)
        try:
            with (directory / MANIFEST_NAME).open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
                raise ValueError("This project format is not supported by this version of Page Capture.")
            if not isinstance(data.get("title"), str) or not data["title"].strip():
                raise ValueError("The project title is missing.")
            if not all(isinstance(data.get(field), str) for field in ("created_at", "updated_at")):
                raise ValueError("The project dates are invalid.")
            if not all(isinstance(data.get(field), list) for field in ("pages", "archived")):
                raise ValueError("The project's page history is invalid.")
            pages = [_page_from_dict(value) for value in data["pages"]]
            archived = [_page_from_dict(value) for value in data["archived"]]
            all_pages = pages + archived
            if (len({page.id for page in all_pages}) != len(all_pages) or
                    len({page.filename.casefold() for page in all_pages}) != len(all_pages)):
                raise ValueError("The project has conflicting page records.")
            session = cls(directory, lock, data["title"], data["created_at"], data["updated_at"],
                          pages, archived, _region(data.get("region")))
            for page in all_pages:
                session.page_path(page)  # Check containment; image verification is deferred to use.
            return session
        except Exception:
            lock.close()
            raise

    @property
    def title(self) -> str:
        return self._title

    @property
    def pages(self) -> list[Page]:
        with self._mutex:
            return list(self._pages)

    @property
    def region(self) -> tuple[int, int, int, int] | None:
        return self._region

    @property
    def page_paths(self) -> list[Path]:
        return [self.page_path(page) for page in self.pages]

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("This Page Capture project is closed.")

    def page_path(self, page: Page) -> Path:
        # Validate fields without deep-copying a potentially large OCR word cache.
        _page_from_dict(vars(page))
        path = self.directory.joinpath(*PurePosixPath(page.filename).parts).resolve()
        if not path.is_relative_to(self.directory) or path.parent != self.directory / "pages":
            raise ValueError("A page filename points outside the project's pages folder.")
        return path

    def _read_page(self, page: Page) -> bytes:
        with self._mutex:
            self._assert_open()
            path = self.page_path(page)
            try:
                data = path.read_bytes()
            except OSError as error:
                raise OSError(f"The original image for page {page.id[:8]} cannot be read: {path.name}") from error
            if hashlib.sha256(data).hexdigest() != page.sha256:
                raise ValueError(f"Checksum mismatch for {path.name}. The original image changed; restore it before continuing.")
            return data

    def _commit(self, *, pages=_UNSET, archived=_UNSET, region=_UNSET) -> None:
        self._assert_open()
        proposed_pages = self._pages if pages is _UNSET else pages
        proposed_archived = self._archived if archived is _UNSET else archived
        proposed_region = self._region if region is _UNSET else region
        timestamp = _now()
        data = {
            "version": MANIFEST_VERSION, "title": self._title,
            "created_at": self._created_at, "updated_at": timestamp,
            "region": proposed_region,
            "pages": [asdict(page) for page in proposed_pages],
            "archived": [asdict(page) for page in proposed_archived],
        }
        _atomic_json(self.directory / MANIFEST_NAME, data)
        self._pages, self._archived, self._region = proposed_pages, proposed_archived, proposed_region
        self._updated_at = timestamp

    def add_image(self, image: Image.Image, source_name: str | None = None) -> Page | None:
        with self._mutex:
            self._assert_open()
            if not isinstance(image, Image.Image) or image.width <= 0 or image.height <= 0:
                raise ValueError("A page must be a nonempty image.")
            # Normalize representation only; no resize, crop, or JPEG degradation.
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            normalized = image.convert("RGBA" if has_alpha else "RGB")
            try:
                buffer = BytesIO()
                normalized.save(buffer, format="PNG", compress_level=6)
                payload = buffer.getvalue()
                checksum = hashlib.sha256(payload).hexdigest()
                for existing in self._pages:
                    if existing.sha256 == checksum:
                        self._read_page(existing)
                        return None
                identifier = uuid.uuid4().hex
                stem = _slug(Path(source_name).stem) if source_name else "page"
                page = Page(identifier, f"pages/{stem}-{identifier}.png", checksum,
                            normalized.width, normalized.height, _now())
                path = self.page_path(page)
                _atomic_bytes(path, payload)
                # A manifest failure leaves this image as a recoverable orphan,
                # while both the previous manifest and in-memory ordering stay intact.
                self._read_page(page)
                self._commit(pages=self._pages + [page])
                return page
            finally:
                normalized.close()

    def undo_last(self) -> Page | None:
        with self._mutex:
            self._assert_open()
            if not self._pages:
                return None
            page = self._pages[-1]
            self._commit(pages=self._pages[:-1], archived=self._archived + [page])
            return page

    def restore_last(self) -> Page | None:
        with self._mutex:
            self._assert_open()
            if not self._archived:
                return None
            page = self._archived[-1]
            self._read_page(page)
            self._commit(pages=self._pages + [page], archived=self._archived[:-1])
            return page

    def set_region(self, region: tuple | None) -> None:
        with self._mutex:
            self._assert_open()
            self._commit(region=_region(region))

    def _store_ocr(self, page: Page, result: dict) -> None:
        with self._mutex:
            self._assert_open()
            for index, current in enumerate(self._pages):
                if current.id == page.id and current.sha256 == page.sha256:
                    self._read_page(current)
                    updated = self._pages.copy()
                    updated[index] = replace(current, ocr=result)
                    self._commit(pages=updated)
                    return
            raise ValueError("This page was removed while recognition was running. Completed OCR is still saved.")

    def close(self) -> None:
        with self._mutex:
            if not self._closed:
                self._closed = True
                self._lock.close()

    def __enter__(self) -> Session:
        self._assert_open()
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def documents_directory() -> Path:
    """Ask Windows for Documents, including OneDrive/domain folder redirection."""
    if os.name != "nt":
        return Path.home() / "Documents"

    class GUID(ctypes.Structure):
        _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                    ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    folder_id = GUID.from_buffer_copy(uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le)
    path_pointer = ctypes.c_void_p()
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32,
                                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    try:
        result = shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None,
                                           ctypes.byref(path_pointer))
        if result < 0 or not path_pointer.value:
            raise OSError(f"Windows could not locate Documents (0x{result & 0xFFFFFFFF:08X}).")
        return Path(ctypes.wstring_at(path_pointer))
    finally:
        if path_pointer.value:
            ole.CoTaskMemFree(path_pointer)


def list_sessions(base_dir: Path) -> list[dict]:
    base_dir = Path(base_dir)
    if not base_dir.exists():
        return []
    sessions = []
    for directory in base_dir.iterdir():
        manifest = directory / MANIFEST_NAME
        if not directory.is_dir() or not manifest.is_file():
            continue
        try:
            with manifest.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            if (not isinstance(data, dict) or not isinstance(data.get("title"), str) or
                    not isinstance(data.get("pages"), list) or not isinstance(data.get("updated_at"), str)):
                raise ValueError("Invalid project metadata")
            entry = {"directory": str(directory.resolve()), "title": data["title"],
                     "page_count": len(data["pages"]), "updated_at": data["updated_at"]}
        except (OSError, ValueError, UnicodeError):
            # Keep damaged projects visible in the library so users can recover them.
            entry = {"directory": str(directory.resolve()), "title": directory.name,
                     "page_count": 0, "updated_at": "", "error": "Project metadata could not be read."}
        sessions.append(entry)
    return sorted(sessions, key=lambda entry: entry["updated_at"], reverse=True)


def import_files(session, paths: list[Path], cancel=None, progress=None, dpi=144,
                 page_range=None) -> int:
    """Import in supplied order. Repeating a stopped import skips exact duplicates."""
    session._assert_open()
    if isinstance(dpi, bool) or not isinstance(dpi, (int, float)) or not math.isfinite(dpi) or dpi <= 0:
        raise ValueError("Import resolution must be a positive number.")
    paths = [Path(path).expanduser().resolve(strict=True) for path in paths]
    if page_range is not None and (len(paths) != 1 or paths[0].suffix.lower() != ".pdf"):
        raise ValueError("A page range can be used when importing one PDF.")
    plan = []
    pdfium = None
    for path in paths:
        _check_cancel(cancel)
        try:
            if path.suffix.lower() == ".pdf":
                if pdfium is None:
                    import pypdfium2 as pdfium
                with pdfium.PdfDocument(str(path)) as document:
                    count = len(document)
                start, end = 1, count
                if page_range is not None:
                    if (not isinstance(page_range, (tuple, list)) or len(page_range) != 2 or
                            any(type(item) is not int for item in page_range)):
                        raise ValueError("Use a one-based start and end page number.")
                    start, end = page_range
                    if start < 1 or end < start or end > count:
                        raise ValueError(f"The page range must be inside this PDF's {count} pages.")
                if count == 0:
                    raise ValueError("This PDF contains no pages.")
                plan.append((path, "pdf", start - 1, end))
            else:
                with Image.open(path) as image:
                    count = getattr(image, "n_frames", 1)
                plan.append((path, "image", 0, count))
        except Exception as error:
            raise ValueError(f"Could not import {path.name}: {error}") from error
    total = sum(end - start for _path, _kind, start, end in plan)
    _progress(progress, 0, total, "Preparing import")
    completed = added = 0
    for path, kind, start, end in plan:
        try:
            if kind == "pdf":
                with pdfium.PdfDocument(str(path)) as document:
                    for index in range(start, end):
                        _check_cancel(cancel)
                        pdf_page = document.get_page(index)
                        try:
                            bitmap = pdf_page.render(scale=dpi / 72)
                            try:
                                image = bitmap.to_pil().copy()
                            finally:
                                bitmap.close()
                        finally:
                            pdf_page.close()
                        try:
                            if session.add_image(image, f"{path.stem}-page-{index + 1}") is not None:
                                added += 1
                        finally:
                            image.close()
                        completed += 1
                        _progress(progress, completed, total, f"Imported {path.name}, page {index + 1}")
            else:
                with Image.open(path) as source:
                    for index in range(start, end):
                        _check_cancel(cancel)
                        source.seek(index)
                        image = ImageOps.exif_transpose(source)
                        try:
                            name = path.name if end == 1 else f"{path.stem}-page-{index + 1}"
                            if session.add_image(image, name) is not None:
                                added += 1
                        finally:
                            image.close()
                        completed += 1
                        _progress(progress, completed, total, f"Imported {path.name}" +
                                  (f", page {index + 1}" if end > 1 else ""))
        except JobCancelled:
            raise
        except Exception as error:
            raise ValueError(f"Could not finish importing {path.name}: {error}. Completed pages are saved.") from error
    _check_cancel(cancel)
    return added


def ocr_available() -> dict:
    from .ocr import ocr_available as available
    return available()


def recognize_pages(session, language=None, cancel=None, progress=None) -> dict:
    from .ocr import recognize_pages as recognize
    return recognize(session, language=language, cancel=cancel, progress=progress)


def _ocr_matches(page: Page, language=None) -> bool:
    data = page.ocr
    if (not isinstance(data, dict) or data.get("version") != 1 or
            data.get("engine") != "Windows.Media.Ocr" or data.get("source_sha256") != page.sha256 or
            data.get("width") != page.width or data.get("height") != page.height or
            not isinstance(data.get("language"), str) or not isinstance(data.get("text"), str) or
            not isinstance(data.get("lines"), list)):
        return False
    if language is not None and data["language"].casefold() != str(language).casefold():
        return False
    for line in data["lines"]:
        if not isinstance(line, dict) or not isinstance(line.get("words"), list):
            return False
        for word in line["words"]:
            if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                return False
            boxes = [word.get(key) for key in ("x", "y", "width", "height")]
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
                   not math.isfinite(value) for value in boxes):
                return False
            x, y, width, height = boxes
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > page.width + 1 or y + height > page.height + 1:
                return False
    return True


def _search_font() -> str:
    from reportlab import __file__ as reportlab_file
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font_name = "PageCaptureOCR"
    with _PDF_FONT_LOCK:
        if font_name not in pdfmetrics.getRegisteredFontNames():
            candidates = []
            if os.name == "nt":
                candidates.append(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf")
            candidates.append(Path(reportlab_file).parent / "fonts" / "Vera.ttf")
            font_path = next((path for path in candidates if path.is_file()), None)
            if font_path is None:
                raise RuntimeError("The local PDF text font is missing. Repair the Page Capture installation.")
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    return font_name


def _draw_search_text(document, page: Page, scale: float) -> bool:
    if not _ocr_matches(page):
        return False
    words = [word for line in page.ocr["lines"] for word in line["words"] if word["text"].strip()]
    if not words:
        return False
    from reportlab.pdfbase import pdfmetrics
    font_name = _search_font()
    ascent = pdfmetrics.getAscent(font_name) / 1000
    descent = pdfmetrics.getDescent(font_name) / 1000
    for word in words:
        font_size = word["height"] * scale / (ascent - descent)
        baseline = (page.height - word["y"] - word["height"]) * scale - descent * font_size
        text = document.beginText(word["x"] * scale, baseline)
        text.setTextRenderMode(3)  # PDF invisible text: page imagery is untouched.
        text.setFont(font_name, font_size)
        natural_width = pdfmetrics.stringWidth(word["text"], font_name, font_size)
        if natural_width > 0:
            text.setHorizScale(word["width"] * scale / natural_width * 100)
        text.textOut(word["text"] + " ")
        document.drawText(text)
    return True


def export_pdf(session, output_path: Path, searchable=True, compact=False, cancel=None,
               progress=None) -> dict:
    """Build and validate a complete PDF before replacing the selected destination."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader
    session._assert_open()
    pages = session.pages
    if not pages:
        raise ValueError("Capture or import a page before exporting a PDF.")
    _check_cancel(cancel)
    output_path = Path(output_path).expanduser().resolve()
    protected = {session.directory / MANIFEST_NAME, session.directory / ".pagecapture.lock"}
    protected.update(session.page_path(page) for page in pages + session._archived)
    if output_path in protected or output_path.is_relative_to(session.directory / "pages"):
        raise ValueError("Choose a PDF destination that does not replace a project file.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{output_path.stem}-", suffix=".building.pdf",
                                             dir=output_path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    searchable_pages = 0
    try:
        document = canvas.Canvas(str(temporary_path), pagesize=(1, 1), pageCompression=1)
        document.setTitle(session.title)
        document.setAuthor("Page Capture")
        document.setSubject("Pages saved locally in reading order")
        _progress(progress, 0, len(pages), "Preparing PDF")
        for completed, page in enumerate(pages, 1):
            _check_cancel(cancel)
            payload = session._read_page(page)
            with Image.open(BytesIO(payload)) as image:
                image.load()
                if image.size != (page.width, page.height):
                    raise ValueError(f"Image dimensions changed for {page.filename}.")
                # Match the capture engine's established effective resolution of 144 DPI.
                scale = 0.5
                page_width, page_height = page.width * scale, page.height * scale
                document.setPageSize((page_width, page_height))
                if compact:
                    rgb = Image.new("RGB", image.size, "white")
                    if "A" in image.getbands():
                        rgb.paste(image, mask=image.getchannel("A"))
                    else:
                        rgb.paste(image.convert("RGB"))
                    try:
                        buffer = BytesIO()
                        rgb.save(buffer, format="JPEG", quality=92, optimize=True, subsampling=0)
                        buffer.seek(0)
                        reader = ImageReader(buffer)
                    finally:
                        rgb.close()
                else:
                    reader = ImageReader(image)
                document.drawImage(reader, 0, 0, width=page_width, height=page_height, mask="auto")
                if searchable and _draw_search_text(document, page, scale):
                    searchable_pages += 1
                document.showPage()
            _progress(progress, completed, len(pages), f"Prepared page {completed} of {len(pages)}")
        _check_cancel(cancel)
        document.save()
        # Windows' fsync requires a descriptor opened for writing.
        with temporary_path.open("rb+") as stream:
            if len(PdfReader(stream, strict=True).pages) != len(pages):
                raise ValueError("The completed PDF did not contain every page.")
            os.fsync(stream.fileno())
        _check_cancel(cancel)
        os.replace(temporary_path, output_path)
        return {"path": str(output_path), "pages": len(pages), "searchable_pages": searchable_pages}
    finally:
        temporary_path.unlink(missing_ok=True)
