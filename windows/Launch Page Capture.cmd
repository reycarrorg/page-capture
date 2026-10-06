@echo off
setlocal
cd /d "%~dp0.."
if exist "dist\PageCapture\PageCapture.exe" (
  start "" "dist\PageCapture\PageCapture.exe"
  exit /b 0
)
if exist ".windows-runtime\Scripts\pythonw.exe" (
  start "" ".windows-runtime\Scripts\pythonw.exe" "windows\main.py"
  exit /b 0
)
echo Page Capture is not built in this checkout yet.
echo Download the Windows portable build or follow docs\WINDOWS.md.
pause
exit /b 1
