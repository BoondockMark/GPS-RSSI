"""BLE cleanup adaptation for the pinned Benlink revision."""

from benlink.link import BleCommandLink
from bleak import BleakClient


class RecoveringBleCommandLink(BleCommandLink):
    def __init__(self, device_uuid, timeout=30):
        # Pass the same timeout to Bleak's implicit discovery, not just the
        # logger's outer wait; otherwise discovery still uses its old limit.
        self._client = BleakClient(device_uuid, timeout=timeout)

    async def disconnect(self):
        # Bleak disconnect automatically stops notifications. Benlink first calls
        # stop_notify, which can fail after a dropped link or partial connection
        # and prevent physical disconnect from running. Go directly to Bleak's
        # cleanup, including after discovery/service/notification setup failures.
        await self._client.disconnect()
