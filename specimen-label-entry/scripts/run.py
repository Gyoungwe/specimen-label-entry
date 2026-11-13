#!/usr/bin/env python3
"""Run a bundled helper with the isolated Python chosen during installation."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    scripts = Path(__file__).resolve().parent
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python scripts/run.py HELPER.py [arguments ...]')
    name = sys.argv[1]
    target = scripts / name
    if Path(name).name != name or not name.endswith('.py') or name == 'run.py' or not target.is_file():
        raise SystemExit('Choose a Python helper located directly in this skill\'s scripts directory')
    config = scripts.parent / 'runtime.local.json'
    executable = os.environ.get('SPECIMEN_PYTHON')
    if not executable and config.is_file():
        executable = json.loads(config.read_text(encoding='utf-8')).get('python')
    executable = executable or sys.executable
    if not Path(executable).is_file():
        raise SystemExit('Installed Python is missing; rerun install.py or set SPECIMEN_PYTHON to an interpreter path')
    return subprocess.run([executable, str(target), *sys.argv[2:]], check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
