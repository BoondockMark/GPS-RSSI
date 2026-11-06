"""Benshi audio framing and platform RFCOMM streams, separate from BLE control."""

import asyncio
import os
import re
import socket
from xml.etree import ElementTree as ET

VENDOR_AUDIO = "39144315-32fa-40db-85ed-fbfeba2d86e6"
GENERIC_AUDIO = "00001203-0000-1000-8000-00805f9b34fb"
AUDIO_SERVICES = {VENDOR_AUDIO, GENERIC_AUDIO}
END_AUDIO = b"\x7e\x01" + b"\x00" * 8 + b"\x7e"


def mac_address(value):
    compact = value.replace(":", "").replace("-", "")
    if not re.fullmatch(r"[0-9a-fA-F]{12}", compact):
        raise ValueError("Bluetooth Classic audio needs the radio's 48-bit MAC address")
    return ":".join(compact[i:i + 2].upper() for i in range(0, 12, 2))


def uuid(value):
    value = value.lower()
    if value.startswith("0x"):
        return f"{int(value, 16):08x}-0000-1000-8000-00805f9b34fb"
    return value


def parse_sdp(text):
    text = re.sub(r"<\?xml[^>]*\?>", "", text)
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text)
    root = ET.fromstring("<records>" + text + "</records>")
    out = []
    for record in root.iter("record"):
        attributes = {int(a.attrib["id"], 0): a for a in record.findall("attribute")}
        services = [uuid(n.attrib["value"]) for n in attributes.get(1, ET.Element("missing")).iter("uuid")]
        name = attributes.get(0x100, ET.Element("missing")).find("text")
        for sequence in attributes.get(4, ET.Element("missing")).iter("sequence"):
            protocol, channel = sequence.find("uuid"), sequence.find("uint8")
            if protocol is not None and uuid(protocol.attrib["value"]) == uuid("0x0003") and channel is not None:
                number = int(channel.attrib["value"], 0)
                if not 1 <= number <= 30:
                    raise ValueError("SDP returned an invalid RFCOMM channel")
                out.append(dict(channel=number, service_uuids=services,
                                name=name.attrib.get("value", "") if name is not None else ""))
    return out


async def windows_services(address):
    try:
        from winrt.windows.devices.bluetooth import BluetoothDevice
    except ImportError as exc:
        raise RuntimeError('Windows audio requires: pip install -e ".[bluetooth,audio]"') from exc
    device = await BluetoothDevice.from_bluetooth_address_async(int(mac_address(address).replace(":", ""), 16))
    if device is None:
        raise RuntimeError("Windows could not open the paired Bluetooth Classic device")
    try:
        result = await device.get_rfcomm_services_async()
        if int(result.error) != 0:
            raise RuntimeError(f"Windows RFCOMM service discovery failed: {result.error}")
        return device, list(result.services)
    except BaseException:
        device.close()
        raise


