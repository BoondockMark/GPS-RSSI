"""Reproducible windowed SINAD analysis and standalone reports over PCM WAV."""

import csv
from datetime import timedelta
from html import escape
import hashlib
import json
from pathlib import Path
import statistics
import uuid
import wave

from .audio_signal import dependencies, measure_sinad
from .measurements import finite, timestamp, utc_now


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def render_report(rows, report):
    values = [r["sinad_db"] for r in rows if r["valid"]]
    seconds = max((r["end_seconds"] for r in rows), default=1)
    dots = []
    for row in rows:
        if row["valid"]:
            x = 45 + 700 * row["start_seconds"] / seconds
            y = 225 - row["sinad_db"] / 80 * 200
            dots.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#036985"><title>' +
                        escape(f"{row['start_seconds']:.2f}s: {row['sinad_db']:.2f} dB") + '</title></circle>')
    fields = ("start_seconds", "end_seconds", "estimated_utc", "sinad_db", "tone_hz", "rms_dbfs", "reason")
    table = "".join("<tr>" + "".join("<td>" + escape(str(r.get(k) if r.get(k) is not None else "—")) + "</td>" for k in fields) + "</tr>" for r in rows)
    typical = f"{statistics.median(values):.1f} dB median" if values else "No valid tone measurements"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audio-path SINAD report</title><style>body{{font:16px system-ui;color:#172b3a;background:#f3f6fa;max-width:1100px;margin:auto;padding:1.5rem}}p{{line-height:1.6}}svg{{min-width:600px;width:100%;background:white}}.scroll{{overflow:auto}}table{{border-collapse:collapse;background:white;width:100%;font-size:.85rem}}th,td{{padding:.6rem;text-align:left;border-bottom:1px solid #ccd5dd;white-space:nowrap}}.metric{{font-size:1.5rem;font-weight:700}}</style></head><body>
<h1>Audio-path SINAD</h1><p>{escape(report['station'])} · {len(values)} valid / {len(rows)} analyzed windows</p><p class="metric">{typical}</p>
<p>End-to-end audio-path estimate, including the transmitter and any Bluetooth codecs. This is not calibrated receiver sensitivity.
Analysis bandwidth: {report['bandwidth_hz'][0]:g}–{report['bandwidth_hz'][1]:g} Hz; expected test tone: {report['frequency_hz']:g} Hz.
Invalid windows have no SINAD value. Reported 80 dB values can be lower bounds; inspect JSON/CSV flags.</p>
<div class="scroll"><svg viewBox="0 0 780 280" role="img" aria-label="SINAD in dB versus recorded audio time in seconds">
<line x1="45" y1="25" x2="45" y2="225" stroke="#64748b"/><line x1="45" y1="225" x2="745" y2="225" stroke="#64748b"/>
<text x="5" y="30">80 dB</text><text x="10" y="230">0 dB</text><text x="45" y="260">0 seconds</text><text x="650" y="260">{seconds:.1f} seconds</text>{''.join(dots)}</svg></div>
<p>Dots are individual valid measurements; no lines bridge missing audio. The horizontal axis is captured sample time.
Bluetooth captures omit delivery gaps instead of inventing silence; windows crossing recorded boundaries are invalid.
UTC values, when present, are estimates from host receipt timing or a user-supplied WAV start time.</p>
<div class="scroll"><table><thead><tr><th>Start (s)</th><th>End (s)</th><th>Estimated UTC</th><th>SINAD (dB)</th><th>Tone (Hz)</th><th>RMS (dBFS)</th><th>Invalid reason</th></tr></thead><tbody>{table}</tbody></table></div>
<p>Keep receive level, bandwidth, noise reduction and audio gain fixed. Establish a strong-signal baseline before comparing field tests. The CSV and JSON retain all windows and analysis settings.</p></body></html>'''


def analyze_wav(path, output, *, frequency=1000, window=1, bandwidth=(300, 3000), station=None, started_at=None):
    if not finite(window) or not .25 <= window <= 10:
        raise ValueError("Analysis window must be between 0.25 and 10 seconds")
    np, _, _, _ = dependencies()
    path, output = Path(path).expanduser().resolve(), Path(output).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    manifest = None
    sibling = path.with_name("report.json")
    if sibling.exists():
        candidate = json.loads(sibling.read_text(encoding="utf-8"))
        if candidate.get("kind") == "audio_capture":
            manifest = candidate
    digest = file_hash(path)
    if manifest and manifest.get("wav_sha256") != digest:
        raise ValueError("Capture metadata does not match this WAV file; analysis canceled")
    if started_at:
        timestamp(started_at)
    rows = []
    with wave.open(str(path), "rb") as wav:
        rate, frames = wav.getframerate(), wav.getnframes()
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
            raise ValueError("SINAD input must be uncompressed 16-bit mono PCM WAV")
        if not 8000 <= rate <= 192000:
            raise ValueError("Unsupported WAV sample rate")
        measure_sinad([], rate, frequency=frequency, bandwidth=bandwidth)  # Validate limits before output.
        segments = manifest.get("segments", []) if manifest else []
        if manifest and manifest.get("sample_rate") != rate:
            raise ValueError("Capture sample rate differs from WAV")
        previous = 0
        for segment in segments:
            start, end = segment["start_sample"], segment["end_sample"]
            if not 0 <= previous <= start <= end <= frames:
                raise ValueError("Invalid capture segment sample bounds")
            previous = end
            timestamp(segment["estimated_started_at"])
        count, start = round(window * rate), 0
        while start < frames:
            pcm = np.frombuffer(wav.readframes(count), dtype="<i2")
            if not len(pcm):
                break
            end = start + len(pcm)
            matching = [s for s in segments if s["start_sample"] <= start and end <= s["end_sample"]]
            reason = ""
            estimated = None
            if manifest:
                if manifest.get("state") != "completed":
                    reason = "capture_not_completed"
                elif len(matching) != 1:
                    reason = "audio_boundary_or_delivery_gap"
                else:
                    s = matching[0]
                    estimated = (timestamp(s["estimated_started_at"]) + timedelta(seconds=(start - s["start_sample"]) / rate)).isoformat()
            elif started_at:
                estimated = (timestamp(started_at) + timedelta(seconds=start / rate)).isoformat()
            result = (dict(valid=False, reason=reason, sinad_db=None, lower_bound=False, tone_hz=None,
                           rms_dbfs=None, tone_fraction=None, clipped_fraction=None) if reason else
                      measure_sinad(pcm, rate, frequency=frequency, bandwidth=bandwidth))
            rows.append(dict(start_sample=start, end_sample=end, start_seconds=start / rate,
                             end_seconds=end / rate, estimated_utc=estimated, **result))
            start = end
    report = dict(kind="sinad_analysis", id=str(uuid.uuid4()), created_at=utc_now(), input=str(path),
                  input_sha256=digest, station=station or (manifest or {}).get("station", "receiver"),
                  capture_id=(manifest or {}).get("id"), sample_rate=rate, frequency_hz=frequency,
                  frequency_search_hz=10, bandwidth_hz=list(bandwidth), window_seconds=window,
                  method="bandpass_butterworth6_zero_phase_fitted_fundamental_v1", edge_trim_seconds=.05,
                  min_rms_dbfs=-60, min_tone_fraction=.05, maximum_clipped_fraction=.001,
                  upper_limit_db=80, meaning="Uncalibrated end-to-end audio-path SINAD estimate",
                  capture_metadata=manifest, window_count=len(rows), valid_windows=sum(r["valid"] for r in rows),
                  windows=rows)
    page = render_report(rows, report)
    fields = ("start_sample", "end_sample", "start_seconds", "end_seconds", "estimated_utc", "valid",
              "reason", "sinad_db", "lower_bound", "tone_hz", "rms_dbfs", "tone_fraction", "clipped_fraction")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    created = []
    try:
        for name, content in (("report.json", json.dumps(report, indent=2, allow_nan=False)), ("report.html", page)):
            path = output / name
            with path.open("x", encoding="utf-8") as stream:
                created.append(path)
                stream.write(content)
        path = output / "measurements.csv"
        with path.open("x", encoding="utf-8", newline="") as stream:
            created.append(path)
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        try:
            output.rmdir()
        except OSError:
            pass
        raise
    return report
