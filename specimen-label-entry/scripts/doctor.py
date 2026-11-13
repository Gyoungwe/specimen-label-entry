#!/usr/bin/env python3
"""Check local workflow dependencies without contacting a model or uploading inputs."""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import shutil
import sys


def diagnose():
    root = Path(__file__).resolve().parents[1]
    modules = {'Pillow': 'PIL', 'numpy': 'numpy', 'openpyxl': 'openpyxl', 'lxml': 'lxml'}
    packages = {}
    for distribution, module in modules.items():
        available = importlib.util.find_spec(module) is not None
        packages[distribution] = {'available': available}
        if available:
            try:
                packages[distribution]['version'] = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                packages[distribution]['version'] = 'unknown'
    assets = {}
    for name in ['rules.json', '标本库_模板.xlsx', '核心网格.xlsx']:
        path = root / 'assets' / name
        assets[name] = {'present': path.is_file()}
        if path.is_file():
            assets[name]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    python_ok = sys.version_info >= (3, 10)
    ready = python_ok and all(p['available'] for p in packages.values()) and all(a['present'] for a in assets.values())
    poppler = shutil.which('pdftoppm')
    return {'python': sys.executable, 'python_version': sys.version.split()[0], 'python_supported': python_ok,
            'packages': packages, 'assets': assets, 'jpg_and_workbook_ready': ready,
            'pdf_renderer': poppler, 'pdf_ready': ready and bool(poppler),
            'macos_ocr': shutil.which('swift') if sys.platform == 'darwin' else None,
            'vision': 'Host must provide actual image viewing or a configured OCR/vision tool; not tested by this command',
            'network_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-pdf', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = diagnose()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['jpg_and_workbook_ready'] and (not args.require_pdf or result['pdf_ready']) else 1


if __name__ == '__main__':
    sys.exit(main())
