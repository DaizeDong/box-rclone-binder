#!/usr/bin/env python3
"""Resolve + validate a box-rclone-binder machines.yaml (config-spec E3/E5).

This is the standalone verify entry point. It reuses box-binder's own discovery order and the
boxbinder.config loader, so it validates EXACTLY what the skill will run against -- no contract
drift. It prints the RESOLVED config path (so a hot-swap of $BOX_RCLONE_BINDER_CONFIG between two
configs is provable) and validates schema + pointer-only secrets + no inline secret values.

Discovery order (first hit wins; same as box_binder.discover_config_path):
  1. -c/--config <path>                (file, or dir holding machines.yaml)
  2. $BOX_RCLONE_BINDER_CONFIG         (file, or dir holding machines.yaml)
  3. $BOX_RCLONE_BINDER_CONFIG_DIR     (dir holding machines.yaml)
  4. ./machines.yaml
  5. ~/.box-rclone-binder-config/machines.yaml
  6. ~/.config/box-rclone-binder/machines.yaml

Usage:
  python scripts/verify_config.py [-c <path>] [--json]

Exit 0 = config resolved + schema valid (missing secret refs are a warning, not a failure).
Exit 3 = config not found or invalid (EXIT_CONFIG).
Stdlib only. Cross-platform. Never echoes a secret VALUE -- only presence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import box_binder as cli                 # discovery + env-var convention (no duplication)
from boxbinder import config as cfgmod   # the loader the skill actually runs against
from boxbinder import EXIT_OK, EXIT_CONFIG


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Resolve and validate a box-rclone-binder machines.yaml (E5 hot-swap aware).")
    ap.add_argument("-c", "--config", default=None,
                    help="path to machines.yaml (file or dir). If omitted, resolved via "
                         "$%s / $%s / ./machines.yaml / ~/.box-rclone-binder-config/ / "
                         "~/.config/box-rclone-binder/" % (cli.CONFIG_ENV, cli.CONFIG_ENV_DIR))
    ap.add_argument("--json", action="store_true", help="machine-readable verdict")
    a = ap.parse_args(argv)

    path = cli.discover_config_path(a.config)  # absolute; dir -> dir/machines.yaml

    try:
        cfg = cfgmod.load(path)
    except FileNotFoundError:
        return _emit(a.json, EXIT_CONFIG,
                     {"verify": "config", "config_path": path, "ready": False,
                      "error": "config not found: %s" % path, "exit_code": EXIT_CONFIG})
    except cfgmod.ConfigError as e:
        return _emit(a.json, EXIT_CONFIG,
                     {"verify": "config", "config_path": path, "ready": False,
                      "error": str(e), "exit_code": EXIT_CONFIG})

    refs = cfgmod.resolve_refs(cfg)                       # presence only; never the value
    missing = sorted(k for k, v in refs.items() if not v["present"])
    result = {"verify": "config", "config_path": path, "ready": True,
              "schema_valid": True, "no_inline_secrets": True,
              "hosts": [h.get("host") for h in cfg.hosts],
              "auth_modes": sorted({cfg.auth_mode(h) for h in cfg.hosts}),
              "secret_source": cfg.secrets.get("source", "env"),
              "missing_refs": missing, "exit_code": EXIT_OK}
    return _emit(a.json, EXIT_OK, result)


def _emit(as_json, code, result):
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    elif result.get("ready"):
        print("READY: %s" % result["config_path"])
        print("  hosts       : %s" % ", ".join(result["hosts"]))
        print("  auth_modes  : %s" % ", ".join(result["auth_modes"]))
        print("  secret_src  : %s" % result["secret_source"])
        if result["missing_refs"]:
            print("  WARNING: secret refs not yet present in backend: %s"
                  % ", ".join(result["missing_refs"]))
        print("  schema valid, pointer-only secrets, no inline secret values.")
    else:
        print("NOT READY: %s" % result["config_path"])
        print("  ERROR: %s" % result.get("error", "invalid config"))
    return code


if __name__ == "__main__":
    sys.exit(main())
