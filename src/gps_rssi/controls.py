"""Thread-safe requests from the local dashboard to the main radio event loop."""

import asyncio
import secrets
import threading
import uuid


class Controls:
    def __init__(self, loop, *, allow_reset=True):
        self.loop = loop
        self.token = secrets.token_urlsafe(32)
        self.queue = asyncio.Queue()
        self.ack = asyncio.Event()
        self.lock = threading.Lock()
        self.operation = {"state": "idle"}
        self.closed = False
        self.allow_reset = allow_reset

    def status(self):
        with self.lock:
            return dict(self.operation)

    def submit(self, action):
        if action not in ("reset", "exit"):
            raise ValueError("Unknown control action")
        if action == "reset" and not self.allow_reset:
            raise RuntimeError("Finish this named sweep before resetting its database. Start a new session for another run.")
        with self.lock:
            if self.closed or self.operation["state"] in ("pending", "running"):
                raise RuntimeError("Another operation is in progress or the app is stopping")
            self.operation = {"id": uuid.uuid4().hex, "action": action, "state": "pending",
                              "message": "Request accepted"}
            result = dict(self.operation)
            self.loop.call_soon_threadsafe(self.queue.put_nowait, result)
            return result

    def update(self, state, message, **details):
        with self.lock:
            self.operation.update(state=state, message=message, **details)

    def acknowledge(self, operation_id):
        with self.lock:
            if (self.operation.get("id") == operation_id and self.operation.get("action") == "exit"
                    and self.operation["state"] == "completed"):
                self.loop.call_soon_threadsafe(self.ack.set)

    def close(self):
        with self.lock:
            self.closed = True
