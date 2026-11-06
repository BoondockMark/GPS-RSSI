from contextlib import redirect_stdout
from importlib.resources import files
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from gps_rssi.cli import parser
from gps_rssi.discovery import SERVICE_UUID, candidates_from_advertisements, discover, model_hint
from gps_rssi.scan_cli import run_scan
from gps_rssi.settings import save_selection, setup_hints


def advert(address, name=None, *, local=None, services=(), signal=-65):
    return (SimpleNamespace(address=address, name=name),
            SimpleNamespace(local_name=local, service_uuids=services, rssi=signal))


RADIO = {"address": "AA:BB:CC:DD:EE:FF", "name": "VR-N7600", "advertised_model": "VR-N7600",
         "setup_hints": [], "compatible_service": True, "bluetooth_rssi_dbm": -65}


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_supported_models_and_unrelated_devices(self):
        ads = {str(i): advert(f"AA:00:00:00:00:{i:02X}", name) for i, name in enumerate(
            ["UV-PRO", "BTECH UV-50PRO", "VR-N76", "VR-N7600", "SISPB-C9G001941", None, "VR-N76000"])}
        scanner = SimpleNamespace(discover=AsyncMock(return_value=ads))
        with tempfile.TemporaryDirectory() as d:
            result = await discover(4, scanner=scanner, directory=Path(d))
            self.assertEqual(list(Path(d).iterdir()), [])
        self.assertEqual(result["devices_seen"], 7)
        self.assertEqual({c["advertised_model"] for c in result["candidates"]},
                         {"UV-PRO", "UV-50PRO", "VR-N76", "VR-N7600"})
        scanner.discover.assert_awaited_once_with(timeout=4, return_adv=True)

    async def test_radio_scan_error_is_actionable(self):
        scanner = SimpleNamespace(discover=AsyncMock(side_effect=OSError("adapter disabled")))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RuntimeError, "Bluetooth scan failed.*adapter disabled"):
                await discover(scanner=scanner, directory=Path(d))

    async def test_advertised_alias_stays_distinct_from_configured_brand(self):
        template = tomllib.loads(files("gps_rssi").joinpath("profiles/fixed.toml").read_text())
        scanner = SimpleNamespace(discover=AsyncMock(return_value={"radio": advert(template["radio"]["address"], "VR-N7600")}))
        with tempfile.TemporaryDirectory() as d:
            result = await discover(scanner=scanner, directory=Path(d))
        radio = result["candidates"][0]
        self.assertEqual(radio["advertised_model"], "VR-N7600")
        self.assertTrue(any("UV-50PRO" in hint for hint in radio["setup_hints"]))


