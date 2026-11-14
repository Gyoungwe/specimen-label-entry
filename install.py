#!/usr/bin/env python3
"""Install the local release for Codex, Claude Code or DeepSeek Harness.

Backups and an isolated Python environment live outside host skill catalogs.
This installer changes no model, credential, permission or global hook settings.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

NAME = 'specimen-label-entry'
VERSION = '3.4.0'
ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / NAME
IGNORED = {'runtime.local.json', '.DS_Store'}


def file_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and p.name not in IGNORED
            and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def verify_source():
    manifest = ROOT / 'release-manifest.json'
    if not manifest.is_file():
        raise ValueError('release-manifest.json is missing; use the complete installation bundle')
    expected = json.loads(manifest.read_text(encoding='utf-8'))
    if expected['version'] != VERSION or expected['files'] != file_hashes(SOURCE):
        raise ValueError('Release checksums do not match; export a complete, unmodified bundle')


def selected_hosts(host):
    return ['codex', 'claude', 'deepseek'] if host == 'all' else [host]


def destinations(hosts, user_root, project=None, use_environment=True):
    if project:
        roots = {'codex': project / '.agents', 'claude': project / '.claude', 'deepseek': project / '.dsh'}
    else:
        roots = {'codex': Path(os.environ.get('CODEX_HOME', str(user_root / '.codex'))) if use_environment else user_root / '.codex',
                 'claude': user_root / '.claude',
                 'deepseek': Path(os.environ.get('DSH_HOME', str(user_root / '.dsh'))) if use_environment else user_root / '.dsh'}
    return {host: roots[host] / 'skills' / NAME for host in hosts}


def python_path(environment):
    return environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def ensure_environment(user_root, interpreter, no_deps):
    interpreter = Path(shutil.which(str(interpreter)) or interpreter).absolute()
    if not interpreter.is_file():
        raise ValueError('Python interpreter does not exist')
    if no_deps:
        return interpreter
    environment = user_root / '.local/share' / NAME / 'venvs' / VERSION
    executable = python_path(environment)
    environment.parent.mkdir(parents=True, exist_ok=True)
    if not executable.exists():
        print('Creating isolated Python environment...', file=sys.stderr, flush=True)
        subprocess.run([str(interpreter), '-m', 'venv', str(environment)], check=True)
    print('Installing/checking image and workbook dependencies...', file=sys.stderr, flush=True)
    subprocess.run([str(executable), '-m', 'pip', 'install', '--disable-pip-version-check',
                    '-r', str(SOURCE / 'requirements.txt')], check=True, stdout=sys.stderr)
    return executable.absolute()


def install_one(source, destination, host, interpreter, backups):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not (destination / 'SKILL.md').is_file():
        raise ValueError(f'Destination is not a skill directory: {destination}')
    if destination.resolve() == source.resolve():
        raise ValueError('Release source cannot also be an installation destination')
    stage = Path(tempfile.mkdtemp(prefix='.specimen-install-', dir=destination.parent))
    backup = None
    try:
        shutil.copytree(source, stage, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', 'runtime.local.json'))
        (stage / 'runtime.local.json').write_text(json.dumps({'python': str(interpreter), 'host': host,
                                                              'version': VERSION}, ensure_ascii=False, indent=2), encoding='utf-8')
        if file_hashes(stage) != file_hashes(source):
            raise ValueError('Installed content differs from the release')
        if destination.exists() or destination.is_symlink():
            backup = backups / host
            backup.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(backup)
        try:
            stage.rename(destination)
        except BaseException:
            if backup is not None:
                backup.rename(destination)
            raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {'host': host, 'path': str(destination.absolute()), 'version': VERSION,
            'backup': str(backup) if backup else None, 'content_verified': file_hashes(destination) == file_hashes(source)}


def codex_alias(user_root, installed, backups):
    alias = user_root / '.agents/skills' / NAME
    if alias.absolute() == installed.absolute():
        return {'path': str(alias), 'status': 'primary'}
    if alias.is_symlink() and alias.resolve() == installed.resolve():
        return {'path': str(alias), 'status': 'already_linked'}
    if alias.exists() or alias.is_symlink():
        # Preserve another existing installation; the primary install still works.
        return {'path': str(alias), 'status': 'existing_entry_preserved',
                'note': 'Resolve a different same-named shared skill before relying on that catalog root'}
    alias.parent.mkdir(parents=True, exist_ok=True)
    try:
        alias.symlink_to(installed.absolute(), target_is_directory=True)
        return {'path': str(alias), 'status': 'linked'}
    except OSError:
        shutil.copytree(installed, alias, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        return {'path': str(alias), 'status': 'copied', 'note': 'Symlinks unavailable; rerun installer after upgrades'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=['codex', 'claude', 'deepseek', 'all'], default='all')
    parser.add_argument('--project', type=Path, help='Install into this project instead of user-level directories')
    parser.add_argument('--python', default=sys.executable, help='Interpreter used to create the isolated environment')
    parser.add_argument('--no-deps', action='store_true', help='Use --python as-is; do not create a venv or install packages')
    parser.add_argument('--user-root', type=Path, help='Override the user root, useful for an isolated installation test')
    parser.add_argument('--receipt', type=Path, help='Write a local installation receipt outside host configuration')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10 or later is required')
    verify_source()
    user_root = (args.user_root or Path.home()).expanduser().absolute()
    project = args.project.expanduser().absolute() if args.project else None
    targets = destinations(selected_hosts(args.host), user_root, project, args.user_root is None)
    plan = {'version': VERSION, 'scope': 'project' if project else 'user', 'targets': {h: str(p) for h, p in targets.items()},
            'claude_desktop': 'Import the separate .skill/ZIP through Claude Customize; ~/.claude/skills installs Claude Code only',
            'uses_models': False}
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    interpreter = ensure_environment(user_root, args.python, args.no_deps)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backups = user_root / '.local/share' / NAME / 'backups' / stamp
    installed = []
    try:
        for host, destination in targets.items():
            installed.append(install_one(SOURCE, destination, host, interpreter, backups))
    except BaseException:
        # Roll back completed hosts when a later host fails, keeping all copies consistent.
        for item in reversed(installed):
            destination = Path(item['path'])
            shutil.rmtree(destination)
            if item['backup']:
                Path(item['backup']).rename(destination)
        raise
    receipt = {**plan, 'installed': installed, 'python': str(interpreter), 'created_at': stamp}
    if 'codex' in targets and not project:
        receipt['codex_shared_discovery'] = codex_alias(user_root, targets['codex'], backups)
    for item in installed:
        result = subprocess.run([str(interpreter), str(Path(item['path']) / 'scripts/doctor.py')],
                                check=False, capture_output=True, text=True, encoding='utf-8')
        item['doctor_exit_code'] = result.returncode
        try:
            item['doctor'] = json.loads(result.stdout)
        except json.JSONDecodeError:
            item['doctor_error'] = result.stderr[-2000:]
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if any(item['doctor_exit_code'] for item in installed):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
