#!/usr/bin/env python3
"""Repo-root shim -> skills/box-rclone-binder/scripts/init_config.py (config-spec E3/E4).

This single-skill plugin keeps its real scripts under skills/box-rclone-binder/scripts/. This thin
root-level shim makes init_config.py discoverable from the repo root (where the config standard
expects it) and delegates verbatim to the canonical script -- same args, same deterministic output.

Usage (identical to the real script):
  python scripts/init_config.py [--out <path>] [--force]
"""
import os
import runpy

_REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "skills", "box-rclone-binder", "scripts", "init_config.py")

if __name__ == "__main__":
    runpy.run_path(os.path.abspath(_REAL), run_name="__main__")
