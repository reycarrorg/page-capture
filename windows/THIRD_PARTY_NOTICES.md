# Windows dependency notices

Required Notice: Copyright 2026 Rolando Carreon.

Page Capture's authored application code follows the repository's PolyForm Noncommercial License 1.0.0. The dependencies below retain their respective terms. This document is attribution, not a replacement for those terms.

| Component | Role | License / source |
|---|---|---|
| CPython | Bundled Windows runtime | Python Software Foundation License, [python.org](https://www.python.org/) |
| Tcl / Tk | Native desktop widgets | Tcl/Tk license, [tcl.tk](https://www.tcl.tk/) |
| Pillow | Images, previews and screen-region capture | HPND and bundled third-party notices, [python-pillow.org](https://python-pillow.org/) |
| ReportLab | PDF generation and invisible recognized text | BSD license, [reportlab.com](https://www.reportlab.com/) |
| Bitstream Vera | Fallback PDF text font | Bitstream Vera license, bundled with ReportLab |
| pypdf | Independent PDF validation | BSD-3-Clause, [pypdf.readthedocs.io](https://pypdf.readthedocs.io/) |
| pypdfium2 / PDFium | Local PDF page rendering/import | pypdfium2 Apache-2.0 / BSD-3-Clause choices and PDFium's complete bundled notices, [github.com/pypdfium2-team/pypdfium2](https://github.com/pypdfium2-team/pypdfium2) |
| PyWinRT | Python bindings for installed Windows APIs | MIT, [github.com/pywinrt/pywinrt](https://github.com/pywinrt/pywinrt) |
| Windows.Media.Ocr | Installed local Windows recognition engine | Windows OS component; not redistributed as a model or DLL from the OS |
| PyInstaller | Executable bootloader and build tool | GPL with the bootloader distribution exception, [pyinstaller.org](https://pyinstaller.org/) |

`third-party-licenses/` preserves the exact legal texts from the pinned installed distributions, including transitive components. Its manifest records their hashes and origins. The portable executable includes these texts under `licenses/`. Build-tool notices are retained even when the tool itself is not redistributed.

Windows Arial is used from the installed OS for PDF text when available; its font file is not redistributed in the application bundle. ReportLab's Vera font and notice are included for the fallback. The application icon is original code-generated artwork.

There is no model API, telemetry client, account service or updater in the product. Local capture, image import, OCR and PDF export do not send the material to an external service.
