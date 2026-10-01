#!/usr/bin/env python3
"""Stamp a box-rclone-binder machines.yaml from the committed, commit-safe template
(config-spec E3/E4).

Deterministic: the output is a byte-for-byte copy of config/machines.example.yaml, so re-running
with --force yields byte-identical output (E4). The template carries ZERO secret values (pointer-only
*_ref), so the stamped file is safe to start from; you then edit hosts and keep secrets as pointers.

Discovery this skill uses (also in CONFIG.md, E2). box-binder resolves machines.yaml from, in order:
  1. -c/--config <path>
  2. $BOX_RCLONE_BINDER_CONFIG        (file, or dir holding machines.yaml)
  3. $BOX_RCLONE_BINDER_CONFIG_DIR    (dir holding machines.yaml)
  4. machines.yaml in the private companion repository

Usage:
  python scripts/init_config.py [--out <path>] [--force]

--out    target file OR dir (a dir -> <dir>/machines.yaml).
         Default: the same environment or private companion path the skill discovers.
         Real inventories belong in a versioned private companion repository.
--force  overwrite if the target already exists.

Stdlib only. Cross-platform. Never writes a secret value.
"""
import argparse
import os
import sys

import box_binder as cli
from boxbinder import EXIT_CONFIG
from boxbinder.config import ConfigError

ENV = cli.CONFIG_ENV
BASENAME = cli.CONFIG_BASENAME


def template_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "config", "machines.example.yaml"))


def main():
    ap = argparse.ArgumentParser(
        description="Stamp a machines.yaml from the commit-safe template (deterministic).")
    ap.add_argument("--out", default=None,
                    help="target file or dir in a private companion (default: the skill's discovery path)")
    ap.add_argument("--force", action="store_true", help="overwrite if the target already exists")
    a = ap.parse_args()

    tmpl = template_path()
    if not os.path.isfile(tmpl):
        print("ERROR: template not found: %s" % tmpl)
        return 2

    try:
        out = a.out or cli.discover_config_path()
    except ConfigError as exc:
        print("ERROR: %s" % exc)
        return EXIT_CONFIG
    out = os.path.abspath(os.path.expanduser(out))
    # Treat the target as a DIRECTORY (write <dir>/machines.yaml inside it) when it is an
    # existing dir, ends with a separator, or has no file extension in its basename. A path
    # whose basename carries an extension (e.g. private-companion/machines.yaml) is a file.
    if os.path.isdir(out) or out.endswith(("/", "\\")) or "." not in os.path.basename(out):
        out = os.path.join(out, BASENAME)

    # Resolve the installed entrypoint first, since a skill may be mounted through a junction.
    tool_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.realpath(__file__)))))
    real_target = os.path.normcase(os.path.realpath(out))
    tool_root = os.path.normcase(tool_root)
    try:
        inside_tool = os.path.commonpath([real_target, tool_root]) == tool_root
    except ValueError:  # Separate Windows volumes cannot contain each other.
        inside_tool = False
    if inside_tool:
        print("ERROR: inventories belong in a private companion repository, outside the public tool tree.")
        return EXIT_CONFIG

    print("Init box-rclone-binder config")
    print("Template : %s" % tmpl)
    print("Target   : %s" % out)
    print("Discovery: $%s (or $%s_DIR), then the private companion repository." % (ENV, ENV))

    if os.path.exists(out) and not a.force:
        print("\nEXISTS (use --force to overwrite): %s" % out)
        print("Nothing changed.")
        return 0

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(tmpl, "rb") as f:
        data = f.read()
    with open(out, "wb") as f:  # byte-for-byte copy -> deterministic (E4)
        f.write(data)
    print("\nwrote: %s  (%d bytes, byte-identical to the committed template)" % (out, len(data)))

    print("\nNext:")
    print("  1) Edit %s: set your hosts; keep every secret as a *_ref POINTER, never a literal." % out)
    print("  2) Keep the inventory in your private companion's version history and point the skill at it:")
    print("       export %s=%s" % (ENV, out))
    print("  3) Put the real secret VALUES in your backend (env/op/vault/aws-ssm/file) — not in the yaml.")
    print("  4) Confirm it is ready (doctor reports per-item what is missing):")
    print("       python scripts/box_binder.py verify-config -c %s --json" % out)
    print("       python scripts/box_binder.py doctor        -c %s --json" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
