"""Discord alerting with severity routing and hard secret-scrubbing.

Every outbound message passes through scrub() so a token/secret can never reach the relay even
if a caller is careless. Severity routing mirrors ARCHITECTURE §5: jitter-only events are not
pushed; recovered=INFO; heal-failed/broken-chain=CRITICAL; drift=WARN.

Delivery honesty is the other half of the contract: send() reports pushed=True only when the
egress process exits 0, and every False carries a reason. See _spawn().
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

_REDACTORS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+)?"),  # JWT
    re.compile(r"(?i)(access_token|refresh_token|client_secret|token)\s*[=:]\s*\S+"),
    re.compile(r"-----BEGIN[^-]+PRIVATE KEY-----[\s\S]+?-----END[^-]+PRIVATE KEY-----"),
    # long opaque blob: covers both standard and URL-safe base64 alphabets (-, _) so an
    # unlabeled URL-safe token blob cannot slip through unscrubbed.
    re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}"),
]

SEVERITY_PUSH = {"INFO": True, "WARN": True, "CRITICAL": True, "DEBUG": False}

# This skill's Agent Center stream. Infra alerts must be addressed explicitly, because an egress
# that is asked for no stream defaults to "mail" and the alert lands in the wrong channel.
STREAM = "infra"


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


def _spawn(argv: list) -> tuple:
    """Run an egress child and report the truth about it: (delivered, reason).

    THE INVARIANT THIS FUNCTION EXISTS FOR: delivery is claimed only when the child exits 0.
    A previous version ran the child with check=False, ignored the return code, and set
    pushed=True unconditionally, so an argv the egress rejected (exit 2) or a relay path that
    did not exist was reported to the operator as a successful push. Silent non-delivery is the
    single worst failure this skill can have, because the operator stops looking.

    Never raises: an alert path that explodes must degrade to "not pushed, here is why".
    """
    try:
        p = subprocess.run(argv, timeout=20, text=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired:
        return False, "egress timed out after 20s"
    except Exception as e:
        return False, "egress could not be spawned: %s" % scrub(str(e))
    if p.returncode == 0:
        return True, ""
    tail = scrub(" ".join((p.stderr or "").split()))[-200:]
    return False, "egress exited %d%s" % (p.returncode, (": " + tail) if tail else "")


def send(event: str, message: str, relay: str = None, enabled: bool = True,
         stream: str = STREAM) -> dict:
    """Push one severity-routed, scrubbed alert. Returns a result dict.

    `pushed` is True only if an egress child actually exited 0. Whenever it is False the
    `reason` field says why (policy, missing egress, nonzero exit, timeout), never silence.
    """
    sev = route(event)
    safe = scrub(message)
    result = {"event": event, "severity": sev, "pushed": False, "message": safe,
              "egress": None, "reason": ""}
    if not enabled:
        result["reason"] = "alerts disabled"
        return result
    if sev is None or not SEVERITY_PUSH.get(sev, False):
        result["reason"] = "severity %r is log-only, not pushed by policy" % sev
        return result
    payload = "[box-binder %s] %s" % (sev, safe)
    # Egress selection. An explicit `relay` (the `alerts.relay` config key, or a test override)
    # wins; else the relay named by BOX_RCLONE_BINDER_RELAY, defaulting to the machine adapter
    # ~/.local/relay.py; else a minimal notifier script (BOX_RCLONE_BINDER_NOTIFIER) so the skill
    # still works standalone. `~` is expanded on every path, including the explicit one, because
    # a config file naturally writes "~/.local/relay.py" and an unexpanded tilde is a missing file.
    if relay:
        target, kind = os.path.expanduser(relay), "relay"
    else:
        rp = os.path.expanduser(
            os.environ.get("BOX_RCLONE_BINDER_RELAY", "~/.local/relay.py"))
        if os.path.isfile(rp):
            target, kind = rp, "relay"
        else:
            target, kind = os.path.expanduser(
                os.environ.get("BOX_RCLONE_BINDER_NOTIFIER", "~/.local/notifier.py")), "notifier"
    result["egress"] = kind
    if not os.path.isfile(target):
        result["reason"] = "%s script not found: %s" % (kind, target)
        return result
    # Two conventions, and they are NOT interchangeable. A relay takes an argparse subcommand
    # (`send --stream NAME --text TEXT`) and rejects a bare positional with exit 2. A minimal
    # notifier takes the message as argv[1]; the trailing --stream pair is there for the ones
    # that understand it and is harmlessly ignored by the ones that do not.
    #
    # sys.executable, never the literal "python": under the Windows Task Scheduler, PATH resolves
    # only to the WindowsApps stub, so a bare "python" hangs or dies with no output.
    if kind == "relay":
        argv = [sys.executable, target, "send", "--stream", stream, "--text", payload]
    else:
        argv = [sys.executable, target, payload, "--stream", stream]
    result["pushed"], result["reason"] = _spawn(argv)
    return result
