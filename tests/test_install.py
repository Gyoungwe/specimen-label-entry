#!/usr/bin/env python3
"""Exercise installation, backup and rollback in an isolated user directory."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_installer', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skill install 空格 ')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def run_install(self, *extra):
        stream = io.StringIO()
        args = ['install.py', '--user-root', str(self.home), '--host', 'all', '--no-deps', '--python', sys.executable, *extra]
        with patch.object(sys, 'argv', args), redirect_stdout(stream):
            installer.main()
        return json.loads(stream.getvalue())

    def test_three_host_install_and_python_dispatch_without_config_mutation(self):
        settings = [self.home / '.claude/settings.json', self.home / '.dsh/settings.yaml']
        for path in settings:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('existing settings stay unchanged')
        receipt = self.run_install()
        self.assertEqual(len(receipt['installed']), 3)
        self.assertTrue(all(x['content_verified'] and x['doctor_exit_code'] == 0 for x in receipt['installed']))
        for path in settings:
            self.assertEqual(path.read_text(), 'existing settings stay unchanged')
        root = self.home / '.claude/skills/specimen-label-entry'
        result = subprocess.run([sys.executable, str(root / 'scripts/run.py'), 'doctor.py'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['jpg_and_workbook_ready'])

    def test_existing_skill_is_backed_up_on_upgrade(self):
        destination = self.home / '.codex/skills/specimen-label-entry'
        destination.mkdir(parents=True)
        (destination / 'SKILL.md').write_text('previous version')
        (destination / 'user-note.txt').write_text('preserve my old note in backup')
        receipt = self.run_install()
        item = next(x for x in receipt['installed'] if x['host'] == 'codex')
        backup = Path(item['backup'])
        self.assertEqual((backup / 'SKILL.md').read_text(), 'previous version')
        self.assertEqual((backup / 'user-note.txt').read_text(), 'preserve my old note in backup')
        self.assertFalse((destination / 'user-note.txt').exists())

    def test_later_host_failure_restores_previous_installation(self):
        old = self.home / '.codex/skills/specimen-label-entry'
        old.mkdir(parents=True)
        (old / 'SKILL.md').write_text('previous')
        blocked = self.home / '.claude/skills/specimen-label-entry'
        blocked.mkdir(parents=True)
        (blocked / 'user-file.txt').write_text('not a skill; keep it')
        with self.assertRaises(ValueError):
            self.run_install()
        self.assertEqual((old / 'SKILL.md').read_text(), 'previous')
        self.assertEqual((blocked / 'user-file.txt').read_text(), 'not a skill; keep it')
        self.assertFalse((self.home / '.dsh/skills/specimen-label-entry').exists())

    def test_project_plan_uses_host_specific_project_roots(self):
        project = self.home / 'research project'
        receipt = self.run_install('--project', str(project), '--dry-run')
        self.assertEqual(receipt['targets']['codex'], str(project / '.agents/skills/specimen-label-entry'))
        self.assertEqual(receipt['targets']['claude'], str(project / '.claude/skills/specimen-label-entry'))
        self.assertEqual(receipt['targets']['deepseek'], str(project / '.dsh/skills/specimen-label-entry'))
        self.assertFalse(project.exists())

    def test_checksum_failure_rejects_modified_release(self):
        source = self.home / 'source'
        source.mkdir()
        (source / 'SKILL.md').write_text('modified')
        (self.home / 'release-manifest.json').write_text(json.dumps({'version': installer.VERSION, 'files': {}}))
        with patch.object(installer, 'ROOT', self.home), patch.object(installer, 'SOURCE', source):
            with self.assertRaisesRegex(ValueError, 'checksums'):
                installer.verify_source()


if __name__ == '__main__':
    unittest.main()
