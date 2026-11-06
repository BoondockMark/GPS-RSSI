"""Cross-platform process locks shared by recording and setup editing."""

import os


class InstanceLock:
    """OS-owned lock; automatically released if the app exits or crashes."""
    def __init__(self, path, *, message=None):
        self.path = path
        self.file = None
        self.message = message or ("This database is already in use by another start command. "
                                   "Use its existing window/dashboard instead.")

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.file.seek(0, 2) == 0:
            self.file.write(b"\0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError(self.message) from exc
        return self

    def __exit__(self, *args):
        self.file.close()  # Do not unlink: another process may be waiting on this inode.
