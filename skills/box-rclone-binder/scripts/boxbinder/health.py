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
    keys = ("auth_mode", "root_folder_id", "box_sub_type", "remote_name", "rclone_version")
    fields, divergences = {}, []
    for k in keys:
        vals = {}
        for r in reports:
            if k in r and r[k] is not None:
                vals.setdefault(str(r[k]), []).append(r.get("host"))
        fields[k] = vals
        if len(vals) > 1:
            divergences.append({"field": k, "values": vals})
    return {"consistent": not divergences, "divergences": divergences, "fields": fields}


def refresh_token_invariant(reports: list) -> dict:
    """Cross-host invariant: <=1 holder of a refresh_token (exactly 1 in broker, 0 otherwise)."""
    holders = [r.get("host") for r in reports if r.get("has_refresh_token")]
    modes = {r.get("auth_mode") for r in reports}
    if modes == {"oauth-broker"}:
        ok = len(holders) <= 1  # exactly the master (or none yet)
    else:
        ok = len(holders) == 0
    return {"ok": ok, "holders": holders, "modes": sorted(m for m in modes if m)}
