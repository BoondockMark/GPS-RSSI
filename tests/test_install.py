"""Installation orchestration without downloading packages or touching real setups."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gps_rssi_installer', ROOT / 'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'checkout with spaces'
        self.project.mkdir()
        self.python = self.project / '.venv' / ('Scripts/python.exe' if installer.IS_WINDOWS else 'bin/python')

    def environment(self):
        self.python.parent.mkdir(parents=True, exist_ok=True)
        self.python.write_bytes(b'existing environment')

    def test_new_install_creates_environment_and_installs_all_app_features(self):
        with patch.object(installer.venv, 'EnvBuilder') as builder, patch.object(installer, 'run') as run:
            installer.install(self.project)
        builder.assert_called_once_with(with_pip=True)
        builder.return_value.create.assert_called_once_with(self.project / '.venv')
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(all(command[0] == self.python for command in commands))
        self.assertIn([self.python, '-m', 'pip', 'install', '--upgrade', '-e', '.[bluetooth,audio]'], commands)
        self.assertIn([self.python, '-m', 'pip', 'check'], commands)
        self.assertIn('import numpy, scipy, av', commands[-1][-1])
        self.assertIn('get_channel', commands[-1][-1])
        self.assertIn('basemap.js', commands[-1][-1])
        self.assertTrue(all(call.args[1] == self.project for call in run.call_args_list))

    def test_refresh_reuses_environment_and_preserves_personal_files(self):
        self.environment()
        files = [self.project / 'mobile.toml', self.project / 'vehicle.radio.toml',
                 self.project / 'data/radio.sqlite', self.project / 'data/.gps-rssi-tiles/cache.sqlite']
        for file in files:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'personal data')
        with patch.object(installer.venv, 'EnvBuilder') as builder, patch.object(installer, 'run') as run:
            installer.install(self.project, basic=True)
        builder.assert_not_called()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn([self.python, '-m', 'pip', 'install', '--upgrade', '-e', '.[bluetooth]'], commands)
        self.assertNotIn('import numpy, scipy, av', commands[-1][-1])
        for file in files:
            self.assertEqual(file.read_bytes(), b'personal data')
        self.assertEqual(self.python.read_bytes(), b'existing environment')

    def test_other_os_environment_is_not_overwritten(self):
        other = self.project / '.venv' / ('bin/python' if installer.IS_WINDOWS else 'Scripts/python.exe')
        other.parent.mkdir(parents=True)
        other.write_bytes(b'other platform')
        with patch.object(installer, 'run') as run, self.assertRaisesRegex(RuntimeError, 'another operating system'):
            installer.install(self.project)
        run.assert_not_called()
        self.assertEqual(other.read_bytes(), b'other platform')

    @unittest.skipUnless(os.name == 'posix', 'symlink fixture')
    def test_dangling_other_os_interpreter_is_not_overwritten(self):
        other = self.project / '.venv/Scripts/python.exe'
        other.parent.mkdir(parents=True)
        other.symlink_to(self.project / 'missing-python')
        with self.assertRaisesRegex(RuntimeError, 'another operating system'):
            installer.install(self.project)
        self.assertTrue(other.is_symlink())

    def test_old_python_and_missing_git_have_actionable_errors(self):
        with patch.object(installer.sys, 'version_info', (3, 10)), self.assertRaisesRegex(RuntimeError, 'Python 3.11'):
            installer.install(self.project)
        with patch.object(installer.shutil, 'which', return_value=None), self.assertRaisesRegex(RuntimeError, 'Install Git'):
            installer.install(self.project)
        self.assertFalse((self.project / '.venv').exists())

    def test_failed_install_stops_before_verification_and_preserves_exit_code(self):
        self.environment()
        failure = subprocess.CalledProcessError(7, ['pip', 'install'])
        with patch.object(installer, 'PROJECT_DIR', self.project), \
             patch.object(installer, 'run', side_effect=[None, None, failure]) as run:
            self.assertEqual(installer.main([]), 7)
        self.assertEqual(run.call_count, 3)

    def test_incomplete_environment_can_be_completed_without_clearing_it(self):
        environment = self.project / '.venv'
        environment.mkdir()
        (environment / 'marker').write_text('keep')
        with patch.object(installer.venv, 'EnvBuilder') as builder, patch.object(installer, 'run'):
            installer.install(self.project)
        builder.assert_called_once_with(with_pip=True)
        self.assertEqual((environment / 'marker').read_text(), 'keep')

    def test_existing_environment_without_pip_is_repaired_offline(self):
        installer.venv.EnvBuilder(with_pip=False).create(self.project / '.venv')
        commands = []
        original_run = installer.run
        def preflight_only(command, project):
            if not commands:
                original_run(command, project)
            commands.append(command)
        with patch.object(installer, 'run', side_effect=preflight_only):
            installer.install(self.project, basic=True)
        result = subprocess.run([str(self.python), '-m', 'pip', '--version'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_subprocess_arguments_and_working_directory_are_not_shell_interpolated(self):
        with patch.object(installer.subprocess, 'run') as run:
            installer.run([self.python, '-m', 'pip', 'install', '-e', '.[bluetooth,audio]'], self.project)
        run.assert_called_once_with([str(self.python), '-m', 'pip', 'install', '-e', '.[bluetooth,audio]'],
                                    cwd=self.project, check=True)

    def test_native_installer_help_works_from_another_directory_with_spaces(self):
        (self.project / 'scripts').mkdir()
        shutil.copy2(ROOT / 'scripts/install.py', self.project / 'scripts/install.py')
        if os.name == 'nt':
            shutil.copy2(ROOT / 'install.cmd', self.project / 'install.cmd')
            command = ['cmd.exe', '/d', '/c', f'call "{self.project / "install.cmd"}" --help < NUL']
        else:
            shutil.copy2(ROOT / 'install.sh', self.project / 'install.sh')
            command = [str(self.project / 'install.sh'), '--help']
        result = subprocess.run(command, cwd=self.temp.name, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('--basic', result.stdout)
        self.assertFalse((self.project / '.venv').exists())
        if os.name == 'nt':
            command = ['cmd.exe', '/d', '/c', f'call "{self.project / "install.cmd"}" --invalid < NUL']
        else:
            command = [str(self.project / 'install.sh'), '--invalid']
        failed = subprocess.run(command, cwd=self.temp.name, capture_output=True, text=True, timeout=20)
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
        self.assertFalse((self.project / '.venv').exists())


if __name__ == '__main__':
    unittest.main()
