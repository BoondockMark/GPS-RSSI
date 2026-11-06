import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HAS_AUDIO = all(importlib.util.find_spec(name) for name in ("numpy", "scipy", "av"))


@unittest.skipUnless(HAS_AUDIO, "Optional audio dependencies not installed")
class SinadReportTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from gps_rssi.audio_signal import write_wav
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wav = self.root / "audio.wav"
        time = np.arange(64000) / 32000
        samples = 6000 * np.sin(2 * np.pi * 1000 * time) + 600 * np.sin(2 * np.pi * 2000 * time)
        write_wav(self.wav, np.rint(samples).astype("<i2"))

    def manifest(self, state="completed", split=32000):
        from gps_rssi.sinad import file_hash
        data = dict(kind="audio_capture", id="capture-uuid", state=state, station="radio-a", sample_rate=32000,
                    wav_sha256=file_hash(self.wav), segments=[
                        dict(start_sample=0, end_sample=split, estimated_started_at="2026-11-06T17:00:00Z"),
                        dict(start_sample=split, end_sample=64000, estimated_started_at="2026-11-06T17:00:10Z")])
        (self.root / "report.json").write_text(json.dumps(data))

    def test_analysis_keeps_physical_units_provenance_and_utc_without_modifying_wav(self):
        from gps_rssi.sinad import analyze_wav
        self.manifest()
        before = self.wav.read_bytes()
        output = self.root / "analysis"
        report = analyze_wav(self.wav, output)
        self.assertEqual(report["valid_windows"], 2)
        self.assertEqual(report["station"], "radio-a")
        self.assertEqual(report["capture_id"], "capture-uuid")
        self.assertAlmostEqual(report["windows"][0]["sinad_db"], 20.04, delta=.2)
        self.assertEqual(report["windows"][1]["estimated_utc"], "2026-11-06T17:00:10+00:00")
        self.assertEqual(self.wav.read_bytes(), before)
        self.assertTrue((output / "report.html").is_file())
        with (output / "measurements.csv").open(newline="") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 2)
        with self.assertRaises(FileExistsError):
            analyze_wav(self.wav, output)

    def test_windows_crossing_audio_boundaries_are_invalid_instead_of_joining_gaps(self):
        from gps_rssi.sinad import analyze_wav
        self.manifest(split=16000)
        report = analyze_wav(self.wav, self.root / "analysis")
        self.assertEqual(report["valid_windows"], 1)
        self.assertEqual(report["windows"][0]["reason"], "audio_boundary_or_delivery_gap")
        self.assertIsNone(report["windows"][0]["sinad_db"])

    def test_failed_or_changed_captures_do_not_get_valid_measurements(self):
        from gps_rssi.sinad import analyze_wav
        self.manifest(state="failed")
        report = analyze_wav(self.wav, self.root / "failed")
        self.assertEqual(report["valid_windows"], 0)
        self.assertTrue(all(r["reason"] == "capture_not_completed" for r in report["windows"]))
        data = json.loads((self.root / "report.json").read_text())
        data["wav_sha256"] = "wrong"
        (self.root / "report.json").write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "does not match"):
            analyze_wav(self.wav, self.root / "changed")
        self.assertFalse((self.root / "changed").exists())

    def test_external_wav_without_timing_does_not_invent_utc_or_calibration(self):
        from gps_rssi.sinad import analyze_wav
        report = analyze_wav(self.wav, self.root / "external", station='</script><script>bad()</script>')
        self.assertIsNone(report["windows"][0]["estimated_utc"])
        page = (self.root / "external/report.html").read_text()
        self.assertNotIn('</script><script>bad()', page)
        self.assertIn("&lt;/script&gt;", page)
        self.assertIn("not calibrated receiver sensitivity", page)


if __name__ == "__main__":
    unittest.main()
