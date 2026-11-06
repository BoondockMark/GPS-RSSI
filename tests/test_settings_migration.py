"""Protect saved station settings while switching to role-based filenames."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gps_rssi.settings import load_settings, save_selection, setup_hints


class SettingsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = (b'# station notes\r\n[radio]\r\naddress="my-radio"\r\n'
                         b'[recording]\r\ndatabase="survey.sqlite"\r\n'
                         b'label="VR-N76 roof antenna"\r\n')

    def test_both_profiles_inherit_legacy_bytes_and_keep_database(self):
        for profile, old in [('mobile', 'uvpro.toml'), ('fixed', 'uv50pro.toml')]:
            with self.subTest(profile=profile):
                legacy = self.root / old
                target = self.root / f'{profile}.toml'
                legacy.write_bytes(self.original)
                settings = load_settings(target, profile=profile)
                self.assertEqual(target.read_bytes(), self.original)
                self.assertEqual(legacy.read_bytes(), self.original)
                self.assertEqual(settings['radio']['address'], 'my-radio')
                self.assertEqual(settings['recording']['database'], str(self.root / 'survey.sqlite'))
                self.assertEqual(settings['recording']['role'], profile)

    def test_existing_role_file_wins_and_legacy_changes_are_not_synced(self):
        legacy = self.root / 'uvpro.toml'
        target = self.root / 'mobile.toml'
        legacy.write_bytes(self.original)
        target.write_bytes(b'[radio]\naddress="selected-role-radio"\n')
        before = target.read_bytes()
        self.assertEqual(load_settings(target)['radio']['address'], 'selected-role-radio')
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(legacy.read_bytes(), self.original)

    def test_custom_filename_does_not_inherit_legacy_sibling(self):
        (self.root / 'uvpro.toml').write_bytes(self.original)
        settings = load_settings(self.root / 'custom.toml')
        self.assertNotEqual(settings['radio']['address'], 'my-radio')

    def test_discovery_reads_legacy_hints_without_migrating_and_prefers_role(self):
        (self.root / 'uvpro.toml').write_bytes(self.original)
        hints, warnings = setup_hints(self.root)
        self.assertFalse(warnings)
        self.assertIn('my-radio', hints)
        self.assertFalse((self.root / 'mobile.toml').exists())
        (self.root / 'mobile.toml').write_text('[radio]\naddress="role-radio"\n')
        hints, _ = setup_hints(self.root)
        self.assertIn('role-radio', hints)
        self.assertNotIn('my-radio', hints)

    def test_scanner_preserves_legacy_fields_and_backs_up_migrated_file(self):
        legacy = self.root / 'uv50pro.toml'
        target = self.root / 'fixed.toml'
        legacy.write_bytes(self.original)
        backup = save_selection({'address': 'new-radio', 'name': 'VR-N7600'},
                                target, profile='fixed')
        self.assertEqual(backup.read_bytes(), self.original)
        self.assertEqual(legacy.read_bytes(), self.original)
        result = load_settings(target, profile='fixed')
        self.assertEqual(result['radio']['address'], 'new-radio')
        self.assertEqual(result['recording']['label'], 'VR-N76 roof antenna')
        self.assertEqual(result['recording']['database'], str(self.root / 'survey.sqlite'))

    def test_migration_publication_failure_keeps_original_and_cleans_temporary(self):
        (self.root / 'uvpro.toml').write_bytes(self.original)
        with patch('gps_rssi.settings.os.link', side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError, 'disk error'):
                load_settings(self.root / 'mobile.toml')
        self.assertEqual(list(self.root.iterdir()), [self.root / 'uvpro.toml'])
        self.assertEqual((self.root / 'uvpro.toml').read_bytes(), self.original)

    def test_concurrent_role_creation_is_never_overwritten(self):
        (self.root / 'uvpro.toml').write_bytes(self.original)
        target = self.root / 'mobile.toml'
        winner = b'[radio]\naddress="concurrent-radio"\n'
        def publish_winner(*args):
            target.write_bytes(winner)
            raise FileExistsError()
        with patch('gps_rssi.settings.os.link', side_effect=publish_winner):
            self.assertEqual(load_settings(target)['radio']['address'], 'concurrent-radio')
        self.assertEqual(target.read_bytes(), winner)
        self.assertFalse(list(self.root.glob('.mobile.toml-*')))
