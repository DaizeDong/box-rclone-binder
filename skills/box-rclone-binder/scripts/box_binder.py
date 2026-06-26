#!/usr/bin/env python3
"""box-binder — bind one Box drive across many servers via rclone, idempotently and safely.

CLI contract and exit codes per ARCHITECTURE §8. Stdlib only. Every command supports --json
(machine verdict) and --dry-run (no side effects). Secrets are referenced, never printed.

Usage:
  box-binder <deploy|refresh|healthcheck|status|verify-config|doctor> [options]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from boxbinder import (EXIT_OK, EXIT_PARTIAL, EXIT_ALL_FAILED, EXIT_CONFIG,
                       EXIT_UNREACHABLE, EXIT_HEAL_FAILED, __version__)
from boxbinder import config as cfgmod
from boxbinder import deploy as deploymod
from boxbinder import health as healthmod
from boxbinder import refresh as refreshmod
from boxbinder.drivers import SSHHostDriver


CONFIG_ENV = "BOX_RCLONE_BINDER_CONFIG"
CONFIG_ENV_DIR = "BOX_RCLONE_BINDER_CONFIG_DIR"
CONFIG_BASENAME = "machines.yaml"


def discover_config_path(explicit=None):
    """Resolve the machines.yaml path (config-spec E2). First hit wins:

      1. explicit -c/--config (file, or dir holding machines.yaml)
      2. $BOX_RCLONE_BINDER_CONFIG       (file, or dir holding machines.yaml)
      3. $BOX_RCLONE_BINDER_CONFIG_DIR   (dir holding machines.yaml)
      4. ./machines.yaml                 (cwd-relative; the historical default)
      5. ~/.box-rclone-binder-config/machines.yaml
      6. ~/.config/box-rclone-binder/machines.yaml

    Existence-checked candidates (4-6) only win if present; the cwd default is returned as the
    last resort even when absent, so the 'config not found' error still names a concrete path.
    An explicit flag value is honored verbatim (a missing path then surfaces EXIT_CONFIG).
    """
    def as_file(p):
        p = os.path.abspath(os.path.expanduser(p))
        return os.path.join(p, CONFIG_BASENAME) if os.path.isdir(p) else p

    if explicit:
        return as_file(explicit)
    val = os.environ.get(CONFIG_ENV)
    if val:
        return as_file(val)
    d = os.environ.get(CONFIG_ENV_DIR)
    if d:
        return os.path.join(os.path.abspath(os.path.expanduser(d)), CONFIG_BASENAME)
    candidates = [
        os.path.abspath(CONFIG_BASENAME),
        os.path.expanduser(os.path.join("~", ".box-rclone-binder-config", CONFIG_BASENAME)),
        os.path.expanduser(os.path.join("~", ".config", "box-rclone-binder", CONFIG_BASENAME)),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0]  # cwd ./machines.yaml as the named last resort


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _select_hosts(cfg, only):
    if not only:
        return cfg.hosts
    want = set(only)
    return [h for h in cfg.hosts if h.get("host") in want]


def _default_factory(host, dry_run):
    return SSHHostDriver(host, dry_run=dry_run)


# ---- commands (each returns (exit_code, result_dict)) ------------------------------------

def cmd_verify_config(cfg, args, **_):
    refs = cfgmod.resolve_refs(cfg)
    missing = [k for k, v in refs.items() if not v["present"]]
    res = {"command": "verify-config", "ts": _now(),
           "hosts": [h.get("host") for h in cfg.hosts],
           "auth_modes": sorted({cfg.auth_mode(h) for h in cfg.hosts}),
           "secret_source": cfg.secrets.get("source", "env"),
           "refs": refs, "missing_refs": missing,
           "schema_valid": True, "no_inline_secrets": True}
    # schema/inline already enforced by load(); missing refs is a warning, not a hard fail.
    res["exit_code"] = EXIT_OK
    return EXIT_OK, res


def cmd_doctor(cfg, args, factory=_default_factory, **_):
    hosts_out, worst = [], EXIT_OK
    for h in _select_hosts(cfg, args.host):
        d = factory(h, dry_run=args.dry_run)
        entry = {"host": h.get("host")}
        rc, out, err = d.exec(["rclone", "version"], mutate=False, timeout=args.timeout)
        entry["ssh_ok"] = rc == 0 or bool(out)
        entry["rclone"] = (out.splitlines()[0] if out else "").strip()
        rc2, out2, _ = d.exec(["sh", "-c", "command -v systemctl >/dev/null && echo yes || echo no"],
                              mutate=False, timeout=args.timeout)
        entry["systemd"] = out2.strip() == "yes"
        if not entry["ssh_ok"]:
            worst = max(worst, EXIT_UNREACHABLE)
        hosts_out.append(entry)
    return worst, {"command": "doctor", "ts": _now(), "hosts": hosts_out, "exit_code": worst}


def cmd_deploy(cfg, args, factory=_default_factory, **_):
    hosts = _select_hosts(cfg, args.host)
    results, n_fail = [], 0
    for h in hosts:
        if args.dry_run:
            results.append(deploymod.deploy_host(None, h, dry_run=True))
            continue
        try:
            d = factory(h, dry_run=False)
            results.append(deploymod.deploy_host(d, h, dry_run=False))
        except Exception as e:  # noqa: BLE001 - surface, never crash the batch
            n_fail += 1
            results.append({"host": h.get("host"), "error": str(e), "changed": [],
                            "mutations": 0})
    code = EXIT_OK
    if not args.dry_run and n_fail:
        code = EXIT_ALL_FAILED if n_fail == len(hosts) else EXIT_PARTIAL
    return code, {"command": "deploy", "ts": _now(), "dry_run": args.dry_run,
                  "results": results,
                  "summary": {"total": len(hosts), "failed": n_fail,
                              "changed": sum(len(r.get("changed", [])) for r in results)},
                  "exit_code": code}


def cmd_healthcheck(cfg, args, factory=_default_factory, **_):
    hosts = _select_hosts(cfg, args.host)
    reports, n_unhealthy = [], 0
    for h in hosts:
        try:
            d = factory(h, dry_run=args.dry_run)
            rep = healthmod.probe(d, h, timeout=args.timeout)
        except Exception as e:  # noqa: BLE001
            rep = {"host": h.get("host"), "healthy": False, "category": "unknown",
                   "action": "fail", "error": str(e), "has_refresh_token": False,
                   "auth_mode": cfg.auth_mode(h)}
        if not rep.get("healthy"):
            n_unhealthy += 1
        reports.append(rep)
    cons = healthmod.consistency(reports)
    inv = healthmod.refresh_token_invariant(reports)
    code = EXIT_OK
    if reports and n_unhealthy == len(reports):
        code = EXIT_UNREACHABLE if all(r.get("category") in ("network", "unknown")
                                       for r in reports) else EXIT_ALL_FAILED
    elif n_unhealthy:
        code = EXIT_PARTIAL
    if any(r.get("category") == "auth" for r in reports):
        code = max(code, EXIT_HEAL_FAILED) if code else EXIT_HEAL_FAILED
    return code, {"command": "healthcheck", "ts": _now(),
                  "hosts": reports, "consistency": cons, "refresh_token_invariant": inv,
                  "summary": {"total": len(reports), "unhealthy": n_unhealthy,
                              "divergences": len(cons["divergences"])},
                  "exit_code": code}


def cmd_refresh(cfg, args, factory=_default_factory, **_):
    hosts = _select_hosts(cfg, args.host)
    plans = [dict(refreshmod.plan_refresh(h), host=h.get("host")) for h in hosts]
    return EXIT_OK, {"command": "refresh", "ts": _now(), "dry_run": args.dry_run,
                     "plans": plans, "exit_code": EXIT_OK,
                     "note": "jwt/ccg-native self-renew; ccg-mint/oauth-broker act on schedule"}


def cmd_status(cfg, args, factory=_default_factory, **_):
    hosts = _select_hosts(cfg, args.host)
    out = [{"host": h.get("host"), "auth_mode": cfg.auth_mode(h),
            "root_folder_id": str(h.get("root_folder_id", "0")),
            "box_sub_type": h.get("box_sub_type", "enterprise"),
            "remote_name": h.get("remote_name", "box")} for h in hosts]
    return EXIT_OK, {"command": "status", "ts": _now(), "hosts": out, "exit_code": EXIT_OK}


COMMANDS = {
    "deploy": cmd_deploy, "refresh": cmd_refresh, "healthcheck": cmd_healthcheck,
    "status": cmd_status, "verify-config": cmd_verify_config, "doctor": cmd_doctor,
}


def build_parser():
    p = argparse.ArgumentParser(prog="box-binder", description="Bind one Box drive across many servers via rclone.")
    p.add_argument("command", choices=list(COMMANDS))
    p.add_argument("-c", "--config", default=None,
                   help="path to machines.yaml (file or dir). If omitted, resolved via "
                        "$BOX_RCLONE_BINDER_CONFIG / $BOX_RCLONE_BINDER_CONFIG_DIR / ./machines.yaml "
                        "/ ~/.box-rclone-binder-config/ / ~/.config/box-rclone-binder/")
    p.add_argument("-H", "--host", action="append", default=[])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--no-alert", action="store_true")
    p.add_argument("-v", "--verbose", action="count", default=0)
    p.add_argument("--version", action="version", version="box-binder %s" % __version__)
    return p


def run(argv=None, factory=_default_factory):
    args = build_parser().parse_args(argv)
    cfg_path = discover_config_path(args.config)
    try:
        cfg = cfgmod.load(cfg_path)
    except FileNotFoundError:
        return _emit(args, EXIT_CONFIG, {"command": args.command, "error": "config not found: %s" % cfg_path, "exit_code": EXIT_CONFIG})
    except cfgmod.ConfigError as e:
        return _emit(args, EXIT_CONFIG, {"command": args.command, "error": str(e), "exit_code": EXIT_CONFIG})
    code, result = COMMANDS[args.command](cfg, args, factory=factory)
    return _emit(args, code, result)


def _emit(args, code, result):
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        _human(result)
    return code


def _human(result):
    cmd = result.get("command", "?")
    if "error" in result:
        print("box-binder %s: ERROR: %s" % (cmd, result["error"]))
        return
    print("box-binder %s @ %s  (exit %s)" % (cmd, result.get("ts", ""), result.get("exit_code")))
    for key in ("results", "hosts", "plans"):
        if key in result and isinstance(result[key], list):
            for item in result[key]:
                print("  - %s" % json.dumps(item, ensure_ascii=False, sort_keys=True))
    if "summary" in result:
        print("  summary: %s" % json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    sys.exit(run())
