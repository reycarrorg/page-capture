"""Optional, entirely local Windows.Media.Ocr integration through PyWinRT.

Required Notice: Copyright 2026 Rolando Carreon.
Distributed under the repository's PolyForm Noncommercial License 1.0.0.
No model downloads, remote services, or captured-content execution.
"""
from __future__ import annotations

from io import BytesIO
import os

from PIL import Image

from .core import _check_cancel, _now, _ocr_matches, _progress


def _bindings():
    if os.name != "nt":
        raise RuntimeError("Local Windows OCR requires Windows 10 or later.")
    # Foundation projections register the async and collection types used by OCR.
    import winrt.windows.foundation  # noqa: F401
    import winrt.windows.foundation.collections  # noqa: F401
    # SoftwareBitmap.copy_from_buffer dynamically uses this IBuffer projection.
    import winrt.windows.storage.streams  # noqa: F401
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    return Language, BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap, OcrEngine


def ocr_available() -> dict:
    """Report actual installed Windows recognizers, with a readable failure reason."""
    try:
        Language, _alpha, _pixel, _bitmap, OcrEngine = _bindings()
        languages = [item.language_tag for item in OcrEngine.available_recognizer_languages]
        if not languages:
            return {"available": False, "languages": [],
                    "reason": "No Windows OCR language is installed. Add OCR for a language in Windows Settings."}
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            engine = OcrEngine.try_create_from_language(Language(languages[0]))
        if engine is None:
            return {"available": False, "languages": languages,
                    "reason": "Windows could not create a local OCR recognizer for an installed language."}
        return {"available": True, "languages": languages, "reason": ""}
    except ImportError:
        return {"available": False, "languages": [],
                "reason": "The optional local Windows OCR components are not installed. Repair the Page Capture installation to enable OCR."}
    except Exception as error:
        return {"available": False, "languages": [],
                "reason": f"Local Windows OCR is unavailable: {error}"}


class _Recognizer:
    def __init__(self, language=None):
        try:
            self.Language, self.Alpha, self.Pixel, self.Bitmap, self.Engine = _bindings()
        except ImportError as error:
            raise RuntimeError(ocr_available()["reason"]) from error
        if language is not None:
            if not isinstance(language, str) or not language.strip():
                raise ValueError("Select an installed Windows OCR language.")
            requested = self.Language(language)
            if not self.Engine.is_language_supported(requested):
                raise ValueError(f"Windows OCR for {language} is not installed on this computer.")
            self.engine = self.Engine.try_create_from_language(requested)
        else:
            self.engine = self.Engine.try_create_from_user_profile_languages()
            if self.engine is None:
                languages = self.Engine.available_recognizer_languages
                self.engine = self.Engine.try_create_from_language(languages[0]) if languages else None
        if self.engine is None:
            raise RuntimeError("Windows could not create a local OCR recognizer. Check the installed Windows OCR languages.")
        self.language = self.engine.recognizer_language.language_tag

    def recognize(self, image: Image.Image) -> dict:
        original_width, original_height = image.size
        maximum = self.Engine.max_image_dimension
        if maximum <= 0:
            raise RuntimeError("Windows reported an invalid maximum OCR image size.")
        # Composite transparent imports onto paper without modifying their originals.
        rgb = Image.new("RGB", image.size, "white")
        if "A" in image.getbands():
            rgb.paste(image, mask=image.getchannel("A"))
        else:
            rgb.paste(image.convert("RGB"))
        working = rgb
        try:
            ratio = min(1.0, maximum / max(original_width, original_height))
            if ratio < 1:
                working = rgb.resize((max(1, int(original_width * ratio)),
                                      max(1, int(original_height * ratio))), Image.Resampling.LANCZOS)
            width, height = working.size
            # PyWinRT 3.2 projects IBuffer input as Python's buffer protocol.
            # BGRA8 with ignored alpha is the documented OCR SoftwareBitmap format.
            bgra = working.convert("RGBA")
            try:
                pixels = bgra.tobytes("raw", "BGRA")
            finally:
                bgra.close()
            with self.Bitmap(self.Pixel.BGRA8, width, height, self.Alpha.IGNORE) as bitmap:
                bitmap.copy_from_buffer(pixels)
                # Runs on the UI's MTA worker. PyWinRT .get() raises if a caller
                # instead supplies a single-threaded COM apartment.
                result = self.engine.recognize_async(bitmap).get()
            scale_x, scale_y = original_width / width, original_height / height
            lines = []
            for line in result.lines:
                words = []
                for word in line.words:
                    box = word.bounding_rect
                    left = min(original_width, max(0.0, float(box.x) * scale_x))
                    top = min(original_height, max(0.0, float(box.y) * scale_y))
                    right = min(original_width, max(left, float(box.x + box.width) * scale_x))
                    bottom = min(original_height, max(top, float(box.y + box.height) * scale_y))
                    if right > left and bottom > top:
                        words.append({"text": word.text, "x": left, "y": top,
                                      "width": right - left, "height": bottom - top})
                lines.append({"text": line.text, "words": words})
            angle = result.text_angle
            return {"text": result.text, "lines": lines,
                    "text_angle": None if angle is None else float(angle),
                    "recognition_width": width, "recognition_height": height}
        finally:
            if working is not rgb:
                working.close()
            rgb.close()


def recognize_pages(session, language=None, cancel=None, progress=None) -> dict:
    """Recognize one verified source at a time and atomically cache each result."""
    session._assert_open()
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise ValueError("Select an installed Windows OCR language.")
    pages = session.pages
    total, processed, skipped = len(pages), 0, 0
    _check_cancel(cancel)
    _progress(progress, 0, total, "Preparing local OCR")
    recognizer = None
    for completed, page in enumerate(pages, 1):
        _check_cancel(cancel)
        # Even cached OCR requires a fresh checksum check of the real source file.
        payload = session._read_page(page)
        if _ocr_matches(page, language):
            skipped += 1
            _progress(progress, completed, total, f"Reused local OCR for page {completed}")
            continue
        if recognizer is None:
            recognizer = _Recognizer(language)
        with Image.open(BytesIO(payload)) as image:
            image.load()
            if image.size != (page.width, page.height):
                raise ValueError(f"Image dimensions changed for {page.filename}.")
            result = recognizer.recognize(image)
        cache = dict(result, version=1, source_sha256=page.sha256, engine="Windows.Media.Ocr",
                     language=recognizer.language, width=page.width, height=page.height,
                     recognized_at=_now())
        # Reject malformed native output instead of saving a cache that cannot export.
        from dataclasses import replace
        if not _ocr_matches(replace(page, ocr=cache), language):
            raise ValueError(f"Windows returned invalid OCR positions for page {completed}.")
        session._store_ocr(page, cache)
        processed += 1
        _progress(progress, completed, total, f"Recognized page {completed} of {total} locally")
    _check_cancel(cancel)
    return {"processed": processed, "total": total, "skipped": skipped}
