"""Reproducible source-driven Windows build and portable ZIP packaging."""
from __future__ import annotations
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resources():
    from PIL import Image, ImageDraw
    assets = ROOT / "windows/assets"
    assets.mkdir(exist_ok=True)
    # Original code-native document/capture mark, no third-party art or fonts.
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 248, 248), radius=52, fill="#187153")
    draw.rounded_rectangle((66, 43, 190, 217), radius=14, fill="white")
    for y, right in ((93, 164), (121, 164), (149, 147)):
        draw.rounded_rectangle((89, y, right, y+9), radius=4, fill="#187153")
    draw.rectangle((43, 71, 56, 169), fill="#abdfca")
    image.save(assets / "pagecapture.png")
    image.save(assets / "pagecapture.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    target = ROOT / "windows/third-party-licenses"
    target.mkdir(exist_ok=True)
    names = []
    for distribution in metadata.distributions():
        component = distribution.metadata["Name"]
        if component == "pip":
            continue
        for entry in distribution.files or []:
            lowered = str(entry).lower()
            filename = Path(str(entry)).name.lower()
            if not ("/licenses/" in lowered or filename.startswith(("license", "licence", "copying", "notice")) or any(term in filename for term in ("license", "licence")) and filename.endswith((".txt", ".md"))):
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file() or source.suffix.lower() in {".py", ".pyc", ".pyd"}:
                continue
            relative = Path(component) / Path(str(entry)).name
            destination = target / relative
            destination.parent.mkdir(exist_ok=True)
            if destination.exists() and source.read_bytes() != destination.read_bytes():
                destination = destination.with_name(hashlib.sha256(str(entry).encode()).hexdigest()[:8] + "-" + destination.name)
            shutil.copyfile(source, destination)
            names.append({"component": component, "source": str(entry), "path": destination.relative_to(ROOT).as_posix(), "sha256": digest(destination)})
    base = Path(sys.base_prefix)
    for label, candidates in {
        "CPython": [base / "LICENSE.txt", base / "LICENSE"],
        "Tcl": [base / "tcl/tcl8.6/license.terms", base / "share/tcl8.6/license.terms", base / "lib/tcl8.6/license.terms"],
        "Tk": [base / "tcl/tk8.6/license.terms", base / "share/tk8.6/license.terms", base / "lib/tk8.6/license.terms"],
    }.items():
        source = next((p for p in candidates if p.is_file()), None)
        if source:
            destination = target / (label + "-LICENSE.txt")
            shutil.copyfile(source, destination)
            names.append({"component": label, "path": destination.relative_to(ROOT).as_posix(), "sha256": digest(destination)})
    (target / "manifest.json").write_text(json.dumps(names, indent=2), encoding="utf-8")


def package():
    folder = ROOT / "dist/PageCapture"
    if not (folder / "PageCapture.exe").is_file():
        raise RuntimeError("Build the executable first.")
    from pagecapture import __version__
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    records = [{"path": path.relative_to(folder).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)} for path in files]
    source = [{"path": p.relative_to(ROOT).as_posix(), "sha256": digest(p)} for p in sorted((ROOT / "windows").rglob("*.py")) if "__pycache__" not in p.parts and ".verification" not in p.parts]
    receipt = {"version": __version__, "platform": "windows-x64", "authenticode_signed": False,
        "source_files": source, "files": records, "bundled_python": sys.version.split()[0],
        "dependencies": {name: metadata.version(name) for name in ("Pillow", "reportlab", "pypdf", "pypdfium2", "winrt-runtime", "pyinstaller")}}
    subprocess_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    receipt["source_commit"] = subprocess_result.stdout.strip() if subprocess_result.returncode == 0 else None
    (folder / "BUILD-RECEIPT.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    destination = ROOT / f"dist/PageCapture-Windows-{__version__}.zip"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, Path("PageCapture") / path.relative_to(folder))
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Portable archive CRC verification failed.")
    receipt.update(archive=destination.name, archive_bytes=destination.stat().st_size, archive_sha256=digest(destination))
    (ROOT / "dist/windows-build-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("version", "archive", "archive_bytes", "archive_sha256", "source_commit", "authenticode_signed")}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "windows"))
    if not args.package_only:
        resources()
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(ROOT/"dist"), "--workpath", str(ROOT/"build/windows"), str(ROOT/"windows/PageCapture.spec")], cwd=ROOT, check=True)
    package()


if __name__ == "__main__":
    main()
