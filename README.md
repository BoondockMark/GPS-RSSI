# GPS-RSSI

Collect measurements for mapping both directions of an RF repeater link around
La Habra Heights. This first milestone is a **read-only BTECH diagnostic logger**.
It supports Benlink BLE and Bluetooth Classic RFCOMM, local SQLite storage,
JSONL replay, and CSV export. It never requests PTT, sends RF packets, or changes
radio settings. Bluetooth requests themselves are transmitted to the radio.

The Kenwood ADC adapter, event pairing between stations, calibration, and mapping
are planned; they are not implemented in this milestone.

## Install

Python 3.11 or later and Git on Linux are the initial target, including Raspberry Pi OS
64-bit and mini-PCs. Start from the existing checkout:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[bluetooth]'
gps-rssi --help
```

For replay, export, and tests without Bluetooth dependencies, use
`python -m pip install -e .` instead. No API keys or Internet connection are
required during logging. Bluetooth operation needs a local adapter, BlueZ and a
running Bluetooth service on Linux. Pair/trust the radio using your operating
system's Bluetooth tools (for example `bluetoothctl`). Stop other radio-control
apps during the initial test. Run `record` on the computer beside the radio.

## Try without hardware

The example contains **synthetic measurements**, not a coverage survey:

```sh
gps-rssi replay examples/mobile.jsonl --db data/demo.sqlite --station demo-mobile
gps-rssi export --db data/demo.sqlite --output data/demo.csv
python -m unittest discover -s tests -v
```

Each replay appends a distinct session; repeating it duplicates the input as a
new session. Export refuses to overwrite an existing file. The `source` column
distinguishes replay from live Bluetooth data.

## First radio test

Start with one channel selected and scanning/dual watch disabled manually.
Use the actual device address or platform UUID from Bluetooth pairing:

```sh
gps-rssi record --address AA:BB:CC:DD:EE:FF --transport ble \
  --station mobile-uvpro --db data/uvpro.sqlite \
  --label 'UV-PRO; firmware and antenna notes' --max-samples 30
```

BLE is the default. For Bluetooth Classic, discover the radio's **command**
service RFCOMM channel and pass it explicitly. Do not substitute a KISS/TNC or
audio service. For example, **only if discovery reports command channel 1**:

```sh
gps-rssi record --address AA:BB:CC:DD:EE:FF --transport rfcomm \
  --rfcomm-channel 1 --station mobile-uv50pro --db data/uv50pro.sqlite \
  --gps --interval 1 --timeout 10
```

Omit `--max-samples` to log until Ctrl-C. It counts successful status samples,
not connection attempts; a disconnected radio is retried until stopped.
`--reconnect-delay` defaults to 5 seconds. Every connection and command has a
timeout. Samples commit individually; interruption retains completed samples.
Connection diagnostics and device information are saved in the `events` table.
The software does not change system clock, radio firmware, or radio configuration.

`--gps` requests a position only when status reports GPS lock. Position retrieval
failure retains the status measurement without coordinates and reconnects with
fresh request state. Leave GPS off for the first RSSI-only test. Device/firmware
support for GPS and extended status must be confirmed on each radio. Benlink
lists UV-PRO; HTCommander lists UV-PRO and UV-50Pro, but this adapter has **not
yet been validated against either physical radio**.

If an older Benlink was already installed and the logger reports a missing GPS
API, run `python -m pip install --force-reinstall -e '.[bluetooth]'` in this
virtual environment. The upstream source revision shares its version number
with the older PyPI release, so a regular install can retain the older code.

## Measurement meaning

- BTECH strength is stored as `benshi_relative_0_100`, **not dBm or calibrated
  percent signal power**. Benlink scales a four-bit field (16 levels) by 100/15.
  This is radio RF strength, not Bluetooth RSSI. Decoded replies are retained
  as JSON; these are not original wire bytes.
- Mobile receive samples are labeled `downlink`; repeater-role receive samples
  are labeled `uplink`. Idle/TX samples remain `unqualified`, with their state
  retained. These labels describe receiver direction, not confirmed source
  identity or successful two-way communication. No event pairing is done yet.
- `--role repeater` changes labeling for a BTECH used as a site monitor. It does
  **not** enable an NXR-1800 interface or make a monitor's reading equivalent to
  the repeater receiver's reading.
- Squelch and receive flags are retained separately; low/zero RSSI is not dropped.
  RSSI alone does not establish usable voice reception.
- Host UTC status-request and receipt times are recorded, along with the radio's
  GPS timestamp and accuracy in the decoded payload. GPS is queried after status,
  so those observations are not simultaneous. No previous fix is carried forward.
  Keep host clocks synchronized before comparing logs across stations. Inspect
  GPS age and accuracy before mapping; the logger does not yet filter stale fixes.
- Record model, firmware, antenna, mounting, frequency, bandwidth, and power
  settings in `--label`. The status channel ID is not a frequency. Compare the
  two BTECH models separately until their strength scales are characterized.

## Bench acceptance checklist

1. Record device information and 30 status samples from each BTECH model.
2. With a known received signal, check strength changes, RX/squelch flags, and
   idle behavior. Check TX flags during a separately controlled transmission.
3. Enable GPS and confirm coordinates, timestamp, lock loss, and accuracy.
4. Disconnect/reconnect Bluetooth; confirm earlier samples remain and a connection
   error appears in `events`.
5. Export CSV and inspect values before a stationary two-station experiment.

Software tests exercise polling, timeouts, reconnection, cancellation, validation,
storage, replay, and export with fake radios. They do not establish RF calibration,
Bluetooth interoperability, firmware compatibility, or real sampling rate.

## Next hardware adapter: Kenwood NXR-1800

The supplied service manual identifies CONTROL I/O pin 1 as a 0–3.3 V RSSI
output **after the TL501/TL502 modification** (section 2.4.2, page 1-12; pin
specification page 1-27). Pin 17 provides squelch control with a roughly 5 V
pull-up (page 1-28). A Raspberry Pi requires an external ADC for RSSI and suitable
interfacing for the squelch signal. Grounding, loading, protection, polarity, and
voltage-to-dBm calibration need bench verification before use.

## Protocol references

- [Benlink](https://github.com/khusmann/benlink), pinned to commit
  `c4b9d22112402ea193d376f412bc028d1778fdb4`. The PyPI 0.1.1 release lacks
  `get_position`; this source revision includes it.
- [Benlink RSSI encoding](https://github.com/khusmann/benlink/blob/main/src/benlink/protocol/command/status.py).
- [HTCommander](https://github.com/Ylianst/HTCommander), including UV-50Pro support.
- [HTCommander command protocol](https://github.com/Ylianst/HTCommander/blob/HEAD/docs/blogs/radio-command-protocol.md).
