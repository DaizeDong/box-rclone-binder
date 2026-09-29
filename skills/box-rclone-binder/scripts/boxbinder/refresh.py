"""Token refresh / self-heal, dispatched by auth_mode.

- jwt / ccg-native : rclone renews automatically (tokenRenewer / native CCG). refresh() is a
  validation-only no-op: it runs a probe and reports; it does NOT mint anything.
- ccg-mint         : POST client_credentials, persist a validated access-only token in a
  protected file, then let the runtime supply it through rclone's environment.
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
import uuid

from .atomic import atomic_write

DEFAULT_TOKEN_URL = "https://api.box.com/oauth2/token"


class NonRetryable(RuntimeError):
    pass


class Retryable(RuntimeError):
    pass


class Locked(RuntimeError):
    pass


# ---- cross-platform single-instance lock (broker) ----------------------------------------

# Legacy constructor default retained for callers; FileLock uses OS ownership, not age.
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
    """OS-owned exclusive lock; a crash releases ownership without stale-file deletion.

    The persistent file stores diagnostic PID metadata only. Age and PID guesses
    never authorize taking a live owner's lock. Legacy constructor options remain
    accepted for callers, but ownership comes from the OS lock.
    """

    def __init__(self, path, stale_after=_STALE_LOCK_SECONDS, pid_alive=None):
        self.path = path
        self._fd = None

    def acquire(self):
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise Locked('another refresh owns the broker lock, or locking is unavailable') from None
        self._fd = fd
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            self.release()
            raise
        return self

    def release(self):
        if self._fd is not None:
            fd = self._fd
            self._fd = None
            try:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

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
    """Persist a valid access-only token for the runtime's rclone environment."""
    code, payload = _post_form(token_url, fields, timeout=timeout)
    if code == 200:
        blob = _token_blob(payload)
        atomic_write(tokenfile, json.dumps(blob), mode=0o600)
        return {"ok": True, "tokenfile": tokenfile, "delivery": "rclone-environment"}
    err = payload.get('error', '') if isinstance(payload, dict) else ''
    if err in ("invalid_grant", "invalid_client", "unauthorized_client"):
        raise NonRetryable("CCG mint rejected: %s" % err)
    raise Retryable("CCG mint failed: http %s" % code)


def _token_blob(payload):
    if not isinstance(payload, dict):
        raise Retryable('invalid token response')
    token, seconds = payload.get('access_token'), payload.get('expires_in')
    if (not isinstance(token, str) or not token or isinstance(seconds, bool)
            or not isinstance(seconds, int) or not 0 < seconds <= 86400):
        raise Retryable('invalid token or expiry in response')
    return {'access_token': token, 'token_type': 'bearer', 'expiry': _expiry_iso(seconds)}


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
        if code != 200 or not isinstance(payload, dict):
            err = payload.get('error', '') if isinstance(payload, dict) else ''
            if err == "invalid_grant":
                raise NonRetryable("refresh chain broken (invalid_grant): manual re-auth required")
            raise Retryable("broker refresh failed: http %s" % code)
        if not isinstance(payload.get('refresh_token'), str) or not payload['refresh_token']:
            raise NonRetryable('broker response omitted the rotated refresh token; re-auth may be required')
        events.append("token_minted")
        # 1) PERSIST new refresh_token FIRST (atomic)
        new_state = {"refresh_token": payload['refresh_token'],
                     "updated": _expiry_iso(0)}
        atomic_write(state_path, json.dumps(new_state), mode=0o600)
        events.append("refresh_token_persisted")
        # 2) THEN render access-only blobs for slaves (refresh_token STRIPPED)
        blob = _token_blob(payload)
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


def invoke_runtime(driver, host, action, timeout=60):
    """Accept only a successful receipt for this invocation and selected mode."""
    from .drivers import RemoteError
    operation_id = uuid.uuid4().hex
    argv = ['python3', '/opt/box-binder/box_runtime.py', action, '--host-file',
            host.get('config_dir', '/etc/box-binder') + '/host.json',
            '--operation-id', operation_id, '--timeout', str(timeout)]
    output = driver.checked_exec(argv, mutate=action == 'refresh', timeout=timeout + 10,
                                 operation='runtime ' + action)
    try:
        receipt = json.loads(output)
        if (not isinstance(receipt, dict) or receipt.get('ok') is not True
                or receipt.get('operation_id') != operation_id
                or receipt.get('auth_mode') != host.get('auth_mode', 'jwt')
                or receipt.get('action') != action):
            raise ValueError
    except (TypeError, ValueError):
        raise RemoteError('runtime receipt', 'stale, malformed or unsuccessful') from None
    return {'host': host['host'], 'ok': True, 'status': 'completed', 'action': action,
            'auth_mode': host.get('auth_mode', 'jwt'), 'operation_id': operation_id}


def safe_error(exc):
    from .drivers import RemoteError
    return str(exc) if isinstance(exc, RemoteError) else type(exc).__name__


def execute_refresh(cfg, selected, factory, timeout=60):
    """Execute selected hosts; retries of broker slaves reuse the persisted access token."""
    from .runtime import access_blob
    from .deploy import _converge_file
    results = {}
    brokers = [host for host in selected if cfg.auth_mode(host) == 'oauth-broker']
    for host in selected:
        if cfg.auth_mode(host) == 'oauth-broker':
            continue
        try:
            driver = factory(host, dry_run=False)
            action = 'refresh' if cfg.auth_mode(host) == 'ccg-mint' else 'validate'
            results[host['host']] = invoke_runtime(driver, host, action, timeout)
        except Exception as exc:
            results[host['host']] = {'host': host['host'], 'ok': False, 'status': 'failed', 'error': safe_error(exc)}
    if brokers:
        try:
            masters = [host for host in cfg.hosts if cfg.auth_mode(host) == 'oauth-broker'
                       and host.get('broker_role') == 'master']
            if len(masters) != 1:
                raise NonRetryable('exactly one broker master is required')
            master = masters[0]
            source = factory(master, dry_run=False)
            if master in brokers:
                results[master['host']] = invoke_runtime(source, master, 'refresh', timeout)
            raw = source.read_text(master.get('config_dir', '/etc/box-binder') + '/access.json')
            blob = json.dumps(access_blob(json.loads(raw)), sort_keys=True)
        except Exception as exc:
            for host in brokers:
                results[host['host']] = {'host': host['host'], 'ok': False, 'status': 'failed',
                                         'error': 'broker source unavailable: ' + safe_error(exc)}
        else:
            for host in brokers:
                if host is master or host == master:
                    continue
                try:
                    driver = factory(host, dry_run=False)
                    target = host.get('config_dir', '/etc/box-binder') + '/access.json'
                    _converge_file(driver, target, blob, 0o600)
                    results[host['host']] = invoke_runtime(driver, host, 'validate', timeout)
                except Exception as exc:
                    results[host['host']] = {'host': host['host'], 'ok': False, 'status': 'failed', 'error': safe_error(exc)}
    return [results[host['host']] for host in selected]
