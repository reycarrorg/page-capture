# Source-built Windows bundle. No administrator privileges or updater.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).resolve().parent
datas = [(str(root / "LICENSE.md"), "licenses"),
         (str(root / "windows/THIRD_PARTY_NOTICES.md"), "licenses"),
         (str(root / "windows/third-party-licenses"), "licenses/third-party"),
         (str(root / "windows/assets"), "assets")]
datas += collect_data_files("reportlab", includes=["fonts/Vera.ttf", "fonts/*license*"])
datas += collect_data_files("pypdfium2_raw")
binaries = collect_dynamic_libs("pypdfium2_raw")
hiddenimports = collect_submodules("winrt")
a = Analysis([str(root / "windows/main.py")], pathex=[str(root / "windows")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PageCapture",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(root / "windows/assets/pagecapture.ico"),
    version=str(root / "windows/version_info.txt"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PageCapture")
