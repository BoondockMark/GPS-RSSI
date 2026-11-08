# Bluetooth tones and SINAD

The optional audio workflow prepares or transmits a known test tone, captures a
second radio's received audio, and produces an **end-to-end audio-path SINAD**
report. Transmissions are explicit and bounded; `record`, `start`, `fleet`, and
the dashboard remain read-only toward RF. An explicitly started automatic mode repeats bounded tones until halted.

Published third-party implementations demonstrate this path on the UV-PRO. The
UV-50PRO is audio-capable too, but this adapter requires physical validation on
each model/firmware and host. Tests use simulated Bluetooth streams and do not
establish RF calibration or hardware interoperability.

## Install

In the existing virtual environment:

```sh
python -m pip install -e ".[bluetooth,audio]"
```

NumPy/SciPy provide analysis and PyAV/FFmpeg provides SBC. Windows also installs
PyWinRT Bluetooth/sockets/streams projections. Linux requires BlueZ, including
`sdptool` (`sudo apt install bluez`). No sound card, microphone, PyAudio, or system
headset routing is required. The initial adapter targets Windows and Linux.

Pair the radio using Bluetooth Classic as well as enabling BLE access. Close
other radio-control applications; run audio commands independently of the normal
logger on that radio. This version uses the supplied Classic MAC address for BLE
control too; hosts/radios requiring different address mappings need validation.

## Prepare without RF

```sh
gps-rssi tone --output data/tone-check
```

This opens no Bluetooth connection. The new directory contains `tone.wav`,
`tone.sbc`, and `report.json`. Defaults are 1 kHz, 3 seconds, −18 dBFS peak, 32 kHz
mono PCM, and 20ms edge ramps. Bounds: `--frequency` 400–2500 Hz, `--duration`
0.5–10 seconds, and `--level-dbfs` −36 to −6. Digital level does not set RF
deviation. SBC encoding is validated before any connection: 16 blocks,
8 subbands, bitpool 16. Existing output directories are refused.

## Discover the audio service

```sh
gps-rssi audio-services --address RADIO_MAC
gps-rssi audio-services --address RADIO_MAC --json
```

Use a Classic MAC (`AA:BB:CC:DD:EE:FF`). Discovery does not transmit. The preferred
service is BS AOC UUID `39144315-32fa-40db-85ed-fbfeba2d86e6`; Generic Audio UUID
`00001203-0000-1000-8000-00805f9b34fb` is the fallback. Control/SPP and KISS/TNC
are separate services. The software discovers the channel rather than assuming
one. `--audio-channel N` selects among advertised audio services; an explicit
command/TNC channel is still refused. Ambiguous audio channels require selection.

Windows uses WinRT streams because native Python lacks the Linux Bluetooth
socket API. Linux uses RFCOMM sockets. BLE status access is independent of audio.

## Capture on the receiving radio, then transmit on the other

Start the receiver first:

```sh
gps-rssi audio-receive --address RECEIVER_MAC --station mobile-a --duration 30 --output data/rx-test-01
```

Capture lasts 1–600 wall-clock seconds. It writes 32 kHz mono `audio.wav` and a
sidecar `report.json`; it never sends audio or end-of-audio frames. It checks BLE
TX state before and during capture. Local TX, transmit echo, unknown frames,
decode failure, or disconnection aborts capture while retaining decoded audio.
Receive frame types `0x00` and `0x03` are accepted; TX echo `0x09` is rejected.

Set the transmitter's channel, power, bandwidth and access tone manually, then:

```sh
gps-rssi tone --output data/tx-test-01 --address TRANSMITTER_MAC --transmit --duration 3
```

Both `--transmit` and `--address` are required for RF. An address without that
flag is an error; the explicit command does not prompt again. Current radio
settings are used. An already transmitting radio is refused without sending a
stop command. Each output folder must be new.

Writes contain at most six 4ms SBC frames, paced to the sample clock. Slow writes
or excessive scheduling delay abort instead of bursting to catch up. Tone length
is capped at 10 seconds; failed transmissions are never automatically retried.

