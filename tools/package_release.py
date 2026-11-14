#!/usr/bin/env python3
"""Export checked skill and installer archives from the current source tree."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def package(out, refresh=False, replace=False):
    spec = importlib.util.spec_from_file_location('release_installer', ROOT / 'install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    if refresh:
        manifest = {'name': installer.NAME, 'version': installer.VERSION, 'files': installer.file_hashes(installer.SOURCE)}
        (ROOT / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    installer.verify_source()
    version = installer.VERSION
    out.mkdir(parents=True, exist_ok=True)
    core = [(installer.SOURCE / name, f'{installer.NAME}/{name}') for name in installer.file_hashes(installer.SOURCE)]
    roots = ['install.py', 'release-manifest.json', 'README.md', '安装与适配说明.md', 'specimen-label-entry', 'tests', 'docs', 'examples', 'tools']
    bundle = []
    for name in roots:
        source = ROOT / name
        items = sorted(source.rglob('*')) if source.is_dir() else [source]
        for path in items:
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc' and path.name not in ['.DS_Store', 'runtime.local.json']:
                bundle.append((path, f'{installer.NAME}-v{version}/{path.relative_to(ROOT).as_posix()}'))
    targets = [(out / f'{installer.NAME}-v{version}.skill', core),
               (out / f'{installer.NAME}-v{version}-claude.zip', core),
               (out / f'{installer.NAME}-v{version}-install.zip', bundle)]
    if not replace and any(path.exists() for path, _ in targets):
        raise FileExistsError('Release files already exist; choose a new output directory or pass --replace')
    results = []
    for destination, files in targets:
        temporary = destination.with_name('.' + destination.name + '.tmp')
        try:
            with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
                for source, name in files:
                    archive.write(source, name)
            with ZipFile(temporary) as archive:
                if archive.testzip() or any(archive.read(name) != source.read_bytes() for source, name in files):
                    raise ValueError('Archive failed CRC/content checks')
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        results.append({'path': str(destination), 'files': len(files), 'bytes': destination.stat().st_size,
                        'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
    archives = sorted([*out.glob('*.skill'), *out.glob('*.zip')])
    (out / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in archives), encoding='utf-8')
    return {'version': version, 'archives': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'downloads')
    parser.add_argument('--refresh-manifest', action='store_true', help='Regenerate core checksums after intentional source changes')
    parser.add_argument('--replace', action='store_true', help='Replace this version\'s generated archives')
    args = parser.parse_args()
    print(json.dumps(package(args.out, args.refresh_manifest, args.replace), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
