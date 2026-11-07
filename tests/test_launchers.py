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
        for name in ('start-app.sh', 'start-mobile.sh', 'start-fixed.sh', 'scan-radios.sh',
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
            ('start-app.sh', ['workspace', '--directory', str(self.root)]),
            ('start-mobile.sh', ['start', '--config', str(self.root / 'mobile.toml')]),
            ('start-fixed.sh', ['start', '--profile', 'fixed', '--config',
                                str(self.root / 'fixed.toml')]),
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
        self.assertEqual(self.run_launcher('start-mobile.sh').returncode, 7)

    def test_missing_environment_is_actionable(self):
        result = self.run_launcher('scan-radios.sh')
        self.assertEqual(result.returncode, 1)
        self.assertIn('README.md', result.stderr)


@unittest.skipUnless(os.name == 'nt', 'native Windows command launchers')
class WindowsLauncherTests(unittest.TestCase):
    def test_workspace_launcher_opens_help_without_starting_services(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['cmd.exe', '/d', '/c', f'call "{root / "start-app.cmd"}" --help < NUL'],
                                cwd=root.parent, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('--directory', result.stdout)
        self.assertIn('workspace', result.stdout)

    def test_role_launchers_forward_help_without_starting_radio(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('start-mobile.cmd', 'start-fixed.cmd'):
            with self.subTest(name=name):
                result = subprocess.run(
                    ['cmd.exe', '/d', '/c', f'call "{root / name}" --help < NUL'],
                    cwd=root.parent, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertIn('mobile.toml', result.stdout)
                self.assertIn('fixed.toml', result.stdout)
