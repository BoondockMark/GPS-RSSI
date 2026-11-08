"""Create radio-free, offline training data through the ordinary survey pipeline."""

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import tempfile

from .storage import Store
from .survey import create_survey
from .training import KIND, STATION


def create_demo(output):
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gps-rssi-training-", dir=output.parent) as temporary:
        database = Path(temporary) / "training.sqlite"
        for run in range(2):
            store = Store(database, f"TEST Run {'B' if run else 'A'}", "mobile", "replay", dict(
                training=KIND, recording=dict(label="Synthetic comparison readings — not antenna performance" if run else "Synthetic baseline — not a field survey")))
            try:
                for step, index in enumerate(range(32) if run == 0 else reversed(range(32))):
                    timestamp = (datetime(2026, 11, 1, 12+run, tzinfo=timezone.utc) +
                                 timedelta(seconds=15*step+(120 if run==0 and step>=17 else 0))).isoformat()
                    locked = not (run==0 and index==8)
                    channel = 2 if run==1 and index==3 else 1
                    frequency = 446.0 if channel==2 else 146.52
                    frame = dict(status_received_at=timestamp, status=dict(
                        rssi=round(max(5, min(95, 80-45*math.exp(-((index-10)/4)**2)+run*8)), 1),
                        is_in_tx=False, is_in_rx=True, is_sq=index%7!=0, is_gps_locked=locked, curr_ch_id=channel),
                        position=dict(latitude=STATION['lat']+.005+index*.0013,
                                      longitude=STATION['lon']+run*.0001, time=timestamp, accuracy=6) if locked else None)
                    if not (run==1 and index==0):
                        frame['channel'] = dict(channel_id=channel, rx_freq=frequency, tx_freq=frequency)
                    store.sample(frame)
            finally:
                store.close()
        return create_survey([database], output, source="replay")
