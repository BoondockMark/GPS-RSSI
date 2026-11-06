"""Exercise Linux entry points without requiring a Bluetooth adapter."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix', 'POSIX shell launchers')
class LinuxLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'checkout with spaces'
        (self.root / 'scripts').mkdir(parents=True)
        source = Path(__file__).resolve().parents[1]
        for name in ('start-uvpro.sh', 'start-uv50pro.sh', 'scan-radios.sh',
                     'scripts/run-gps-rssi.sh'):
            shutil.copy2(source / name, self.root / name)

    def fake_python(self, code=0):
        target = self.root / '.venv/bin/python'
        target.parent.mkdir(parents=True)
        # exec the real interpreter so we can observe arguments, cwd and status.
        script = self.root / 'capture.py'
        script.write_text('import json, os, sys\n'
                          'print(json.dumps([os.getcwd(), sys.argv[1:]]))\n'
                          f'sys.exit({code})\n')
        import shlex
        target.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable)
                          + ' ' + shlex.quote(str(script)) + ' "$@"\n')
        target.chmod(0o755)

    def run_launcher(self, name, *args):
        return subprocess.run([str(self.root / name), *args], cwd=self.temp.name,
                              capture_output=True, text=True)

    def test_profiles_and_arguments_from_another_directory(self):
        self.fake_python()
        for name, expected in (
            ('start-uvpro.sh', ['start', '--config', str(self.root / 'uvpro.toml')]),
            ('start-uv50pro.sh', ['start', '--profile', 'fixed', '--config',
                                str(self.root / 'uv50pro.toml')]),
            ('scan-radios.sh', ['scan', '--select']),
        ):
            with self.subTest(name=name):
                result = self.run_launcher(name, '--config', 'custom setup.toml')
                self.assertEqual(result.returncode, 0, result.stderr)
                cwd, args = json.loads(result.stdout)
                self.assertEqual(cwd, str(self.root))
                self.assertEqual(args, ['-m', 'gps_rssi.cli', *expected,
                                        '--config', 'custom setup.toml'])

    def test_failure_status_reaches_caller(self):
        self.fake_python(7)
        self.assertEqual(self.run_launcher('start-uvpro.sh').returncode, 7)

    def test_missing_environment_is_actionable(self):
        result = self.run_launcher('scan-radios.sh')
        self.assertEqual(result.returncode, 1)
        self.assertIn('README.md', result.stderr)
