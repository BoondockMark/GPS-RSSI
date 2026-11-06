"""Optional PCM/SBC processing; no radio I/O or automatic transmission."""

import math
import wave

RATE = 32000
MAX_TONE_SECONDS = 10


def dependencies():
    try:
        import numpy as np
        import av
        from scipy import signal, optimize
    except ImportError as exc:
        raise RuntimeError('Audio support requires: pip install -e ".[bluetooth,audio]"') from exc
    return np, av, signal, optimize


def validate_tone(frequency, duration, level_dbfs):
    for value in (frequency, duration, level_dbfs):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Tone parameters must be finite numbers")
    if not 400 <= frequency <= 2500:
        raise ValueError("Tone frequency must be between 400 and 2500 Hz")
    if not .5 <= duration <= MAX_TONE_SECONDS:
        raise ValueError(f"Tone duration must be between 0.5 and {MAX_TONE_SECONDS} seconds")
    if not -36 <= level_dbfs <= -6:
        raise ValueError("Tone peak level must be between -36 and -6 dBFS")


def tone_pcm(frequency=1000, duration=3, level_dbfs=-18):
    validate_tone(frequency, duration, level_dbfs)
    np, _, _, _ = dependencies()
    length = round(duration * RATE)
    samples = np.sin(2 * np.pi * frequency * np.arange(length) / RATE)
    ramp = min(round(.02 * RATE), length // 2)
    envelope = np.sin(np.linspace(0, np.pi / 2, ramp)) ** 2
    samples[:ramp] *= envelope
    samples[-ramp:] *= envelope[::-1]
    return np.rint(samples * 32767 * 10 ** (level_dbfs / 20)).astype("<i2")


def write_wav(path, pcm, rate=RATE):
    # wave.open(path) uses truncation, so exclusively create its underlying file.
    with path.open("xb") as stream, wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())


def encode_sbc(pcm):
    """Pre-encode known radio-compatible frames before opening a radio connection."""
    np, av, _, _ = dependencies()
    codec = av.CodecContext.create("sbc", "w")
    codec.sample_rate, codec.layout, codec.format = RATE, "mono", "s16"
    # Avoid the 6.4375ms boundary: recent FFmpeg rounds that down to 12 blocks.
    codec.options = {"global_quality": str(16 * 118), "sbc_delay": "0.0065", "msbc": "false"}
    codec.open()
    if codec.frame_size != 128:
        raise RuntimeError("SBC encoder did not select the required 16-block / 8-subband format")
    packets = []
    for index in range(0, len(pcm), codec.frame_size):
        block = np.zeros((1, codec.frame_size), dtype=np.int16)
        part = pcm[index:index + codec.frame_size]
        block[0, :len(part)] = part
        frame = av.AudioFrame.from_ndarray(block, format="s16", layout="mono")
        frame.sample_rate = RATE
        packets.extend(bytes(p) for p in codec.encode(frame))
    packets.extend(bytes(p) for p in codec.encode(None))
    if not packets or any(p[:3] != b"\x9c\x71\x10" for p in packets):
        raise RuntimeError("Unexpected SBC format; refusing to transmit")
    # Six 4ms frames per write: 24ms audio, below the radio's 300-byte payload limit.
    return [(b"".join(packets[i:i + 6]), min(6, len(packets) - i) * 128)
            for i in range(0, len(packets), 6)]


