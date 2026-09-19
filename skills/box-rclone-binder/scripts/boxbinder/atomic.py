"""Compatibility entrypoint for the shared fleet atomic writer.

The shared writer reports permission and durability errors instead of suppressing
them. Publication may already have happened when a directory sync fails; callers
must reconcile their transaction rather than retrying a business operation.
"""
from __future__ import annotations
import os
from fleet_guards import filesystem
from fleet_guards.filesystem import CrossVolumeError, _same_volume, assert_same_volume


def atomic_write(dest_path: str, data, mode: int = 0o600) -> str:
    """Write complete bytes and preserve the legacy absolute-path return value."""
    dest_path = os.path.abspath(dest_path)
    filesystem.atomic_replace(dest_path, data, mode=mode)
    return dest_path
