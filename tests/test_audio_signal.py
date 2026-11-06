import importlib.util
import math
import unittest

HAS_AUDIO = all(importlib.util.find_spec(name) for name in ("numpy", "scipy", "av"))


@unittest.skipUnless(HAS_AUDIO, "Optional audio dependencies not installed")
class AudioSignalTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        self.rate = 32000
        self.time = np.arange(self.rate) / self.rate

    def sine(self, frequency=1000, amplitude=6000):
        return amplitude * self.np.sin(2 * self.np.pi * frequency * self.time)

    def test_known_harmonic_distortion_matches_sinad_power_ratio(self):
        from gps_rssi.audio_signal import measure_sinad
        samples = self.sine() + self.sine(2000, 600)
        result = measure_sinad(samples)
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["sinad_db"], 10 * math.log10(101), delta=.2)

    def test_known_band_limited_noise_and_fractional_tone_frequency(self):
        from scipy import signal
        from gps_rssi.audio_signal import measure_sinad
        np = self.np
        noise = signal.sosfiltfilt(signal.butter(6, (400, 2700), btype="bandpass", fs=self.rate, output="sos"),
                                 np.random.default_rng(123).normal(size=self.rate))
        noise *= (6000 / np.sqrt(2) / 10) / np.sqrt(np.mean(noise ** 2))
        result = measure_sinad(self.sine(1002.3) + noise)
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["sinad_db"], 20.04, delta=.5)
        self.assertAlmostEqual(result["tone_hz"], 1002.3, delta=.02)

    def test_silence_noise_wrong_tone_clipping_and_short_audio_are_invalid(self):
        from gps_rssi.audio_signal import measure_sinad
        np = self.np
        cases = [(np.zeros(self.rate), "muted_or_low_level_audio"),
                 (np.random.default_rng(3).normal(0, 3000, self.rate), "test_tone_not_detected"),
                 (self.sine(1200), "test_tone_not_detected"),
                 (np.clip(self.sine(amplitude=50000), -32768, 32767), "clipped_audio"),
                 (np.zeros(100), "short_or_invalid_audio")]
        for samples, reason in cases:
            with self.subTest(reason=reason):
                result = measure_sinad(samples)
                self.assertFalse(result["valid"])
                self.assertIsNone(result["sinad_db"])
                self.assertEqual(result["reason"], reason)

    def test_near_perfect_tone_has_finite_flagged_upper_limit(self):
        from gps_rssi.audio_signal import measure_sinad
        result = measure_sinad(self.sine())
        self.assertTrue(result["valid"])
        self.assertEqual(result["sinad_db"], 80)
        self.assertTrue(result["lower_bound"])

    def test_tone_amplitude_ramps_and_sbc_roundtrip(self):
        from scipy import signal
        from gps_rssi.audio_signal import tone_pcm, encode_sbc, SbcDecoder
        np = self.np
        samples = tone_pcm(duration=1, level_dbfs=-18)
        self.assertEqual((samples[0], samples[-1]), (0, 0))
        self.assertLessEqual(np.max(np.abs(samples)), 32767 * 10 ** (-18 / 20) + 1)
        packets = encode_sbc(samples)
        self.assertTrue(all(len(payload) < 300 for payload, _ in packets))
        self.assertEqual(sum(count for _, count in packets), len(samples))
        decoder, out = SbcDecoder(), []
        for payload, _ in packets:
            out.extend(decoder.decode(payload))
        out.extend(decoder.decode(b""))
        decoded = np.concatenate(out)
        self.assertEqual(len(decoded), len(samples))
        lag = int(np.argmax(signal.correlate(decoded.astype(float), samples.astype(float), method="fft")) - len(samples) + 1)
        self.assertTrue(0 < lag < 256)  # SBC has filter-bank delay; sample clocks stay intact.
        self.assertGreater(np.corrcoef(samples[1000:-1000], decoded[1000 + lag:-1000 + lag])[0, 1], .99)

    def test_tone_bounds_and_invalid_analysis_bandwidth(self):
        from gps_rssi.audio_signal import tone_pcm, measure_sinad
        for options in (dict(duration=100), dict(duration=float("nan")), dict(level_dbfs=0), dict(frequency=100)):
            with self.assertRaises(ValueError):
                tone_pcm(**options)
        for bandwidth in ((0, 3000), (1500, 3000), (300, 20000)):
            with self.assertRaises(ValueError):
                measure_sinad(self.sine(), bandwidth=bandwidth)

    def test_corrupted_or_truncated_sbc_is_not_silently_discarded(self):
        from gps_rssi.audio_signal import tone_pcm, encode_sbc, SbcDecoder
        payload = encode_sbc(tone_pcm(duration=.5))[0][0]
        damaged = bytearray(payload)
        damaged[3] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC"):
            SbcDecoder().decode(damaged)
        decoder = SbcDecoder()
        decoder.decode(payload[:-1])
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            decoder.decode(b"")


if __name__ == "__main__":
    unittest.main()
