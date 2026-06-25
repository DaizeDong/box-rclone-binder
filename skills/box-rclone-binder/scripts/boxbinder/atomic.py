"""Atomic local write: temp in the SAME directory -> fsync -> os.replace -> dir fsync.

Cross-filesystem rename is non-atomic (and on some platforms fails), so we refuse to rename a
temp that is not on the same volume as the destination (ARCHITECTURE anti-pattern #7, rclone
issue #6656). A crash before os.replace leaves the destination untouched (no half-written file).
"""
from __future__ import annotations

import os
import tempfile


class CrossVolumeError(RuntimeError):
    pass


def _same_volume(a: str, b: str) -> bool:
    """True if paths a and b live on the same device/volume."""
    da = os.path.dirname(os.path.abspath(a)) or "."
    db = os.path.dirname(os.path.abspath(b)) or "."
    # Walk up to an existing ancestor (dest dir may not exist yet in dry tests).
    while not os.path.exists(da) and os.path.dirname(da) != da:
        da = os.path.dirname(da)
    while not os.path.exists(db) and os.path.dirname(db) != db:
        db = os.path.dirname(db)
    try:
        return os.stat(da).st_dev == os.stat(db).st_dev
    except OSError:
        return False


def assert_same_volume(tmp_path: str, dest_path: str) -> None:
    if not _same_volume(tmp_path, dest_path):
        raise CrossVolumeError(
            "refusing non-atomic cross-volume rename: %s -> %s" % (tmp_path, dest_path)
        )


def atomic_write(dest_path: str, data, mode: int = 0o600) -> str:
    """Write `data` (str or bytes) to dest_path atomically. Returns dest_path.

    The temp file is created in the destination directory so the final rename stays on one
    volume. fsync the file and the directory so the rename survives a crash.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    dest_path = os.path.abspath(dest_path)
    d = os.path.dirname(dest_path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bbtmp.", dir=d)
    try:
        assert_same_volume(tmp, dest_path)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass  # chmod is a no-op / unsupported on some filesystems (e.g. Windows)
        os.replace(tmp, dest_path)
        # fsync the directory so the rename is durable.
        try:
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass  # directory fsync unsupported on Windows
        return dest_path
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
