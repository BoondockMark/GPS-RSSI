import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gps_rssi.audio_transport import (AudioParser, END_AUDIO, GENERIC_AUDIO, VENDOR_AUDIO,
                                      WindowsStream, audio_packet, mac_address, parse_sdp, select_audio_service)

SDP = f'''<?xml version="1.0"?>
<record><attribute id="0x0001"><sequence><uuid value="0x1101"/></sequence></attribute>
<attribute id="0x0004"><sequence><sequence><uuid value="0x0003"/><uint8 value="0x01"/></sequence></sequence></attribute></record>
<?xml version="1.0"?><record><attribute id="0x0001"><sequence><uuid value="{VENDOR_AUDIO}"/></sequence></attribute>
<attribute id="0x0100"><text value="BS AOC"/></attribute>
<attribute id="0x0004"><sequence><sequence><uuid value="0x0003"/><uint8 value="0x02"/></sequence></sequence></attribute></record>'''


class AudioTransportTests(unittest.TestCase):
    def test_framing_roundtrips_fragmented_escaped_data_and_multiple_frames(self):
        wire = b"garbage" + audio_packet(b"\x7d\x7e\x00test") + END_AUDIO + b"\x7e\x03RX\x7e\x7e\x09TX\x7e"
        for size in (1, 2, 7, len(wire)):
            parser, messages = AudioParser(), []
            for index in range(0, len(wire), size):
                messages.extend(parser.feed(wire[index:index + size]))
            self.assertEqual(messages, [(0, b"\x7d\x7e\x00test"), (1, b"\0" * 8), (3, b"RX"), (9, b"TX")])

    def test_bad_framing_and_unbounded_payloads_fail_explicitly(self):
        for payload in (b"\x7e\x00\x7d\x7e", b"\x7e" + b"x" * 65536):
            with self.assertRaises(ValueError):
                AudioParser().feed(payload)

    def test_sdp_distinguishes_audio_from_control_and_never_guesses_a_channel(self):
        services = parse_sdp(SDP)
        self.assertEqual(select_audio_service(services)["channel"], 2)
        self.assertEqual(select_audio_service(services, 2)["name"], "BS AOC")
        with self.assertRaises(ValueError):
            select_audio_service(services, 1)
        with self.assertRaises(ValueError):
            select_audio_service([])
        fallback = dict(channel=3, service_uuids=[GENERIC_AUDIO])
        self.assertEqual(select_audio_service([fallback])["channel"], 3)
        with self.assertRaises(ValueError):
            select_audio_service([fallback, dict(channel=4, service_uuids=[GENERIC_AUDIO])])

    def test_mac_address_validation(self):
        self.assertEqual(mac_address("ab-cd-ef-01-23-45"), "AB:CD:EF:01:23:45")
        with self.assertRaises(ValueError):
            mac_address("not-a-mac")


class WindowsTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_windows_uses_advertised_audio_service_and_preserves_byte_stream(self):
        service = Mock(connection_service_name="2", service_id=SimpleNamespace(uuid=VENDOR_AUDIO))
        device, socket, reader, writer = Mock(), Mock(), Mock(), Mock()
        socket.connect_with_protection_level_async = AsyncMock()
        reader.load_async = AsyncMock(return_value=3)
        def fill(buffer):
            buffer[:] = b"abc"
        reader.read_bytes.side_effect = fill
        writer.store_async = AsyncMock(return_value=3)
        modules = {
            "winrt.windows.networking.sockets": SimpleNamespace(StreamSocket=lambda: socket,
                SocketProtectionLevel=SimpleNamespace(BLUETOOTH_ENCRYPTION_ALLOW_NULL_AUTHENTICATION=1)),
            "winrt.windows.storage.streams": SimpleNamespace(DataReader=lambda _: reader, DataWriter=lambda _: writer,
                InputStreamOptions=SimpleNamespace(PARTIAL=1)),
        }
        stream = WindowsStream()
        with patch.dict(sys.modules, modules), patch("gps_rssi.audio_transport.windows_services", AsyncMock(return_value=(device, [service]))):
            await stream.connect("AA:BB:CC:DD:EE:FF", dict(channel=2))
            self.assertEqual(await stream.read(), b"abc")
            await stream.write(b"abc")
            writer.write_bytes.assert_called_once_with(b"abc")
            await stream.close()
            for item in (socket, reader, writer, device, service):
                item.close.assert_called_once()

    async def test_windows_close_releases_remaining_resources_after_one_failure(self):
        stream = WindowsStream()
        stream.reader, stream.writer, stream.socket, stream.device = [Mock() for _ in range(4)]
        stream.reader.close.side_effect = RuntimeError("reader close failed")
        socket = stream.socket
        with self.assertRaisesRegex(RuntimeError, "reader close failed"):
            await stream.close()
        socket.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
