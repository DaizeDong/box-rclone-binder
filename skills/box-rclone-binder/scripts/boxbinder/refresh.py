"""Token refresh / self-heal, dispatched by auth_mode.

- jwt / ccg-native : rclone renews automatically (tokenRenewer / native CCG). refresh() is a
  validation-only no-op: it runs a probe and reports; it does NOT mint anything.
- ccg-mint         : POST client_credentials to mint a fresh access_token, written to a
  chmod-600 @file and injected by reference (never as an argv). A <60m timer re-mints.
- oauth-broker     : single-master flock'd refresh. The new refresh_token is persisted FIRST,
  THEN access-only blobs (refresh_token STRIPPED) are rendered for slaves. invalid_grant is a
  non-retryable broken chain (CRITICAL), not a retry.

No secret value is ever logged or returned. Token endpoints are overridable via env so the
whole flow is testable against a fake server with no real Box credentials.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from .atomic import atomic_write

DEFAULT_TOKEN_URL = "https://api.box.com/oauth2/token"


class NonRetryable(RuntimeError):
    pass


class Retryable(RuntimeError):
    pass


class Locked(RuntimeError):
    pass


# ---- cross-platform single-instance lock (broker) ----------------------------------------

class FileLock:
    def __init__(self, path):
        self.path = path
        self._fd = None

    def acquire(self):
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(self._fd, str(os.getpid()).encode())
        except FileExistsError:
            raise Locked("another box-binder refresh holds %s" % self.path)
        return self

    def release(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            os.remove(self.path)
        except OSError:
            pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *a):
        self.release()


# ---- HTTP token mint (ccg-mint + broker share this primitive) ----------------------------

def _post_form(url, fields, timeout=20):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # type: ignore
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            payload = {"error": "http_%d" % e.code}
        return e.code, payload


def build_mint_fields(client_id, client_secret, enterprise_id, sub_type="enterprise"):
    """The exact body Box CCG requires (architecture §2.3): subject_type/_id are mandatory."""
    return {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "box_subject_type": sub_type,
        "box_subject_id": enterprise_id,
    }


def mint_access_token(token_url, fields, tokenfile, timeout=20):
    """Mint an access token and write it to a chmod-600 @file. Returns an injection spec.

    The token is delivered to rclone BY REFERENCE (`token=@<file>`), never on argv — so it
    cannot leak via ps/argv/history. Returns dict with the rclone injection command parts.
    """
    code, payload = _post_form(token_url, fields, timeout=timeout)
    if code == 200 and "access_token" in payload:
        blob = {"access_token": payload["access_token"],
                "token_type": payload.get("token_type", "bearer"),
                "expiry": _expiry_iso(payload.get("expires_in", 3600))}
        atomic_write(tokenfile, json.dumps(blob), mode=0o600)
        return {"ok": True, "tokenfile": tokenfile,
                "inject": ["rclone", "config", "update", "box",
                           "token=@%s" % tokenfile, "--non-interactive"]}
    err = (payload or {}).get("error", "")
    if err in ("invalid_grant", "invalid_client", "unauthorized_client"):
        raise NonRetryable("CCG mint rejected: %s" % err)
    raise Retryable("CCG mint transient failure: http %s %s" % (code, err))


def _expiry_iso(expires_in):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + int(expires_in)))


# ---- oauth-broker (degraded path, individual Box only) -----------------------------------

def broker_refresh(state_path, token_url, lock_path, slaves, client_id, client_secret,
                   timeout=20):
    """Single-master refresh + access-only distribution. Returns an ordered event log.

    Order is load-bearing: persist the NEW refresh_token before rendering any slave blob, so a
    crash mid-distribute never loses the (single-use, rotating) token. Slave blobs are stripped
    of refresh_token so a slave's rclone is structurally unable to rotate it.
    """
    events = []
    lock = FileLock(lock_path)
    lock.acquire()  # raises Locked if another instance is mid-refresh
    events.append("lock_acquired")
    try:
        state = _read_state(state_path)
        rt = state.get("refresh_token")
        if not rt:
            raise NonRetryable("broker state has no refresh_token; needs one-time authorization")
        fields = {"grant_type": "refresh_token", "refresh_token": rt,
                  "client_id": client_id, "client_secret": client_secret}
        code, payload = _post_form(token_url, fields, timeout=timeout)
        if code != 200 or "access_token" not in payload:
            err = (payload or {}).get("error", "http_%s" % code)
            if err == "invalid_grant":
                raise NonRetryable("refresh chain broken (invalid_grant): manual re-auth required")
            raise Retryable("broker refresh transient: %s" % err)
        events.append("token_minted")
        # 1) PERSIST new refresh_token FIRST (atomic)
        new_state = {"refresh_token": payload.get("refresh_token", rt),
                     "updated": _expiry_iso(0)}
        atomic_write(state_path, json.dumps(new_state), mode=0o600)
        events.append("refresh_token_persisted")
        # 2) THEN render access-only blobs for slaves (refresh_token STRIPPED)
        blob = {"access_token": payload["access_token"],
                "token_type": payload.get("token_type", "bearer"),
                "expiry": _expiry_iso(payload.get("expires_in", 3600))}
        assert "refresh_token" not in blob
        rendered = {}
        for s in slaves:
            rendered[s] = dict(blob)
            events.append("slave_blob_rendered:%s" % s)
        return {"events": events, "slave_blobs": rendered, "state_path": state_path}
    finally:
        lock.release()
        events.append("lock_released")


def _read_state(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---- dispatch ----------------------------------------------------------------------------

def plan_refresh(host: dict) -> dict:
    """Describe what refresh() would do for this host's auth_mode (no side effects)."""
    am = host.get("auth_mode", "jwt")
    if am in ("jwt", "ccg-native"):
        return {"auth_mode": am, "action": "validate-noop",
                "reason": "rclone auto-renews; refresh only re-probes credentials"}
    if am == "ccg-mint":
        return {"auth_mode": am, "action": "re-mint",
                "interval_min": int(host.get("mint_interval_min", 45))}
    if am == "oauth-broker":
        return {"auth_mode": am, "action": "single-master-refresh+distribute"}
    return {"auth_mode": am, "action": "unknown"}