class CandidateTests(unittest.TestCase):
    def test_name_variants_and_model_boundaries(self):
        for name, expected in [("uv pro", "UV-PRO"), ("UV50PRO", "UV-50PRO"),
                               ("VGC VR-N76", "VR-N76"), ("VR_N7600", "VR-N7600"),
                               ("VR-N76000", None), ("NOTUVPRO", None), (None, None)]:
            self.assertEqual(model_hint(name), expected)

    def test_local_name_service_candidates_and_nonfinite_signal(self):
        ads = {"a": advert("A", None, local="UV-PRO"),
               "b": advert("B", None, services=[SERVICE_UUID.upper()], signal=float("nan")),
               "c": advert("C", None), "d": advert("D", "unrelated")}
        candidates = candidates_from_advertisements(ads, {"c": ["known setup"]})
        self.assertEqual({c["address"] for c in candidates}, {"A", "B", "C"})
        service = next(c for c in candidates if c["address"] == "B")
        self.assertIsNone(service["advertised_model"])
        self.assertIsNone(service["bluetooth_rssi_dbm"])
        json.dumps(candidates, allow_nan=False)

    def test_partial_personal_setup_inherits_address_hint_without_being_written(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mobile.toml"
            path.write_text('[recording]\nlabel="custom antenna"\n')
            before = path.read_bytes()
            hints, _ = setup_hints(Path(d))
            self.assertTrue(any("custom antenna" in hint for entries in hints.values() for hint in entries))
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((Path(d) / "fixed.toml").exists())


@unittest.skipUnless(importlib.util.find_spec("tomlkit"), "optional Bluetooth setup editor not installed")
class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "site.toml"

    def test_save_preserves_comments_and_unrelated_settings_with_exact_backup(self):
        original = b'# Keep my notes\n[radio]\naddress = "old-radio" # old comment\ntransport="rfcomm"\nrfcomm_channel=2\n[recording]\nlabel="roof antenna"\ndatabase="custom.sqlite"\n[dashboard]\nport=9876\n'
        self.path.write_bytes(original)
        backup = save_selection(RADIO, self.path, profile="fixed")
        self.assertEqual(backup.read_bytes(), original)
        text = self.path.read_text()
        self.assertIn("# Keep my notes", text)
        self.assertIn("# old comment", text)
        data = tomllib.loads(text)
        self.assertEqual(data["radio"]["address"], RADIO["address"])
        self.assertEqual(data["radio"]["advertised_name"], "VR-N7600")
        self.assertEqual(data["radio"]["transport"], "ble")
        self.assertEqual(data["recording"], {"label": "roof antenna", "database": "custom.sqlite"})
        self.assertEqual(data["dashboard"]["port"], 9876)
        self.assertFalse((self.path.parent / "custom.sqlite").exists())

    def test_new_fixed_setup_uses_selected_address_without_connecting(self):
        backup = save_selection(RADIO, self.path, profile="fixed")
        self.assertIsNone(backup)
        data = tomllib.loads(self.path.read_text())
        self.assertEqual(data["recording"]["role"], "fixed")
        self.assertFalse(data["recording"]["gps"])
        self.assertEqual(data["radio"]["address"], RADIO["address"])

    def test_invalid_existing_settings_are_not_replaced(self):
        original = b'[recording]\ngps="not a boolean"\n'
        self.path.write_bytes(original)
        with self.assertRaises(ValueError):
            save_selection(RADIO, self.path, profile="mobile")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.path.parent / "setup-backups").exists())
        self.assertFalse(list(self.path.parent.glob(".site.toml-*.toml")))

    def test_replace_failure_preserves_original_and_backup(self):
        original = b'[radio]\naddress="old"\n'
        self.path.write_bytes(original)
        with patch("gps_rssi.settings.os.replace", side_effect=OSError("file locked")):
            with self.assertRaisesRegex(OSError, "file locked"):
                save_selection(RADIO, self.path, profile="mobile")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(next((self.path.parent / "setup-backups").iterdir()).read_bytes(), original)

    def test_cli_selection_and_cancellation(self):
        result = {"devices_seen": 12, "candidates": [RADIO], "warnings": []}
        args = parser().parse_args(["scan", "--select", "--profile", "fixed", "--config", str(self.path)])
        with patch("gps_rssi.scan_cli.discover", new=AsyncMock(return_value=result)), redirect_stdout(io.StringIO()):
            run_scan(args, input_fn=lambda _: "q")
            self.assertFalse(self.path.exists())
            answers = iter(["invalid", "1"])
            run_scan(args, input_fn=lambda _: next(answers))
        self.assertEqual(tomllib.loads(self.path.read_text())["radio"]["address"], RADIO["address"])

    def test_json_listing_never_prompts_or_changes_settings(self):
        args = parser().parse_args(["scan", "--json", "--config", str(self.path)])
        output = io.StringIO()
        with patch("gps_rssi.scan_cli.discover", new=AsyncMock(return_value={"devices_seen": 1, "candidates": [RADIO], "warnings": []})), redirect_stdout(output):
            run_scan(args, input_fn=lambda _: self.fail("Unexpected prompt"))
        self.assertEqual(json.loads(output.getvalue())["candidates"][0]["advertised_model"], "VR-N7600")
        self.assertFalse(self.path.exists())

    def test_no_results_do_not_prompt(self):
        args = parser().parse_args(["scan", "--select"])
        with patch("gps_rssi.scan_cli.discover", new=AsyncMock(return_value={"devices_seen": 12, "candidates": [], "warnings": []})), redirect_stdout(io.StringIO()):
            run_scan(args, input_fn=lambda _: self.fail("Unexpected prompt"))

    def test_interactive_profile_choice_and_cancel_before_save(self):
        args = parser().parse_args(["scan", "--select", "--config", str(self.path)])
        result = {"devices_seen": 1, "candidates": [RADIO], "warnings": []}
        with patch("gps_rssi.scan_cli.discover", new=AsyncMock(return_value=result)), redirect_stdout(io.StringIO()):
            answers = iter(["1", "q"])
            run_scan(args, input_fn=lambda _: next(answers))
            self.assertFalse(self.path.exists())
            answers = iter(["1", "2"])
            run_scan(args, input_fn=lambda _: next(answers))
        self.assertEqual(tomllib.loads(self.path.read_text())["recording"]["role"], "fixed")

    def test_backup_failure_does_not_replace_settings(self):
        original = b'[radio]\naddress="old"\n'
        self.path.write_bytes(original)
        (self.path.parent / "setup-backups").write_text("preserve")
        with self.assertRaises(OSError):
            save_selection(RADIO, self.path, profile="mobile")
        self.assertEqual(self.path.read_bytes(), original)

    def test_detected_concurrent_edit_is_not_overwritten(self):
        from gps_rssi.settings import load_settings
        original = b'[radio]\naddress="old"\n'
        changed = b'[radio]\naddress="edited while scanning"\n'
        self.path.write_bytes(original)

        def validate_and_edit(*args, **kwargs):
            result = load_settings(*args, **kwargs)
            self.path.write_bytes(changed)
            return result

        with patch("gps_rssi.settings.load_settings", side_effect=validate_and_edit):
            with self.assertRaisesRegex(RuntimeError, "changed during selection"):
                save_selection(RADIO, self.path, profile="mobile")
        self.assertEqual(self.path.read_bytes(), changed)


if __name__ == "__main__":
    unittest.main()
