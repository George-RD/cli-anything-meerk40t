"""Atomic, durable JSON writes.

Single persistence convention shared by session state and material profiles.

The payload is written to a unique temp file in the SAME directory, fsync'd,
then an exclusive advisory lock is taken on the *target* so concurrent writers
serialise. Only after the lock is held is the temp file renamed into place with
``os.replace`` (atomic on POSIX). The directory entry is fsync'd for durability.

If any step fails, the original target (if any) is left byte-for-byte intact and
the temp file is removed. Module-level ``os.replace`` / ``os.fsync`` are used so
callers can monkey-patch them in tests.
"""

from __future__ import annotations

import fcntl  # POSIX advisory locking; absent on non-POSIX platforms.
import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path, data) -> Path:
    """Write *data* as JSON to *path* atomically and durably.

    Returns the written ``Path``. On any failure the original *path* (if any) is
    left untouched and the exception is re-raised.
    """
    path = Path(path)
    directory = path.parent
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", prefix=f"{path.name}.tmp-", dir=str(directory)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        # Serialise concurrent writers to this exact target.
        lock_fd = os.open(str(path), os.O_RDONLY | os.O_CREAT, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        # Best-effort durability of the directory entry.
        try:
            dir_fd = os.open(str(directory), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return path
