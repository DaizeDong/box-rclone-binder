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

# A broker refresh completes in seconds. A lock far older than this is from a crashed/hung
# holder, not a live refresh, recover it instead of dead-locking every future refresh.
_STALE_LOCK_SECONDS = 900


def _pid_alive(pid: int) -> bool:
    """Best-effort cross-platform liveness probe.

    Returns False only when the process is provably gone; on any uncertainty it returns True
    so we never steal a lock that might still be held by a live process.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            k = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False  # no such process / not openable as a live process
            try:
                code = ctypes.c_ulong()
                if k.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                k.CloseHandle(h)
        except Exception:
            return True  # cannot determine -> assume alive (fail safe: keep the lock)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return True
    return True


class FileLock:
    """Single-instance lock with stale-holder recovery.

    A crash that leaves the lock file behind would otherwise dead-lock every future
    broker_refresh (permanent ``Locked`` -> refresh_token expires unattended). On contention we
    take the lock over iff its holder is provably dead OR the lock is far older than any real
    refresh; otherwise we block. ``pid_alive`` is injectable so the recovery path is hermetically
    testable without spawning/killing real processes.
    """

    def __init__(self, path, stale_after=_STALE_LOCK_SECONDS, pid_alive=None):
        self.path = path
        self._fd = None
        self.stale_after = stale_after
        self._pid_alive = pid_alive

    def _alive(self, pid):
        # Resolve the probe at call time so a test that monkeypatches the module fn is honored.
        return (self._pid_alive or _pid_alive)(pid)

    def _read_holder(self):
        try:
            with open(self.path, "r") as f:
                raw = f.read().strip()
            pid = int(raw) if raw.isdigit() else -1
        except (OSError, ValueError):
            pid = -1
        try:
            age = time.time() - os.path.getmtime(self.path)
        except OSError:
            age = None
        return pid, age

    def _is_stale(self):
        pid, age = self._read_holder()
        if pid <= 0:
            return True  # empty / unparseable holder -> orphan
        if not self._alive(pid):
            return True  # holder process is gone (crash) -> orphan
        if age is not None and self.stale_after is not None and age > self.stale_after:
            return True  # holder alive but lock far older than any real refresh -> hung
        return False

    def _create(self):
        self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(self._fd, str(os.getpid()).encode())

    def acquire(self):
        try:
            self._create()
            return self
        except FileExistsError:
            pass
        # Contention: only take over a provably stale lock; a live, fresh holder still blocks.
        if not self._is_stale():
            raise Locked("another box-binder refresh holds %s" % self.path)
        try:
            os.remove(self.path)
        except OSError:
            pass
        try:
            self._create()  # re-create atomically; losing this race means a live racer won
        except FileExistsError:
            raise Locked("lock %s re-taken during stale recovery" % self.path)
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
