#!/usr/bin/env python3
"""Repo-root shim -> skills/box-rclone-binder/scripts/verify_config.py (config-spec E3/E5).

Makes verify_config.py discoverable from the repo root and delegates verbatim to the canonical
script, which reuses box-binder's own discovery + loader and prints the resolved config path (so a
$BOX_RCLONE_BINDER_CONFIG hot-swap between two configs is provable). Never echoes a secret value.

Usage (identical to the real script):
  python scripts/verify_config.py [-c <path>] [--json]
"""
import os
import runpy

_REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "skills", "box-rclone-binder", "scripts", "verify_config.py")

if __name__ == "__main__":
    runpy.run_path(os.path.abspath(_REAL), run_name="__main__")
