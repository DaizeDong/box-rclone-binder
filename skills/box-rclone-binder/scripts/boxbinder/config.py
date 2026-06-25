"""machines.yaml loading + schema validation + secret-reference resolution (pointers only) +
inline-secret scanning. The config file is INPUT and may be committed; it must never contain a
secret value, only a pointer (env var name / op:// path / absolute file path) to where the
secret lives. We validate that invariant here and refuse to run otherwise.
"""
from __future__ import annotations

import os
import re

from . import AUTH_MODES

try:  # optional dependency; fall back to bundled subset parser
    import yaml as _pyyaml  # type: ignore

    def _yload(text):
        return _pyyaml.safe_load(text)
except Exception:  # pragma: no cover - exercised on machines without PyYAML
    from . import yamlmin

    def _yload(text):
        return yamlmin.load(text)


class ConfigError(ValueError):
    pass


# Keys whose VALUE must never appear inline in machines.yaml.
_SECRET_VALUE_KEYS = ("refresh_token", "access_token", "client_secret", "private_key",
                      "rclone_config_pass")
# A pointer/placeholder looks like an env name, an op:// / vault path, an absolute path, or a
# clearly-empty/placeholder token. Anything else assigned to a secret key is a real secret.
_POINTER_RE = re.compile(r'^(?:[A-Z][A-Z0-9_]+|op://\S+|vault://\S+|aws-ssm://\S+|/\S+|<[^>]+>|""|\'\')?$')
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")
# long opaque blob that is not an obvious id; only flagged when assigned to a secretish key
_BLOB_RE = re.compile(r"[A-Za-z0-9+/=_-]{40,}")

_VALID_SOURCES = ("env", "file", "op", "vault", "aws-ssm")


class Config:
    def __init__(self, data: dict, path: str = "<memory>"):
        self.path = path
        self.version = data.get("version", 1)
        self.defaults = dict(data.get("defaults") or {})
        self.secrets = dict(data.get("secrets") or {})
        self.alerts = dict(data.get("alerts") or {})
        raw_hosts = data.get("hosts") or []
        self.hosts = []
        for h in raw_hosts:
            merged = dict(self.defaults)
            merged.update({k: v for k, v in (h or {}).items() if v is not None})
            self.hosts.append(merged)

    def auth_mode(self, host: dict) -> str:
        return host.get("auth_mode", self.defaults.get("auth_mode", "jwt"))


def _scan_inline_secrets(text: str):
    """Return a list of (lineno, reason) for suspected inline secret VALUES."""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _PEM_RE.search(line) or _JWT_RE.search(line):
            hits.append((i, "embedded key/JWT material"))
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip().lower().lstrip("-").strip()
        val = val.strip()
        # strip an inline comment from the value (best-effort; quoted handled by parser elsewhere)
        if val and not (val[0] in "\"'"):
            val = val.split("#", 1)[0].strip()
        if val in ("", "''", '""'):
            continue
        # secret-bearing key with a value that is not a pointer/placeholder => real secret
        base = key.replace("-", "_")
        if any(base == k or base.endswith("_" + k) for k in _SECRET_VALUE_KEYS):
            v = val.strip("\"'")
            if not _POINTER_RE.match(v):
                hits.append((i, "inline value for secret key '%s'" % key))
                continue
        # generic high-entropy blob assigned to any *secret*-named key (not a *_ref pointer)
        if "secret" in base or "token" in base or "private" in base:
            if not base.endswith("_ref"):
                v = val.strip("\"'")
                if _BLOB_RE.fullmatch(v) and not _POINTER_RE.match(v):
                    hits.append((i, "opaque blob assigned to '%s'" % key))
    return hits


def load(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    inline = _scan_inline_secrets(text)
    if inline:
        msg = "; ".join("line %d: %s" % (n, r) for n, r in inline)
        raise ConfigError("inline secret value(s) detected in %s -> %s" % (path, msg))
    data = _yload(text)
    if not isinstance(data, dict):
        raise ConfigError("top-level YAML must be a mapping")
    cfg = Config(data, path)
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if not cfg.hosts:
        raise ConfigError("no hosts defined")
    src = (cfg.secrets.get("source") or "env")
    if src not in _VALID_SOURCES:
        raise ConfigError("secrets.source %r not in %s" % (src, _VALID_SOURCES))
    for i, h in enumerate(cfg.hosts):
        if not h.get("host"):
            raise ConfigError("hosts[%d] missing 'host'" % i)
        am = cfg.auth_mode(h)
        if am not in AUTH_MODES:
            raise ConfigError("hosts[%d] auth_mode %r not in %s" % (i, am, AUTH_MODES))
        if not h.get("remote_name"):
            raise ConfigError("hosts[%d] missing remote_name (set defaults.remote_name)" % i)
    # secret refs must be pointers, never values
    for k, v in cfg.secrets.items():
        if k.endswith("_ref") and v is not None:
            if not _POINTER_RE.match(str(v)):
                raise ConfigError("secrets.%s is not a pointer (looks like a literal): refuse" % k)


def resolve_refs(cfg: Config) -> dict:
    """Resolve secret *references* to a presence map WITHOUT reading any value.

    Returns {ref_name: {"source", "present": bool, "detail"}}. For source=env we test
    os.environ membership; for source=file we test absolute-path + existence; for backend
    sources we validate the pointer format. The secret VALUE is never read or returned.
    """
    src = cfg.secrets.get("source", "env")
    out = {}
    for k, v in cfg.secrets.items():
        if not k.endswith("_ref") or v is None:
            continue
        ref = str(v)
        entry = {"source": src, "present": False, "detail": ""}
        if src == "env":
            entry["present"] = ref in os.environ
            entry["detail"] = "env var %s" % ref
        elif src == "file":
            entry["present"] = os.path.isabs(ref) and os.path.exists(ref)
            entry["detail"] = "file %s" % ref
        else:  # op / vault / aws-ssm: validate pointer shape only
            entry["present"] = bool(re.match(r"^(op|vault|aws-ssm)://\S+$", ref))
            entry["detail"] = "%s pointer" % src
        out[k] = entry
    return out


# ---- anti-pattern guard (signal 8) -------------------------------------------------------

class AntiPatternError(RuntimeError):
    pass


def assert_no_shared_refresh_token(cfg: Config, rclone_conf_text: str = "") -> None:
    """Reject the cardinal Box multi-host failure mode.

    A rotating OAuth refresh_token must NEVER be deployed to more than one host (any host that
    refreshes invalidates the others). Server-auth modes (jwt/ccg-*) carry no refresh_token.
    In oauth-broker mode exactly one host (the master) may hold it.
    """
    has_rt = bool(rclone_conf_text) and "refresh_token" in rclone_conf_text
    if not has_rt:
        return
    n = len(cfg.hosts)
    modes = {cfg.auth_mode(h) for h in cfg.hosts}
    if n > 1 and modes != {"oauth-broker"}:
        raise AntiPatternError(
            "refresh_token present with %d hosts in mode(s) %s: rotating refresh tokens cannot "
            "be shared across hosts. Use server auth (jwt/ccg) or oauth-broker (single master)."
            % (n, sorted(modes))
        )
