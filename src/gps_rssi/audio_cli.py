"""Audio command arguments and lazy dispatch; ordinary logging stays read-only."""

import argparse
import asyncio
import json
from pathlib import Path
import wave

COMMANDS = {"tone", "audio-services", "audio-receive", "sinad"}


def channel_number(value):
    value = int(value)
    if not 1 <= value <= 30:
        raise argparse.ArgumentTypeError("RFCOMM channel must be between 1 and 30")
    return value


def add_commands(commands):
    tone = commands.add_parser("tone", help="Prepare a test tone; RF transmission requires --transmit and --address.",
        description="Generate a 32 kHz mono test tone and SBC frames. Transmits only when --transmit is supplied.",
        epilog="Example: gps-rssi tone --output data/tone-check. To transmit, also supply --transmit --address RADIO_MAC. "
               "The radio uses its current channel/power. The audio service is discovered separately from BLE control. "
               "Maximum tone length: 10 seconds. No transmission retries or automatic scheduling.")
    tone.add_argument("--output", type=Path, required=True, help="New directory for tone.wav, tone.sbc and report.json")
    tone.add_argument("--frequency", type=float, default=1000, help="Tone frequency, 400–2500 Hz (default: 1000)")
    tone.add_argument("--duration", type=float, default=3, help="Tone duration, 0.5–10 seconds (default: 3)")
    tone.add_argument("--level-dbfs", type=float, default=-18, help="Peak digital level, -36 to -6 dBFS (default: -18); does not set RF deviation")
    tone.add_argument("--transmit", action="store_true", help="Explicitly transmit this tone over RF using the connected radio")
    tone.add_argument("--address", help="Radio Bluetooth Classic MAC address; requires --transmit")
    tone.add_argument("--audio-channel", type=channel_number, help="Select an advertised audio RFCOMM channel; otherwise discover it")
    tone.add_argument("--connect-timeout", type=float, default=30, help="Connection timeout in seconds (default: 30)")
    services = commands.add_parser("audio-services", help="List a radio's Bluetooth Classic services without transmitting.")
    services.add_argument("--address", required=True, help="Paired radio Bluetooth Classic MAC address")
    services.add_argument("--json", action="store_true", help="Print structured service metadata")
    receive = commands.add_parser("audio-receive", help="Capture receive-only Bluetooth SBC audio as WAV with timing metadata.",
        epilog="Uses BLE to monitor TX state and a separate RFCOMM audio service. Run independently of other control apps "
               "on this radio. No RF transmission is requested. Maximum capture: 600 seconds. Analyze using sinad.")
    receive.add_argument("--address", required=True, help="Receiver Bluetooth Classic MAC address")
    receive.add_argument("--audio-channel", type=channel_number, help="Select an advertised audio RFCOMM channel; otherwise discover it")
    receive.add_argument("--output", type=Path, required=True, help="New directory containing audio.wav and report.json")
    receive.add_argument("--duration", type=float, default=30, help="Wall-clock capture duration, 1–600 seconds (default: 30)")
    receive.add_argument("--station", default="receiver", help="Receiver identity retained in analysis reports")
    receive.add_argument("--connect-timeout", type=float, default=30, help="Connection timeout in seconds (default: 30)")
    sinad = commands.add_parser("sinad", help="Analyze a received test-tone WAV and produce SINAD CSV/JSON/HTML reports.",
        epilog="Input is 16-bit mono PCM WAV. A sibling Bluetooth capture report is verified and used for segment/timing "
               "quality. Muted, clipped, missing-tone, incomplete-capture and boundary-crossing windows have no SINAD value. "
               "Results include codec/transmitter distortion and are not calibrated receiver sensitivity.")
    sinad.add_argument("--input", type=Path, required=True, help="Received WAV file; never transmitted or modified")
    sinad.add_argument("--output", type=Path, required=True, help="New analysis directory")
    sinad.add_argument("--frequency", type=float, default=1000, help="Expected test tone in Hz (default: 1000), searched within ±10 Hz")
    sinad.add_argument("--window", type=float, default=1, help="Analysis window, 0.25–10 seconds (default: 1)")
    sinad.add_argument("--band-low", type=float, default=300, help="Lower analysis bandwidth edge in Hz (default: 300)")
    sinad.add_argument("--band-high", type=float, default=3000, help="Upper analysis bandwidth edge in Hz (default: 3000)")
    sinad.add_argument("--station", help="Override receiver identity")
    sinad.add_argument("--started-at", help="External WAV sample-zero timestamp with UTC offset; capture manifests supply their own timing")


def run_audio(args):
    try:
        if args.command == "audio-services":
            from .audio_transport import AUDIO_SERVICES, discover_services
            services = asyncio.run(discover_services(args.address))
            for service in services:
                service["audio"] = bool(set(service["service_uuids"]) & AUDIO_SERVICES)
            if args.json:
                print(json.dumps(services, indent=2))
            else:
                for service in services:
                    print(f"Channel {service['channel']}: {'AUDIO' if service['audio'] else 'other'} "
                          f"{service['name']} | {', '.join(service['service_uuids'])}")
                if not services:
                    print("No RFCOMM services found. Check Bluetooth Classic pairing and radio availability.")
        elif args.command == "tone":
            from .audio_test import run_tone
            report = run_tone(args.output, frequency=args.frequency, duration=args.duration,
                level_dbfs=args.level_dbfs, transmit=args.transmit, address=args.address,
                audio_channel=args.audio_channel, connect_timeout=args.connect_timeout)
            print(f"Wrote {args.output}: {report['state']}" +
                  (f"; radio stop verified={report['stop_verified']}" if args.transmit else "; files only, no radio connection"))
        elif args.command == "audio-receive":
            from .audio_test import run_receive
            report = run_receive(args.output, address=args.address, audio_channel=args.audio_channel,
                duration=args.duration, station=args.station, connect_timeout=args.connect_timeout)
            print(f"Wrote {args.output}: {report['decoded_samples']} decoded audio samples; analyze audio.wav with sinad")
        else:
            from .sinad import analyze_wav
            report = analyze_wav(args.input, args.output, frequency=args.frequency, window=args.window,
                bandwidth=(args.band_low, args.band_high), station=args.station, started_at=args.started_at)
            print(f"Wrote {args.output}: {report['valid_windows']} valid / {report['window_count']} windows. Open report.html.")
        return 0
    except KeyboardInterrupt:
        print("Audio operation interrupted. Check output/report.json for capture or transmitter cleanup status.")
        return 130
    except wave.Error as exc:
        raise ValueError(f"Invalid WAV file: {exc}") from exc
    except Exception as exc:
        if type(exc).__module__.startswith("av."):
            raise RuntimeError(f"Audio codec error: {exc}") from exc
        raise
