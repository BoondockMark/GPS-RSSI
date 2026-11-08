"""Atomic producer status for managed recorders; process state remains separate."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


class OperationStatus:
    def __init__(self, output, **metadata):
        self.output = Path(output).expanduser().resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.report = dict(kind="recording", state="prepared", **metadata)
        self.update()

    def update(self, *, valid_for=30, **values):
        now = datetime.now(timezone.utc)
        self.report.update(values, reported_at=now.isoformat(),
                           expires_at=(now + timedelta(seconds=min(3600, max(1, valid_for)))).isoformat())
        temporary = self.output / ".report.json.tmp"
        temporary.write_text(json.dumps(self.report, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(self.output / "report.json")