After audio connection, completion/error/cancellation attempts two end-of-audio
messages, waits 1.5 seconds for drain, checks BLE TX state up to three times, and
disconnects both channels. Repeated asyncio cancellation does not skip cleanup.
Inspect `stop_attempted`, `stop_verified`, and `cleanup_errors` in the report.
Unverified stop produces an error and an instruction to check the radio.

Software cannot guarantee de-keying after power loss, force-kill, lost Bluetooth,
or radio firmware failure. Keep the radio's own transmit timeout enabled and
observe it during initial tests. Ctrl-C requests cleanup; force-kill is not a
normal stop method. Outputs and final operation state are retained on failure.

## Automatic fixed/mobile cycle

In the workspace Audio panel, select the transmitting radio, enable **Automatic
mode**, then review and start it. The saved station role chooses the offset.
**Halt automatic mode** in the persistent banner or Activity stops the cycle,
including while waiting for its first tone. The same mode is available from CLI:

```sh
gps-rssi tone --transmit --address RADIO_MAC --automatic fixed --output data/auto-fixed-01
gps-rssi tone --transmit --address RADIO_MAC --automatic mobile --output data/auto-mobile-01
```

Each station sends a **1 kHz, 5-second tone every 20 seconds**, overriding the
single-tone frequency and duration settings. Fixed arms for the next minute and
starts at :00, :20, :40. Mobile arms for the next minute plus ten seconds and
starts at :10, :30, :50. In each 20-second period this gives five seconds of local
TX, five seconds of guard time, five seconds of remote TX/local RX, then five
seconds of guard time. The radio listens throughout its fifteen seconds off;
the basic tone mode does not save received audio or RSSI. Use **Field survey**
to record RSSI and enabled GPS between tones on the same control connection,
or add `--record-config YOUR_RADIO.radio.toml` to the automatic CLI command.
The separate logger/capture cannot run on that same radio simultaneously.

After connection, an advancing, timezone-aware radio GPS timestamp is preferred,
regardless of the profile's GPS logging setting. Unlocked, cached/nonadvancing,
invalid, or unavailable GPS falls back to the computer clock. Activity and
`report.json` identify the source and fallback reason. Keep computer clocks
synchronized when using fallback. The initial clock reference is held with a
monotonic timer for the session, so later computer clock adjustments do not
move the cycle. It is not switched or resynchronized mid-session.

Ctrl-C or the workspace halt control stops scheduling and performs bounded
end-of-audio cleanup with TX verification. Each completed tone also checks TX
is off before proceeding. Disconnection, missed deadlines, failed writes, or
unverified TX stop halt the mode without retrying or catching up. It never
restarts automatically after the app closes. Bluetooth buffering, GPS timestamp
resolution, and host scheduling limit actual over-the-air synchronization;
validate both radios' RF timing while stationary before field use.

## Analyze

```sh
gps-rssi sinad --input data/rx-test-01/audio.wav --output data/sinad-test-01
```

Open `report.html` for an offline scatter plot, counts, median, and per-window
table. `measurements.csv` and `report.json` retain every window and validity
reason, timing, fitted frequency, signal level, and analysis configuration.
Input audio is unchanged. Defaults: 1 kHz expected tone, 1-second windows,
300–3000 Hz bandwidth. Options: `--frequency`, `--window` (0.25–10 seconds),
`--band-low`, `--band-high`, and `--station`.

External 16-bit mono PCM WAV is supported too. `--started-at` supplies its sample
zero timestamp with UTC offset; otherwise UTC remains unknown. For app captures,
the sibling report's SHA-256 must match the WAV before using its metadata.

The estimator removes DC, applies a sixth-order Butterworth bandpass forward and
backward, trims 50ms at each edge, finds the tone within ±10 Hz, and fits sine,
cosine and a constant. It reports `10 log10(total in-band power / residual power)`.
Only the fundamental is removed; in-band harmonics remain distortion. Use the
same bandwidth and estimator version when comparing readings.