async def discover_services(address, timeout=20):
    address = mac_address(address)
    if os.name == "nt":
        device, services = await asyncio.wait_for(windows_services(address), timeout)
        try:
            return [dict(channel=int(s.connection_service_name), service_uuids=[str(s.service_id.uuid).lower()], name="")
                    for s in services]
        finally:
            for service in services:
                service.close()
            device.close()
    if not hasattr(socket, "AF_BLUETOOTH"):
        raise RuntimeError("This platform lacks RFCOMM socket support; use Windows WinRT or Linux BlueZ")
    try:
        process = await asyncio.create_subprocess_exec("sdptool", "browse", "--xml", address,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError("Install BlueZ (including sdptool) to discover the audio service") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError("Bluetooth SDP discovery failed: " + stderr.decode(errors="replace").strip())
    return parse_sdp(stdout.decode("utf-8"))


def select_audio_service(services, channel=None):
    audio = [s for s in services if set(s["service_uuids"]) & AUDIO_SERVICES]
    if channel is not None:
        audio = [s for s in audio if s["channel"] == channel]
    elif any(VENDOR_AUDIO in s["service_uuids"] for s in audio):
        audio = [s for s in audio if VENDOR_AUDIO in s["service_uuids"]]
    channels = {s["channel"] for s in audio}
    if len(channels) != 1:
        raise ValueError("Select one advertised BS AOC / Generic Audio RFCOMM channel using audio-services and --audio-channel")
    return audio[0]


def audio_packet(data):
    payload = b"\x00" + data
    escaped = bytearray()
    for value in payload:
        if value in (0x7d, 0x7e):
            escaped.extend((0x7d, value ^ 0x20))
        else:
            escaped.append(value)
    return b"\x7e" + bytes(escaped) + b"\x7e"


class AudioParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > 65536:
            self.buffer.clear()
            raise ValueError("Audio framing buffer overflow")
        frames = []
        while self.buffer:
            try:
                start = self.buffer.index(0x7e)
            except ValueError:
                self.buffer.clear()
                break
            del self.buffer[:start]
            try:
                end = self.buffer.index(0x7e, 1)
            except ValueError:
                break
            if end == 1:
                del self.buffer[0]
                continue
            encoded = self.buffer[1:end]
            del self.buffer[:end + 1]
            decoded = bytearray()
            index = 0
            while index < len(encoded):
                value = encoded[index]
                if value == 0x7d:
                    index += 1
                    if index == len(encoded):
                        raise ValueError("Truncated audio escape sequence")
                    value = encoded[index] ^ 0x20
                decoded.append(value)
                index += 1
            frames.append((decoded[0], bytes(decoded[1:])))
        return frames


class SocketStream:
    def __init__(self):
        self.socket = None

    async def connect(self, address, service):
        self.socket = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self.socket.setblocking(False)
        await asyncio.get_running_loop().sock_connect(self.socket, (address, service["channel"]))

    async def write(self, data):
        await asyncio.get_running_loop().sock_sendall(self.socket, data)

    async def read(self):
        return await asyncio.get_running_loop().sock_recv(self.socket, 4096)

    async def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None


class WindowsStream:
    """Use WinRT because standard Windows Python lacks AF_BLUETOOTH sockets."""
    def __init__(self):
        self.device = self.socket = self.reader = self.writer = None
        self.services = []

    async def connect(self, address, service):
        from winrt.windows.networking.sockets import StreamSocket, SocketProtectionLevel
        from winrt.windows.storage.streams import DataReader, DataWriter, InputStreamOptions
        self.device, self.services = await windows_services(address)
        matches = [s for s in self.services if int(s.connection_service_name) == service["channel"]
                   and str(s.service_id.uuid).lower() in AUDIO_SERVICES]
        if not matches:
            raise RuntimeError("The selected Windows audio service disappeared")
        self.socket = StreamSocket()
        s = matches[0]
        await self.socket.connect_with_protection_level_async(s.connection_host_name, s.connection_service_name,
                                        SocketProtectionLevel.BLUETOOTH_ENCRYPTION_ALLOW_NULL_AUTHENTICATION)
        self.reader = DataReader(self.socket.input_stream)
        self.reader.input_stream_options = InputStreamOptions.PARTIAL
        self.writer = DataWriter(self.socket.output_stream)

    async def read(self):
        count = await self.reader.load_async(4096)
        buffer = bytearray(count)
        self.reader.read_bytes(buffer)
        return bytes(buffer)

    async def write(self, data):
        self.writer.write_bytes(data)
        count = await self.writer.store_async()
        if count != len(data):
            raise OSError("Incomplete Windows Bluetooth audio write")

    async def close(self):
        errors = []
        for name in ("reader", "writer", "socket", "device"):
            obj = getattr(self, name)
            if obj is not None:
                try:
                    obj.close()
                except Exception as exc:
                    errors.append(str(exc))
                finally:
                    setattr(self, name, None)
        for service in self.services:
            try:
                service.close()
            except Exception as exc:
                errors.append(str(exc))
        self.services = []
        if errors:
            raise RuntimeError("Windows audio cleanup: " + "; ".join(errors))


def new_stream():
    return WindowsStream() if os.name == "nt" else SocketStream()
