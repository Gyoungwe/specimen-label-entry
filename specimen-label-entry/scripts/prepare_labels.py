#!/usr/bin/env python3
"""Render every PDF page and create a source manifest; OCR is optional and local."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--scale", type=int, default=2600)
    parser.add_argument("--ocr-macos", action="store_true")
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    manifest = {"sources": {}, "pages": []}
    for index, original in enumerate(args.inputs, 1):
        original = original.resolve()
        source_id = f"source{index}"
        folder = args.work / source_id
        folder.mkdir(exist_ok=True)
        if original.suffix.lower() == ".pdf":
            if not shutil.which("pdftoppm"):
                raise SystemExit("pdftoppm is required to render PDF inputs; use the bundled runtime or install Poppler")
            if list(folder.glob("page-*.png")):
                raise SystemExit(f"{folder} already contains rendered pages; choose a fresh work directory")
            subprocess.run(["pdftoppm", "-scale-to", str(args.scale), "-png", str(original), str(folder / "page")], check=True)
            images = sorted(folder.glob("page-*.png"), key=lambda p: int(re.search(r"(\d+)\.png$", p.name)[1]))
            if not images:
                raise SystemExit(f"No pages rendered: {original}")
        elif original.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".webp"):
            images = [original]
        else:
            raise SystemExit(f"Unsupported input: {original}")
        manifest["sources"][source_id] = {"name": original.name, "path": str(original),
            "sha256": hashlib.sha256(original.read_bytes()).hexdigest(), "page_count": len(images)}
        for page, image in enumerate(images, 1):
            manifest["pages"].append({"source": source_id, "page": page, "image": str(image.resolve())})
    manifest_path = args.work / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    if args.ocr_macos:
        if sys.platform != "darwin" or not shutil.which("swiftc"):
            raise SystemExit("Manifest saved. macOS OCR needs macOS and swiftc; inspect rendered pages with vision instead")
        binary = args.work / "ocr_macos"
        swift = Path(__file__).with_name("ocr_macos.swift")
        subprocess.run(["swiftc", "-O", str(swift), "-o", str(binary)], check=True)
        subprocess.run([str(binary.resolve()), str((args.work / "ocr.json").resolve())] +
                       [p["image"] for p in manifest["pages"]], check=True)
    print(json.dumps({"manifest": str(manifest_path), "pages": len(manifest["pages"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