Quality gates reject RMS below −60 dBFS, more than 0.1% near-full-scale samples,
or a fitted fundamental explaining less than 5% of in-band power. Short windows,
silence, missing tones, clipping, incomplete captures, and windows crossing
capture boundaries have **no SINAD value**, rather than zero. Values above
80 dB are capped and flagged as lower bounds. These gates are estimator settings,
not calibrated RF reception thresholds.

The capture sidecar preserves station/run identity, SHA-256, decoded sample counts,
and audio segments. End markers or host delivery gaps above 250ms split segments;
gaps are not filled with invented silence. Delivery gaps can reflect buffering
or scheduling and do not prove RF/Bluetooth packet loss. Charts use captured
sample time; estimated UTC retains segment jumps. UTC is based on host receipt
time and includes codec/Bluetooth/scheduling uncertainty, not sample-accurate RF
timing. Failed/canceled captures are retained for diagnosis but have no valid
analysis windows.

## Measurement limits and bench acceptance

SINAD includes both Bluetooth codec paths, the transmitter, RF link, and receiver.
It is not calibrated receiver sensitivity. Establish a strong-signal baseline
with the same equipment/settings before interpreting weak-signal tests. Disable
AI noise reduction and keep volume/gain/bandwidth/squelch settings fixed. An RF
generator or service monitor is still needed for sensitivity at a specified
input level and modulation deviation.

This first implementation exports standalone audio/SINAD reports. It does not
attach results to SQLite GPS/RSSI samples or add SINAD survey-map layers. Capture
timing must be characterized before conservative GPS association is implemented.

Physical acceptance:

1. Verify Classic pairing, BLE status, and advertised audio services on each host/radio.
2. Prepare and inspect the offline tone before connecting to RF hardware.
3. Capture a known received tone and confirm format, frame handling, and absence of TX echoes.
4. Observe one short transmitted tone, its modulation level, and actual de-keying.
5. Repeat with Ctrl-C and a controlled link interruption; check the final report
   and actual TX state, including the radio's own timeout behavior.
6. Compare a strong-signal result to an independent instrument or known distortion
   source before field measurements.

Software checks cover theoretical harmonic/noise ratios, fractional tone frequency,
invalid audio, SBC round trips, framing, service selection, mocked Windows stream
APIs, pacing, cancellation, stop failures, capture, hashes, and segment-aware reports.
Native Windows radio operation and RF calibration remain physical acceptance work.

References: [pinned Benlink example](https://github.com/khusmann/benlink/blob/c4b9d22112402ea193d376f412bc028d1778fdb4/src/benlink/examples/audiotransmit.py),
[HTCommander audio engine](https://github.com/Ylianst/HTCommander/blob/main/src/lib/radio/audio_engine.dart),
[UV-PRO over-the-air tests](https://ylianst.github.io/HTCommanderSite/blogs/dart-over-the-air-findings.html),
[Microsoft RFCOMM API](https://learn.microsoft.com/en-us/windows/uwp/devices-sensors/send-or-receive-files-with-rfcomm).

## Recording during automatic tones

Field survey sessions share one BLE control connection for tone guards and
recording. Samples are collected about once per second in quiet intervals,
leaving a three-second guard before each tone. Sampling does not run during
local transmission or stop verification. Each sample reads status, current
channel frequencies, and enabled/locked GPS without carrying forward an old fix.
If a request times out or recording fails, the cycle halts and performs TX cleanup;
it does not reconnect or silently continue without recording. Partial data remains.

`--record-config` must identify the same address and station role as the tone
command. The normal recording database lock prevents a second writer. Saved
samples use the existing schema and appear in Surveys & exports. Received audio
is not recorded by field mode.

`gps-rssi field-check --config YOUR_RADIO.radio.toml --output data/check-01`
checks codecs, destination access/integrity, control, audio connection, and the
clock without sending audio or PTT. The workspace requires a successful check
within two minutes and matching radio settings before its field review. Computer
clock fallback is shown as needing attention, and remains usable after review.
Connections and clocks are checked again when the session actually starts.
