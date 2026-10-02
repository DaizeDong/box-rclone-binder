"""Read-only health probing, rclone-stderr classification, and multi-host consistency.

The probe is `rclone lsd box: --max-depth 1` with hard timeouts (NEVER `rclone about`, which
the Box backend does not support; ALWAYS bounded so a hung remote cannot stall the barrier).
It only LISTS — it never writes or deletes LIVE data.
"""
from __future__ import annotations

import re

from . import AUTH_MODES

# stderr -> category. Order matters (auth before generic).
_RULES = [
    ("auth", re.compile(r"invalid[ _]refresh[ _]token|invalid_grant|\b401\b|unauthorized|"
                        r"token expired|no refresh token", re.I)),
    ("ratelimit", re.compile(r"\b429\b|rate ?limit|too many requests|retry-after", re.I)),
    ("network", re.compile(r"timeout|timed out|connection refused|no route to host|"
                           r"i/o timeout|dial tcp|temporary failure|network is unreachable", re.I)),
]
# action routing per category
ACTION = {"ok": "none", "auth": "heal", "ratelimit": "retry",
          "network": "retry", "unknown": "fail"}
CONSISTENCY_FIELDS = ("auth_mode", "root_folder_id", "box_sub_type", "remote_name", "rclone_version")


def runtime_settings(host):
    """The nonsecret host settings used by the runtime's access probe."""
    result = {"auth_mode": host.get("auth_mode", "jwt"),
              "root_folder_id": str(host.get("root_folder_id", "0")),
              "box_sub_type": host.get("box_sub_type", "enterprise"),
              "remote_name": host["remote_name"]}
    if result["auth_mode"] == "oauth-broker":
        result["broker_role"] = host.get("broker_role", "slave")
    return result


def validate_observations(value):
    """Admit only typed runtime observations; never copy an arbitrary remote payload."""
    required = CONSISTENCY_FIELDS[:-1]
    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key]
                                         for key in required):
        raise ValueError("missing or malformed runtime observations")
    result = {key: value[key] for key in required}
    if (result["auth_mode"] not in AUTH_MODES or result["box_sub_type"] not in ("enterprise", "user")
            or not re.fullmatch(r"[0-9]+", result["root_folder_id"])
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", result["remote_name"])):
        raise ValueError("invalid runtime observation fields")
    if "rclone_version" in value:
        version = value["rclone_version"]
        if not isinstance(version, str) or not re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9]+)*", version):
            raise ValueError("invalid observed rclone version")
        result["rclone_version"] = version
    if result["auth_mode"] == "oauth-broker":
        if value.get("broker_role") not in ("master", "slave"):
            raise ValueError("missing or malformed observed broker role")
        result["broker_role"] = value["broker_role"]
    return result


def classify(stderr: str, returncode: int = 0) -> str:
    if returncode == 0 and not (stderr or "").strip():
        return "ok"
    text = stderr or ""
    for name, rx in _RULES:
        if rx.search(text):
            return name
    return "ok" if returncode == 0 else "unknown"


def probe_argv(host: dict):
    remote = host.get("remote_name", "box")
    return ["rclone", "lsd", "%s:" % remote, "--max-depth", "1",
            "--contimeout", "15s", "--timeout", "30s",
            "--low-level-retries", "1", "--retries", "1"]


def has_refresh_token(host: dict, role: str = "slave") -> bool:
    """Which hosts structurally hold a rotating refresh_token.

    Server-auth modes (jwt/ccg-native/ccg-mint): NONE — there is no refresh_token at all.
    oauth-broker: only the single master holds it; slaves get an access-only blob.
    """
    am = host.get("auth_mode", "jwt")
    if am == "oauth-broker":
        return role == "master"
    return False


def probe(driver, host: dict, timeout: int = 35) -> dict:
    argv = probe_argv(host)
    rc, out, err = driver.exec(argv, mutate=False, timeout=timeout)
    cat = classify(err, rc)
    return {
        "host": host.get("host"),
        "rc": rc,
        "category": cat,
        "action": ACTION[cat],
        "healthy": cat == "ok",
        "auth_mode": host.get("auth_mode", "jwt"),
        "root_folder_id": str(host.get("root_folder_id", "0")),
        "box_sub_type": host.get("box_sub_type", "enterprise"),
        "remote_name": host.get("remote_name", "box"),
        "has_refresh_token": has_refresh_token(host),
    }


def consistency(reports: list) -> dict:
    """Detect cross-host drift on the fields that MUST match for one shared Box binding."""
    if not reports:
        return {"consistent": True, "divergences": [], "fields": {}}
    fields, divergences = {}, []
    for k in CONSISTENCY_FIELDS:
        vals = {}
        for r in reports:
            if k in r and r[k] is not None:
                vals.setdefault(str(r[k]), []).append(r.get("host"))
        fields[k] = vals
        if len(vals) > 1:
            divergences.append({"field": k, "values": vals})
    return {"consistent": not divergences, "divergences": divergences, "fields": fields}


def refresh_token_invariant(reports: list) -> dict:
    """Allow at most one broker holder; preserve unknown observations unless a violation is proven."""
    holders = [r.get("host") for r in reports if r.get("has_refresh_token")]
    modes = {r["auth_mode"] for r in reports if r.get("auth_mode") is not None}
    if modes == {"oauth-broker"}:
        ok = len(holders) <= 1  # exactly the master (or none yet)
    else:
        ok = len(holders) == 0
    if ok and any(r.get("auth_mode") is None or r.get("has_refresh_token") is None for r in reports):
        ok = None
    return {"ok": ok, "holders": holders, "modes": sorted(modes)}
