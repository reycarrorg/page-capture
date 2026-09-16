@echo off
setlocal
set "APP=%~dp0page_capture.py"

where py.exe >nul 2>nul
if not errorlevel 1 (
    where pyw.exe >nul 2>nul
    if not errorlevel 1 (
        py.exe -3 "%APP%" --runtime-check >nul 2>nul
        if not errorlevel 1 (
            if defined PAGE_CAPTURE_CHECK_ONLY (
                echo LAUNCHER_CHECK_OK
                exit /b 0
            )
            start "" pyw.exe -3 "%APP%"
            exit /b 0
        )
    )
)

where python.exe >nul 2>nul
if not errorlevel 1 (
    where pythonw.exe >nul 2>nul
    if not errorlevel 1 (
        python.exe "%APP%" --runtime-check >nul 2>nul
        if not errorlevel 1 (
            if defined PAGE_CAPTURE_CHECK_ONLY (
                echo LAUNCHER_CHECK_OK
                exit /b 0
            )
            start "" pythonw.exe "%APP%"
            exit /b 0
        )
    )
)

echo.
echo Page Capture could not find a compatible Python setup.
echo.
echo It needs Python 3 with Pillow, ReportLab, and Tkinter.
echo Open this folder in Codex and ask it to repair the Page Capture launcher.
echo No screenshots or existing session files were changed.
pause
exit /b 1
