"""RSSI recording owned by an automatic session's existing control connection."""

import asyncio
from contextlib import contextmanager
from pathlib import Path

from .audio_transport import mac_address
from .locking import InstanceLock
from .measurements import utc_now
from .settings import load_settings
from .storage import Store
from .survey_sessions import session_metadata


def recording_settings(config, address=None, role=None):
    config = Path(config).resolve()
    if not config.is_file():
        raise ValueError("Choose an existing radio setup")
    settings = load_settings(config)
    if address and mac_address(settings["radio"]["address"]) != mac_address(address):
        raise ValueError("Recording setup must belong to the transmitting radio")
    if role and settings["recording"]["role"] != role:
        raise ValueError("Recording role must match the automatic station role")
    return settings


class FieldRecorder:
    def __init__(self, store, settings, report):
        self.store, self.settings, self.report = store, settings, report

    async def sample(self, control):
        requested = utc_now()
        status = await asyncio.wait_for(control.get_status(), .6)
        frame = dict(status=status.model_dump(mode="json"), position=None,
                     status_requested_at=requested, status_received_at=utc_now())
        channel = await asyncio.wait_for(control.get_channel(status.curr_ch_id), .6)
        frame["channel"] = channel.model_dump(mode="json")
        if self.settings["recording"]["gps"] and status.is_gps_locked:
            position = await asyncio.wait_for(control.get_position(), .6)
            frame["position"] = position.model_dump(mode="json")
        self.store.sample(frame)
        self.report.update(samples_recorded=self.report.get("samples_recorded", 0) + 1,
            latest_telemetry=dict(received_at=frame["status_received_at"], rssi=status.rssi,
                                  is_in_rx=status.is_in_rx, is_in_tx=status.is_in_tx,
                                  gps_locked=status.is_gps_locked))


@contextmanager
def field_recording(config, address, role, report, survey_session=None):
    settings = recording_settings(config, address, role)
    recording = settings["recording"]
    database = Path(recording["database"])
    if survey_session:
        settings["survey_session"] = session_metadata(survey_session, config)
        report["survey_session_id"] = settings["survey_session"]["id"]
    with InstanceLock(database.with_name(database.name + ".app.lock")):
        store = Store(database, recording["station"], role, "benlink_bluetooth", settings)
        try:
            report.update(recording=True, samples_recorded=0, database=str(database), recording_session_id=store.session_id)
            store.event("automatic_started", dict(role=role, frequency_hz=1000, period_seconds=20))
            yield FieldRecorder(store, settings, report)
        finally:
            try:
                store.event("automatic_finished", dict(stop_verified=report.get("stop_verified"),
                    samples_recorded=report.get("samples_recorded", 0)))
            finally:
                store.close()