class SbcDecoder:
    def __init__(self):
        self.np, av, _, _ = dependencies()
        self.codec = av.CodecContext.create("sbc", "r")
        self.buffer = bytearray()
        self.expected_samples = self.decoded_samples = 0

    @staticmethod
    def crc(data):
        value = 0x0f
        for byte in data:
            for shift in range(7, -1, -1):
                feedback = ((byte >> shift) ^ (value >> 7)) & 1
                value = (value << 1) & 255
                if feedback:
                    value ^= 0x1d
        return value

    def decode(self, data):
        # Validate complete mono SBC frames before feeding FFmpeg's parser,
        # which can otherwise silently resynchronize past corrupt/missing bytes.
        self.buffer.extend(data)
        accepted = bytearray()
        while len(self.buffer) >= 4:
            sync, header, bitpool, crc = self.buffer[:4]
            if sync != 0x9c or header >> 6 != 1 or (header >> 2) & 3:
                raise ValueError("Radio audio must be standard SBC, 32 kHz mono")
            subbands, blocks = (8 if header & 1 else 4), 4 * (1 + ((header >> 4) & 3))
            if not 2 <= bitpool <= 16 * subbands:
                raise ValueError("Invalid SBC bitpool")
            size = 4 + subbands // 2 + (blocks * bitpool + 7) // 8
            if len(self.buffer) < size:
                break
            frame = self.buffer[:size]
            if self.crc(frame[1:3] + frame[4:4 + subbands // 2]) != crc:
                raise ValueError("SBC header/scale-factor CRC failed")
            accepted.extend(frame)
            del self.buffer[:size]
            self.expected_samples += blocks * subbands
        if not data and self.buffer:
            raise ValueError("Incomplete SBC frame at audio boundary")
        out = []
        packets = self.codec.parse(bytes(accepted)) if accepted or (not data and self.expected_samples) else []
        for packet in packets:
            for frame in self.codec.decode(packet):
                if frame.sample_rate != RATE or len(frame.layout.channels) != 1 or frame.format.name not in ("s16", "s16p"):
                    raise ValueError("Radio audio must be 32 kHz mono for this capture adapter")
                out.append(frame.to_ndarray().reshape(-1).astype("<i2"))
                self.decoded_samples += frame.samples
        if not data and self.decoded_samples != self.expected_samples:
            raise ValueError("SBC decoded sample count differs from frame headers")
        return out


def measure_sinad(pcm, rate=RATE, *, frequency=1000, bandwidth=(300, 3000)):
    """Band-limited fitted-fundamental SINAD estimator, not instrument calibration.

    Frequency is estimated within +/-10Hz of the configured test tone. Only that
    fundamental is removed; in-band harmonics remain in the residual. Crop 50ms
    at each edge after zero-phase filtering to exclude filter transients.
    """
    np, _, signal, optimize = dependencies()
    low, high = bandwidth
    if not 0 < low < frequency - 10 < frequency + 10 < high < rate / 2:
        raise ValueError("Tone search band must be inside the analysis bandwidth and below Nyquist")
    samples = np.asarray(pcm, dtype=float) / 32768
    base = dict(valid=False, reason="", sinad_db=None, lower_bound=False, tone_hz=None,
                rms_dbfs=None, tone_fraction=None, clipped_fraction=0.0)
    if len(samples) < rate * .25 or not np.all(np.isfinite(samples)):
        return {**base, "reason": "short_or_invalid_audio"}
    base["clipped_fraction"] = float(np.mean(np.abs(samples) >= 32760 / 32768))
    if base["clipped_fraction"] > .001:
        return {**base, "reason": "clipped_audio"}
    rms = float(np.sqrt(np.mean(samples ** 2)))
    base["rms_dbfs"] = 20 * math.log10(max(rms, 1e-15))
    if base["rms_dbfs"] < -60:
        return {**base, "reason": "muted_or_low_level_audio"}
    sos = signal.butter(6, bandwidth, btype="bandpass", fs=rate, output="sos")
    filtered = signal.sosfiltfilt(sos, samples - np.mean(samples))
    trim = round(.05 * rate)
    filtered = filtered[trim:-trim]
    times = np.arange(len(filtered)) / rate
    total = float(np.mean(filtered ** 2))
    if total < 1e-15:
        return {**base, "reason": "no_in_band_audio"}
    frequencies = np.fft.rfftfreq(len(filtered), 1 / rate)
    spectrum = np.abs(np.fft.rfft(filtered * np.hanning(len(filtered))))
    eligible = np.flatnonzero(np.abs(frequencies - frequency) <= 10)
    peak = float(frequencies[eligible[np.argmax(spectrum[eligible])]])

    def fit(hz):
        matrix = np.column_stack((np.sin(2 * np.pi * hz * times), np.cos(2 * np.pi * hz * times), np.ones(len(times))))
        weights = np.linalg.lstsq(matrix, filtered, rcond=None)[0]
        return float(np.mean((filtered - matrix @ weights) ** 2))

    step = rate / len(filtered)
    result = optimize.minimize_scalar(fit, bounds=(max(frequency - 10, peak - step), min(frequency + 10, peak + step)),
                                      method="bounded", options={"xatol": 1e-6})
    residual = max(float(result.fun), 1e-15)
    fraction = max(0.0, min(1.0, 1 - residual / total))
    base.update(tone_hz=float(result.x), tone_fraction=fraction)
    if not result.success or fraction < .05:
        return {**base, "reason": "test_tone_not_detected"}
    value = 10 * math.log10(total / residual)
    return {**base, "valid": True, "sinad_db": min(80.0, value), "lower_bound": value > 80}
