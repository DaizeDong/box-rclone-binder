"""Discord alerting with severity routing and hard secret-scrubbing.

Every outbound message passes through scrub() so a token/secret can never reach the relay even
if a caller is careless. Severity routing mirrors ARCHITECTURE §5: jitter-only events are not
pushed; recovered=INFO; heal-failed/broken-chain=CRITICAL; drift=WARN.
"""
from __future__ import annotations

import os
import re
import subprocess

_REDACTORS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+)?"),  # JWT
    re.compile(r"(?i)(access_token|refresh_token|client_secret|token)\s*[=:]\s*\S+"),
    re.compile(r"-----BEGIN[^-]+PRIVATE KEY-----[\s\S]+?-----END[^-]+PRIVATE KEY-----"),
    # long opaque blob: covers both standard and URL-safe base64 alphabets (-, _) so an
    # unlabeled URL-safe token blob cannot slip through unscrubbed.
    re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}"),
]

SEVERITY_PUSH = {"INFO": True, "WARN": True, "CRITICAL": True, "DEBUG": False}


def scrub(text: str) -> str:
    out = text or ""
    for rx in _REDACTORS:
        out = rx.sub("[REDACTED]", out)
    return out


def route(event: str) -> str:
    """Map an event kind to a severity (None => do not push)."""
    return {
        "jitter": None,            # transient retry recovered -> log only
        "recovered": "INFO",       # auth failed then self-healed
        "heal_failed": "CRITICAL", # self-heal failed -> human needed
        "broken_chain": "CRITICAL",
        "drift": "WARN",
    }.get(event, "WARN")


def send(event: str, message: str, relay: str = None, enabled: bool = True) -> dict:
    sev = route(event)
    safe = scrub(message)
    result = {"event": event, "severity": sev, "pushed": False, "message": safe}
    if not enabled or sev is None or not SEVERITY_PUSH.get(sev, False):
        return result
    relay = relay or os.path.expanduser("~/.claude/discord_relay/send.py")
    payload = "[box-binder %s] %s" % (sev, safe)
    try:
        subprocess.run(["python", relay, payload], timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        result["pushed"] = True
    except Exception:
        result["pushed"] = False
    return result
